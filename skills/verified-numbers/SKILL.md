---
name: verified-numbers
description: Use when answering questions about data in CSV files with specific figures (counts, totals, averages, rates, rankings). Computes every figure in SQL and checks the final answer so that no number is stated unless a query returned it.
---

# Verified numbers

When you report figures from CSV data, each number in your answer must be a value that a SQL query returned. Do not estimate, round from memory, or do arithmetic in prose. This skill uses the `va` command from [verified-analyst](https://github.com/QasimJaffry/verified-analyst) to run read-only SQL and to check your draft.

## Setup (once)

Check that the tool is installed:

```bash
va --help
```

If it is missing, ask the user before installing, then run:

```bash
pip install "verified-analyst @ git+https://github.com/QasimJaffry/verified-analyst"
```

It needs Python 3.10 or newer. Every `.csv` file in the data folder becomes a table named after the file.

## Workflow

1. **Read the schema.** Do not guess table or column names.

   ```bash
   va schema --data <folder-with-csv-files>
   ```

2. **Write the SQL that computes each figure you plan to state.** Do sums, differences, ratios and percentages in SQL, so the result contains the exact number you will write. Only single `SELECT` statements are accepted (DuckDB syntax).

3. **Draft the answer, then check it** with the same queries. Repeat `--sql` once per query.

   ```bash
   va check --data <folder> \
     --sql "SELECT platform, COUNT(*) AS users FROM users GROUP BY platform" \
     --sql "SELECT COUNT(*) AS total FROM users" \
     --question "<the user's question>" \
     --answer "<your draft answer>"
   ```

   Pass `--answer -` to read a long draft from standard input.

4. **Act on the result.** The command prints JSON and sets an exit code.

   | exit code | meaning | what to do |
   |---|---|---|
   | 0 | every number is grounded | send the answer |
   | 1 | `ungrounded` lists numbers no query returned | add a query that computes each one, or remove it, then check again |
   | 2 | a query was rejected or failed | fix the SQL using the `error` text, then check again |

5. **Show your work.** Give the user the answer and the queries you ran.

## Rules

- Never send an answer with numbers that has not passed `va check`.
- If the data cannot answer the question (a missing column, a time range the data does not cover), say so. Do not produce a number.
- If the question is too vague to compute (for example "how are we doing?"), ask what to measure.
- Rounding is allowed to the digits you show: a result of `4.4567` grounds "4.5" and "4.46", and `0.408` grounds "40.8%". It does not ground "4.47".
- Dates and clock times are not treated as numeric claims.

## Limits

The check proves that each number came from a query result. It does not prove the query measured the right thing. Reread your SQL against the question before you answer.
