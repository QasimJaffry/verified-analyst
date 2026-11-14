"""The whole idea in a few lines: an answer with an invented number is caught, a grounded one passes.

    va data --out ~/va-demo && python examples/catch_a_lie.py ~/va-demo
"""
import sys

from verified_analyst.db import Database
from verified_analyst.mcp_server import AnalystSession

session = AnalystSession(Database(sys.argv[1] if len(sys.argv) > 1 else "data"))
question = "How many users signed up from each platform?"
sql = "SELECT platform, COUNT(*) AS users FROM users GROUP BY platform ORDER BY users DESC"

print(f"Q: {question}\n")
print("assistant runs SQL:")
print(f"  {sql}")
print("  ->", session.run_sql(sql).replace("\n", "\n     "), "\n")

drafts = [
    ("draft 1 (numbers from memory)", "iOS leads with 4,812 users, followed by Android with 3,100."),
    ("draft 2 (numbers from the query)", None),
]
first = drafts[0]
print(f'{first[0]}: "{first[1]}"')
v = session.check_answer(first[1], question)
print(f"  check_answer -> grounded={v['grounded']}  ungrounded={v['ungrounded']}")
print("  REJECTED: those figures were never returned by a query.\n")

# a second draft built only from what the query returned
rows = session.db.run_select(sql).rows
good = f"{rows[0][0]} leads with {rows[0][1]:,} users, followed by {rows[1][0]} with {rows[1][1]:,}."
print(f'{drafts[1][0]}: "{good}"')
v = session.check_answer(good, question)
print(f"  check_answer -> grounded={v['grounded']}  ungrounded={v['ungrounded']}")
print("  ACCEPTED." if v["grounded"] else "  (rejected)")
