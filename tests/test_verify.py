import pytest

from verified_analyst.verify import GroundingPool, extract_claims, verify_answer


def pool(rows, question=""):
    p = GroundingPool(question)
    p.add_result(["c"], [tuple(r) if isinstance(r, (list, tuple)) else (r,) for r in rows])
    return p


@pytest.mark.parametrize("text,values", [
    ("Revenue was $41,581.04.", [41581.04]),
    ("46.1% are on iOS", [46.1]),
    ("about 1.2k users", [1200.0]),
    ("3 million downloads", [3_000_000.0]),
    ("1,616 Android users and 1,384 iOS users", [1616.0, 1384.0]),
    ("signed up in 2025-03-14 at 10:45", []),          # dates and times are not claims
    ("in 2025-10, 95 subscriptions ended", [95.0]),
    ("Q3 had user_123 on tier3", []),                    # identifiers glued to letters
    ("ranges 10-20", [10.0, 20.0]),                      # a dash is not a minus sign here
    ("it fell -4 points", [-4.0]),
])
def test_extract_claims(text, values):
    assert [c.value for c in extract_claims(text)] == values


def test_exact_value_grounded():
    assert verify_answer("There are 1616 Android users.", pool([1616])).ok


def test_rounding_to_displayed_precision_is_grounded():
    assert verify_answer("Average is 4.46", pool([4.4567])).ok
    assert verify_answer("Average is 4.5", pool([4.4567])).ok
    assert not verify_answer("Average is 4.47", pool([4.4567])).ok     # wrong in the second decimal


def test_percent_matches_percent_or_fraction():
    assert verify_answer("40.8%", pool([40.8])).ok
    assert verify_answer("40.8%", pool([0.408])).ok
    assert not verify_answer("40.8%", pool([0.5])).ok


def test_thousands_and_suffix_forms():
    assert verify_answer("$41,581.04 in revenue", pool([41581.04])).ok
    assert verify_answer("about 41.6k", pool([41581.04])).ok
    assert not verify_answer("about 45k", pool([41581.04])).ok


def test_invented_number_is_flagged_with_the_offender_named():
    v = verify_answer("There are 1616 Android users and 9999 iOS users.", pool([1616]))
    assert not v.ok and v.ungrounded == ["9999"]


def test_numbers_from_the_question_are_allowed():
    assert verify_answer("In 2025 there were 1491 signups.", pool([1491], question="signups in 2025?")).ok


def test_arithmetic_in_prose_is_rejected_by_design():
    # 1616 + 1384 = 3000 but 3000 was never returned: the agent must SELECT it.
    assert not verify_answer("In total there are 3000 users.", pool([1616, 1384])).ok


def test_row_count_is_a_valid_grounding_value():
    p = pool([("ios", 1), ("android", 2)])
    assert verify_answer("There are 2 platforms.", p).ok


def test_no_queries_means_nothing_is_grounded():
    assert not verify_answer("The answer is 42.", GroundingPool("")).ok


def test_answer_without_numbers_passes():
    assert verify_answer("Organic is the biggest channel.", GroundingPool("")).ok


def test_string_cells_contribute_their_digits():
    p = GroundingPool("")
    p.add_result(["month"], [("Q3 2025",)])
    assert verify_answer("Peak in 2025.", p).ok
