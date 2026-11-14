"""Reference solvers that need no model. They exist to validate the HARNESS, not to measure a model.

  oracle          runs the gold SQL and reports it faithfully         -> must score 100%
  wrong_value     right prose, wrong machine-readable `value`         -> scorer must fail it
  tampered_prose  prose number nudged away from what the SQL returned -> verifier must fail it
  hallucinate     never queries, answers with an invented number      -> verifier must fail it
  overconfident   answers every unanswerable/vague task with a number -> scorer must fail it
  refuse_all      declines everything                                 -> must fail all answerable tasks
  self_correct    first draft has a nudged number; after the guard rejects it, reports faithfully
                  -> must pass (this is what "saved by the guard" looks like)

If a mutant ever passes an answerable task, the scorer or verifier has a hole.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..db import QueryResult
from ..providers.base import ToolCall, Turn
from .tasks import Task

MODES = ("oracle", "wrong_value", "tampered_prose", "hallucinate", "overconfident", "refuse_all", "self_correct")


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _fmt(v: Any) -> str:
    v = _plain(v)
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


class ReferenceSolver:
    def __init__(self, task: Task, gold: QueryResult | None, mode: str = "oracle") -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.task, self.gold, self.mode = task, gold, mode
        self.name = f"reference:{mode}"

    # -- helpers ---------------------------------------------------------------
    def _value(self) -> Any:
        g, t = self.gold, self.task
        assert g is not None
        if t.answer_type in ("scalar", "label"):
            return _plain(g.rows[0][0])
        return [[_plain(c) for c in r] for r in g.rows]

    def _prose(self, value: Any) -> str:
        if isinstance(value, list):
            return "; ".join(", ".join(_fmt(c) for c in r) for r in value)
        return _fmt(value)

    def _final(self, status: str, answer: str, value: Any = None) -> Turn:
        args: dict[str, Any] = {"status": status, "answer": answer}
        if value is not None:
            args["value"] = value
        return Turn(None, [ToolCall("final", "final_answer", args)])

    # -- policy ----------------------------------------------------------------
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        t, mode = self.task, self.mode
        answerable = t.expected_status == "answered"
        queried = any(m["role"] == "tool" and m["name"] == "run_sql" and not m["content"].startswith("ERROR")
                      for m in messages)

        if mode == "refuse_all":
            return self._final("cannot_answer", "I cannot answer this.")
        if mode == "hallucinate":
            return self._final("answered", "The answer is 4242.", 4242)
        if mode == "overconfident" and not answerable:
            return self._final("answered", "The answer is 42.", 42)
        if not answerable:  # oracle / other mutants behave: refuse correctly
            return self._final(t.expected_status, "The data needed for this is not available or the "
                                                  "question is too vague to answer.")
        if not queried:
            return Turn(None, [ToolCall("q1", "run_sql", {"query": t.gold_sql})])

        value = self._value()
        rejected = any(m["role"] == "tool" and m["content"].startswith("REJECTED") for m in messages)
        if mode == "self_correct":
            mode = "oracle" if rejected else "tampered_prose"
        if mode == "wrong_value":
            wrong = ([] if isinstance(value, list) else (f"{value}_x" if isinstance(value, str) else value + 1))
            return self._final("answered", self._prose(value), wrong)
        if mode == "tampered_prose":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return self._final("answered", self._prose(value + 7), value)
            if isinstance(value, list) and value and isinstance(value[0][-1], (int, float)):
                bumped = [r[:-1] + [r[-1] + 7] for r in value]
                return self._final("answered", self._prose(bumped), value)
            return self._final("answered", f"{self._prose(value)} (about 31 rows)", value)
        return self._final("answered", self._prose(value), value)  # oracle & overconfident-on-answerable


def reference_factory(mode: str):
    def make(task: Task, gold: QueryResult | None) -> ReferenceSolver:
        return ReferenceSolver(task, gold, mode)
    return make
