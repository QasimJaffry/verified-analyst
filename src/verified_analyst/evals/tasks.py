from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = ("lookup", "aggregate", "join", "time", "unanswerable", "ambiguous")
ANSWER_TYPES = ("scalar", "label", "table", "none")


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    question: str
    expected_status: str = "answered"      # answered | cannot_answer | needs_clarification
    answer_type: str = "scalar"            # scalar | label | table | none
    gold_sql: str | None = None
    tolerance: float = 0.005               # absolute, for scalars and table floats


def load_tasks(path: str | Path) -> list[Task]:
    tasks: list[Task] = []
    for ln, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        d = json.loads(line)
        t = Task(**d)
        if t.category not in CATEGORIES:
            raise ValueError(f"{path}:{ln} bad category {t.category!r}")
        if t.expected_status == "answered" and not t.gold_sql:
            raise ValueError(f"{path}:{ln} answered task {t.id} needs gold_sql")
        tasks.append(t)
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids")
    return tasks


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
