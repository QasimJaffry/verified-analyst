"""Oracle scoring: the expected answer is the result of executing the task's gold SQL.

No LLM judge. A run passes only if BOTH hold:
  correct   - status and machine-readable `value` match the gold result (or the expected refusal)
  grounded  - every number in the prose was returned by one of the agent's own queries
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..agent import AgentResult
from ..db import QueryResult
from .tasks import Task


@dataclass
class Score:
    correct: bool
    grounded: bool
    reason: str

    @property
    def passed(self) -> bool:
        return self.correct and self.grounded


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "").rstrip("%").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _norm_cell(v: Any, tol: float) -> str:
    n = _num(v)
    if n is not None:
        # snap to a grid of 2*tol so a gold value rounded by SQL and the agent's value agree
        step = max(2 * tol, 1e-9)
        return f"n:{round(n / step)}"
    return "s:" + str(v).strip().lower()


def _rows(value: Any) -> list[list[Any]] | None:
    if not isinstance(value, list):
        return None
    rows: list[list[Any]] = []
    for r in value:
        if isinstance(r, dict):
            rows.append(list(r.values()))
        elif isinstance(r, (list, tuple)):
            rows.append(list(r))
        else:
            rows.append([r])
    return rows


def _unwrap_scalar(value: Any) -> Any:
    # tolerate [x], [[x]], [{"k": x}] for a single value
    rows = _rows(value)
    if rows is not None and len(rows) == 1 and len(rows[0]) == 1:
        return rows[0][0]
    return value


def score(task: Task, result: AgentResult, gold: QueryResult | None) -> Score:
    grounded = result.verified and not result.ungrounded

    if task.expected_status != "answered":
        ok = result.status == task.expected_status
        why = "expected refusal/clarification given" if ok else \
            f"expected status {task.expected_status!r}, got {result.status!r}"
        return Score(ok, grounded if result.status == "answered" else True, why)

    if result.status != "answered":
        return Score(False, grounded, f"expected an answer, got status {result.status!r}")
    if gold is None:
        raise ValueError(f"task {task.id}: gold result required")

    if task.answer_type == "scalar":
        want = _num(gold.rows[0][0])
        got = _num(_unwrap_scalar(result.value))
        if got is None or want is None:
            return Score(False, grounded, f"value {result.value!r} is not numeric")
        ok = abs(got - want) <= task.tolerance + 1e-12
        return Score(ok, grounded, "match" if ok else f"want {want}, got {got}")

    if task.answer_type == "label":
        want = str(gold.rows[0][0]).strip().lower()
        got = _unwrap_scalar(result.value)
        ok = got is not None and str(got).strip().lower() == want
        return Score(ok, grounded, "match" if ok else f"want {want!r}, got {got!r}")

    if task.answer_type == "table":
        got_rows = _rows(result.value)
        if got_rows is None:
            return Score(False, grounded, "value must be a list of rows")
        key = lambda r: tuple(sorted(_norm_cell(c, task.tolerance) for c in r))  # noqa: E731
        want_set = sorted(key(list(r)) for r in gold.rows)
        got_set = sorted(key(r) for r in got_rows)
        ok = want_set == got_set
        return Score(ok, grounded, "match" if ok else f"table differs (want {len(want_set)} rows, got {len(got_set)})")

    raise ValueError(f"task {task.id}: bad answer_type {task.answer_type!r}")
