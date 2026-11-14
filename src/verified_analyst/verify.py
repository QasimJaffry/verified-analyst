"""Numeric-claim verification.

Rule: every number in a free-text answer must be traceable to a value the agent's SQL
actually returned (or to a number the user wrote in the question). No LLM is involved:
the check is deterministic, so a failure is a fact, not an opinion.

Matching respects how humans round and format:
  * rounding: "12.35" matches a cell of 12.3456 (tolerance is half a unit of the digits shown)
  * percentages: "12.5%" matches a cell of 12.5 or 0.125
  * suffixes: "1.2k" / "3 million" match cells near 1200 / 3,000,000
  * formatting: "$1,234.50", "1 234" is NOT supported on purpose (ambiguous)

Deliberately NOT supported: arithmetic in prose (sums, ratios, differences). If the answer
needs a derived figure the agent must compute it in SQL. Letting the checker accept derived
values would make coincidental matches possible and weaken the guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_NUM = re.compile(
    r"""(?<![\w.])
        (?P<sign>[-−])?
        (?P<cur>[$€£])?\s?
        (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
        (?:\s?(?P<suffix>%|[kKmMbB]\b|thousand\b|million\b|billion\b))?
        (?!\w)""",
    re.VERBOSE,
)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


@dataclass(frozen=True)
class Claim:
    raw: str
    value: float          # numeric value after applying suffix multiplier (not percent scaling)
    decimals: int         # digits after the decimal point as displayed
    percent: bool
    multiplier: float


def extract_claims(text: str) -> list[Claim]:
    """Pull numeric claims out of free text, ignoring ISO dates and clock times."""
    cleaned = _TIME.sub(" ", _ISO_DATE.sub(" ", text))
    claims: list[Claim] = []
    for m in _NUM.finditer(cleaned):
        digits = m.group("num").replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        suffix = (m.group("suffix") or "").lower()
        mult = _MULT.get(suffix, 1.0)
        value = float(digits) * mult
        if m.group("sign"):
            value = -value
        claims.append(Claim(m.group(0).strip(), value, decimals, suffix == "%", mult))
    return claims


def _cells(value: object) -> list[float]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float, Decimal)):
        return [float(value)]
    if isinstance(value, str):
        return [c.value for c in extract_claims(value)]
    return []


class GroundingPool:
    """Every number the agent is allowed to quote."""

    def __init__(self, question: str = "") -> None:
        self._nums: list[float] = [c.value for c in extract_claims(question)]
        # numbers inside the question may be written as percents ("above 10%")
        self._nums += [c.value / 100 for c in extract_claims(question) if c.percent]

    def add_result(self, columns: list[str], rows: list[tuple]) -> None:
        self._nums.append(float(len(rows)))
        for row in rows:
            for v in row:
                self._nums.extend(_cells(v))

    def supports(self, claim: Claim) -> bool:
        tol = 0.5 * 10 ** (-claim.decimals) * claim.multiplier + 1e-9
        targets = [(claim.value, tol)]
        if claim.percent:
            targets.append((claim.value / 100, tol / 100))
        return any(abs(n - t) <= tt for n in self._nums for t, tt in targets)


@dataclass
class Verdict:
    ok: bool
    ungrounded: list[str]


def verify_answer(text: str, pool: GroundingPool) -> Verdict:
    bad = [c.raw for c in extract_claims(text) if not pool.supports(c)]
    return Verdict(ok=not bad, ungrounded=bad)
