from __future__ import annotations

import pytest

from verified_analyst.datagen import generate
from verified_analyst.db import Database
from verified_analyst.providers.base import ToolCall, Turn, Usage


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("data"), seed=42, n_users=3000)


@pytest.fixture()
def db(data_dir):
    return Database(data_dir)


class Scripted:
    """Plays back a fixed list of turns; records what the agent sent."""

    name = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def complete(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            raise AssertionError("agent asked for more turns than scripted")
        return self.turns.pop(0)


def call(name, id="c", **args):
    return Turn(None, [ToolCall(id, name, args)], Usage(10, 5))


def final(status="answered", answer="ok", id="f", **extra):
    return call("final_answer", id, status=status, answer=answer, **extra)
