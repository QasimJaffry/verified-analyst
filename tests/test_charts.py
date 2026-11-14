import datetime as dt

import pytest

from verified_analyst.charts import ChartError, build_vega_lite
from verified_analyst.db import QueryResult


def qr(rows, cols=("k", "v")):
    return QueryResult(list(cols), rows)


def test_bar_chart_spec_uses_only_returned_rows():
    spec = build_vega_lite(qr([("ios", 10), ("android", 12)]), {"type": "bar", "x": "k", "y": "v"})
    assert spec["data"]["values"] == [{"k": "ios", "v": 10}, {"k": "android", "v": 12}]
    assert spec["mark"] == "bar" and spec["encoding"]["x"]["type"] == "ordinal"


def test_dates_become_temporal():
    spec = build_vega_lite(qr([(dt.date(2025, 1, 1), 1), (dt.date(2025, 2, 1), 2)]), {"type": "line", "x": "k", "y": "v"})
    assert spec["encoding"]["x"]["type"] == "temporal"
    assert spec["data"]["values"][0]["k"] == "2025-01-01"


@pytest.mark.parametrize("chart", [
    {"type": "pie", "x": "k", "y": "v"},
    {"type": "bar", "x": "nope", "y": "v"},
    {"type": "bar", "x": "k", "y": "k"},   # non-numeric y
])
def test_bad_charts_rejected(chart):
    with pytest.raises(ChartError):
        build_vega_lite(qr([("a", 1)]), chart)


def test_empty_result_rejected():
    with pytest.raises(ChartError):
        build_vega_lite(qr([]), {"type": "bar", "x": "k", "y": "v"})
