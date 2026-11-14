"""The analyst loop: plan -> query -> (verify) -> answer, with a hard numeric-grounding gate.

Flow
  question -> model <-> tools {describe_schema, run_sql}  -> final_answer
  final_answer is checked by verify.py; if the prose contains a number no query returned,
  the call is bounced back to the model ("may re-analyze") up to `max_verify_retries` times.
  If it still fails, the result is returned with verified=False and the offending numbers listed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .charts import ChartError, build_vega_lite
from .db import Database, QueryResult, SQLError, SQLRejected
from .providers.base import Provider, Usage
from .verify import GroundingPool, verify_answer

STATUSES = ("answered", "cannot_answer", "needs_clarification")

SYSTEM_PROMPT = """You are a careful data analyst. You answer questions about a SQL database by \
running queries, never from memory or guesswork.

Rules:
1. Call describe_schema first unless you already know the schema.
2. Compute EVERY number you will report inside SQL (counts, sums, rates, rounding). Do not do \
arithmetic in your head or in prose. Your final answer may only contain numbers that appear in \
query results or in the user's question; anything else is rejected and you must redo the analysis.
3. run_sql accepts one read-only SELECT at a time. If a query errors, fix it and retry.
4. Finish by calling final_answer exactly once:
   - status "answered": `answer` is a short plain-language reply; `value` is the machine-readable \
result (a number for a single figure, a string for a single label, or a list of rows for a table).
   - status "cannot_answer": the data needed does not exist in the schema. Say what is missing. \
Do NOT substitute a different metric and present it as the requested one.
   - status "needs_clarification": the question is too vague to answer; say what you need to know.
5. State assumptions (e.g. which rows you excluded) in `answer`. Be concise."""

TOOLS: list[dict] = [
    {"name": "describe_schema",
     "description": "List all tables with columns, types, row counts, allowed values of small "
                    "categorical columns, and sample rows.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "run_sql",
     "description": "Run ONE read-only DuckDB SELECT query and return up to 100 rows.",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
                    "required": ["query"]}},
    {"name": "final_answer",
     "description": "Submit the final answer. Call exactly once when finished.",
     "parameters": {
         "type": "object",
         "properties": {
             "status": {"type": "string", "enum": list(STATUSES)},
             "answer": {"type": "string", "description": "Short plain-language answer."},
             "value": {"description": "Machine-readable result: number, string, or list of rows. "
                                      "Omit unless status is answered."},
             "chart": {"type": "object",
                       "description": "Optional chart of the LAST successful query.",
                       "properties": {"type": {"type": "string", "enum": ["bar", "line", "point"]},
                                      "x": {"type": "string"}, "y": {"type": "string"}}},
         },
         "required": ["status", "answer"]}},
]


@dataclass
class AgentResult:
    status: str = "no_answer"        # answered | cannot_answer | needs_clarification | no_answer | error
    answer: str = ""
    value: Any = None
    verified: bool = False
    ungrounded: list[str] = field(default_factory=list)
    # What the model did BEFORE the guard intervened: the numbers in its first "answered" draft
    # that no query returned. This is the raw "invented a number" signal the leaderboard reports;
    # `ungrounded` above is what survived the guard.
    first_draft_ungrounded: list[str] = field(default_factory=list)
    first_status: str | None = None  # status of the first final_answer the model submitted
    rejections: int = 0              # times the guard bounced an answer back
    chart: dict | None = None
    steps: int = 0
    sql: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    error: str | None = None


def run_agent(question: str, db: Database, provider: Provider, *, max_steps: int = 10,
              max_verify_retries: int = 2) -> AgentResult:
    t0 = time.perf_counter()
    res = AgentResult()
    pool = GroundingPool(question)
    last_ok: QueryResult | None = None
    verify_failures = 0
    nudged = False
    messages: list[dict] = [{"role": "user", "content": question}]

    def tool_msg(call, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}

    for _ in range(max_steps):
        res.steps += 1
        try:
            turn = provider.complete(SYSTEM_PROMPT, messages, TOOLS)
        except Exception as e:  # provider/network failure is a result, not a crash
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
            break
        res.usage.input_tokens += turn.usage.input_tokens
        res.usage.output_tokens += turn.usage.output_tokens
        messages.append({"role": "assistant", "content": turn.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls]})

        if not turn.tool_calls:
            if nudged:
                res.status, res.answer = "no_answer", turn.text or ""
                break
            nudged = True
            messages.append({"role": "user", "content": "Reply by calling a tool. Finish with final_answer."})
            continue

        finished = False
        for call in sorted(turn.tool_calls, key=lambda c: c.name == "final_answer"):
            if "_unparsed_arguments" in call.args:
                messages.append(tool_msg(call, "ERROR: tool arguments were not valid JSON; resend them."))
                continue

            if call.name == "describe_schema":
                messages.append(tool_msg(call, db.describe()))

            elif call.name == "run_sql":
                sql = str(call.args.get("query", ""))
                res.sql.append(sql)
                try:
                    qr = db.run_select(sql)
                except (SQLError, SQLRejected) as e:
                    messages.append(tool_msg(call, f"ERROR: {e}"))
                    continue
                last_ok = qr
                pool.add_result(qr.columns, qr.rows)
                messages.append(tool_msg(call, qr.to_text()))

            elif call.name == "final_answer":
                status = call.args.get("status")
                answer = call.args.get("answer")
                if status not in STATUSES or not isinstance(answer, str) or not answer.strip():
                    messages.append(tool_msg(call, f"ERROR: status must be one of {list(STATUSES)} "
                                                   "and answer must be a non-empty string."))
                    continue
                chart_spec = None
                if call.args.get("chart"):
                    try:
                        if last_ok is None:
                            raise ChartError("no successful query to chart yet")
                        chart_spec = build_vega_lite(last_ok, call.args["chart"])
                    except ChartError as e:
                        messages.append(tool_msg(call, f"ERROR: {e}. Resend final_answer without or with a valid chart."))
                        continue
                verdict = verify_answer(answer, pool) if status == "answered" else None
                if res.first_status is None:
                    res.first_status = status
                    if verdict is not None and not verdict.ok:
                        res.first_draft_ungrounded = list(verdict.ungrounded)
                if verdict and not verdict.ok:
                    verify_failures += 1
                    res.rejections += 1
                    res.ungrounded = verdict.ungrounded
                    if verify_failures <= max_verify_retries:
                        messages.append(tool_msg(
                            call,
                            f"REJECTED: these numbers in your answer are not in any query result: "
                            f"{verdict.ungrounded}. Compute them with run_sql, or remove them, then "
                            "call final_answer again."))
                        continue
                res.status, res.answer = status, answer
                res.value = call.args.get("value")
                res.chart = chart_spec
                res.verified = True if verdict is None else verdict.ok
                if verdict is not None and verdict.ok:
                    res.ungrounded = []
                finished = True
                break
            else:
                messages.append(tool_msg(call, f"ERROR: unknown tool {call.name!r}"))
        if finished:
            break
    else:
        res.status = res.status if res.status == "error" else "no_answer"

    res.latency_s = time.perf_counter() - t0
    return res
