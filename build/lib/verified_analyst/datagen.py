"""Deterministic synthetic dataset: a subscription mobile app.

Three tables (users, subscriptions, payments). The data is SYNTHETIC: it exists so
the agent and the eval have a realistic, reproducible schema with the traps real
subscription data has (trials that never convert, failed renewals, refunds,
platform differences). Nothing here describes a real app.

Snapshot date is 2025-12-31: nothing happens after it.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT = date(2025, 12, 31)
TRIAL_DAYS = 7
PRICES = {"monthly": 9.99, "annual": 59.99}

_COUNTRIES = [("US", 30), ("GB", 10), ("DE", 8), ("BR", 10), ("IN", 14),
              ("PK", 6), ("CA", 7), ("AU", 5), ("FR", 5), ("JP", 5)]
_CHANNELS = [("organic", 45), ("paid_social", 25), ("search_ads", 15), ("referral", 15)]


def _pick(rng: random.Random, weighted: list[tuple[str, int]]) -> str:
    return rng.choices([v for v, _ in weighted], [w for _, w in weighted])[0]


def generate(out_dir: str | Path, seed: int = 42, n_users: int = 3000) -> Path:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    users, subs, pays = [], [], []
    sub_id = 0
    pay_id = 0

    for uid in range(1, n_users + 1):
        signup = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 731))
        platform = rng.choices(["ios", "android"], [45, 55])[0]
        channel = _pick(rng, _CHANNELS)
        users.append([uid, signup.isoformat(), platform, _pick(rng, _COUNTRIES), channel])

        if rng.random() >= 0.6:
            continue  # never started a trial

        n_subs = 2 if rng.random() < 0.05 else 1
        start = signup + timedelta(days=rng.randrange(0, 8))
        for _ in range(n_subs):
            if start > SNAPSHOT:
                break
            sub_id += 1
            plan = "annual" if rng.random() < 0.3 else "monthly"
            conv_p = (0.40 if platform == "ios" else 0.33) + (0.08 if channel == "referral" else 0)
            trial_end = start + timedelta(days=TRIAL_DAYS)
            status, ended_on, reason, converted = "trialing", "", "", False

            if trial_end > SNAPSHOT:
                converted = False  # trial still running at the snapshot
            elif rng.random() >= conv_p:
                status, ended_on, reason = "cancelled", trial_end.isoformat(), "user_cancelled"
            else:
                converted, status = True, "active"
                fail_p = 0.03 if platform == "ios" else 0.06
                paid_on = trial_end
                step = 30 if plan == "monthly" else 365
                renew_p = 0.92 if plan == "monthly" else 0.55
                while paid_on <= SNAPSHOT:
                    pay_id += 1
                    if rng.random() < fail_p:
                        pays.append([pay_id, sub_id, paid_on.isoformat(), PRICES[plan], "failed"])
                        status, ended_on, reason = "cancelled", paid_on.isoformat(), "billing_error"
                        break
                    refunded = rng.random() < 0.02
                    pays.append([pay_id, sub_id, paid_on.isoformat(), PRICES[plan],
                                 "refunded" if refunded else "succeeded"])
                    if refunded and rng.random() < 0.5:
                        status, ended_on, reason = "cancelled", paid_on.isoformat(), "refund"
                        break
                    if rng.random() >= renew_p:
                        end = paid_on + timedelta(days=rng.randrange(1, step))
                        if end <= SNAPSHOT:
                            status, ended_on, reason = "cancelled", end.isoformat(), "user_cancelled"
                        break
                    paid_on += timedelta(days=step)

            subs.append([sub_id, uid, plan, PRICES[plan], start.isoformat(),
                         "true" if converted else "false", status, ended_on, reason])
            start = start + timedelta(days=rng.randrange(60, 200))

    _write(out / "users.csv", ["user_id", "signup_date", "platform", "country", "channel"], users)
    _write(out / "subscriptions.csv",
           ["subscription_id", "user_id", "plan", "price_usd", "trial_started_on",
            "converted", "status", "ended_on", "end_reason"], subs)
    _write(out / "payments.csv",
           ["payment_id", "subscription_id", "paid_on", "amount_usd", "status"], pays)
    return out


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


if __name__ == "__main__":  # pragma: no cover
    import sys
    generate(sys.argv[1] if len(sys.argv) > 1 else "data")
