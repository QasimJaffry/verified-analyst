"""Vega-Lite chart specs built ONLY from rows a verified query returned.

The model chooses a chart type and two columns; it never supplies data points, so a chart
cannot show a number the SQL did not return. No plotting dependency: the output is a plain
Vega-Lite v5 spec (render in vega-embed, the Vega editor, or Observable).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .db import QueryResult

CHART_TYPES = {"bar": "bar", "line": "line", "point": "point"}


class ChartError(ValueError):
    pass


def _json_safe(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def build_vega_lite(result: QueryResult, chart: dict, title: str = "") -> dict:
    ctype = chart.get("type")
    x, y = chart.get("x"), chart.get("y")
    if ctype not in CHART_TYPES:
        raise ChartError(f"chart.type must be one of {sorted(CHART_TYPES)}")
    for label, col in (("x", x), ("y", y)):
        if col not in result.columns:
            raise ChartError(f"chart.{label}={col!r} is not a column of the last query ({result.columns})")
    xi, yi = result.columns.index(x), result.columns.index(y)
    if not result.rows:
        raise ChartError("the last query returned no rows")
    if not all(isinstance(r[yi], (int, float, Decimal)) and not isinstance(r[yi], bool)
               for r in result.rows if r[yi] is not None):
        raise ChartError(f"chart.y={y!r} must be numeric")
    x_is_time = isinstance(result.rows[0][xi], (date, datetime))
    x_is_num = isinstance(result.rows[0][xi], (int, float, Decimal)) and not isinstance(result.rows[0][xi], bool)
    x_type = "temporal" if x_is_time else ("quantitative" if x_is_num and ctype != "bar" else "ordinal")
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title or f"{y} by {x}",
        "data": {"values": [{x: _json_safe(r[xi]), y: _json_safe(r[yi])} for r in result.rows]},
        "mark": CHART_TYPES[ctype],
        "encoding": {"x": {"field": x, "type": x_type}, "y": {"field": y, "type": "quantitative"}},
    }
