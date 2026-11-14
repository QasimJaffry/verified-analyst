"""Wire-format tests. OpenAI-compatible: a real HTTP round trip to a local mock server.
Anthropic: a fake SDK client (no network, no key), asserting the exact request we send."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from verified_analyst.agent import TOOLS, run_agent
from verified_analyst.providers.anthropic_provider import AnthropicProvider, to_anthropic_messages
from verified_analyst.providers.openai_compat import OpenAICompatProvider, ProviderError, to_openai_messages

HISTORY = [
    {"role": "user", "content": "How many users?"},
    {"role": "assistant", "content": "checking", "tool_calls": [
        {"id": "t1", "name": "run_sql", "args": {"query": "SELECT 1"}},
        {"id": "t2", "name": "describe_schema", "args": {}}]},
    {"role": "tool", "tool_call_id": "t1", "name": "run_sql", "content": "1 row(s)"},
    {"role": "tool", "tool_call_id": "t2", "name": "describe_schema", "content": "TABLE users"},
]


def test_anthropic_groups_tool_results_into_one_user_message():
    out = to_anthropic_messages(HISTORY)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["t1", "t2"]


def test_openai_message_conversion():
    out = to_openai_messages("SYS", HISTORY)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[2]["tool_calls"][0]["function"] == {"name": "run_sql", "arguments": json.dumps({"query": "SELECT 1"})}
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "t1"


class FakeAnthropic:
    def __init__(self):
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        block = SimpleNamespace(type="tool_use", id="tu1", name="run_sql", input={"query": "SELECT COUNT(*) FROM users"})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="plan"), block],
                               usage=SimpleNamespace(input_tokens=111, output_tokens=22))


def test_anthropic_request_and_response_shape():
    fake = FakeAnthropic()
    p = AnthropicProvider(model="claude-test", client=fake)
    turn = p.complete("SYS", [{"role": "user", "content": "q"}], TOOLS)
    req = fake.requests[0]
    assert req["model"] == "claude-test" and req["system"] == "SYS" and req["max_tokens"] > 0
    assert {t["name"] for t in req["tools"]} == {"describe_schema", "run_sql", "final_answer"}
    assert all("input_schema" in t and "parameters" not in t for t in req["tools"])
    assert turn.text == "plan" and turn.tool_calls[0].args == {"query": "SELECT COUNT(*) FROM users"}
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (111, 22)


@pytest.fixture()
def mock_server():
    """Scripted OpenAI-style endpoint: first call -> run_sql, second -> final_answer."""
    state = {"calls": 0, "bodies": [], "auth": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["bodies"].append(body)
            state["auth"].append(self.headers.get("Authorization"))
            state["calls"] += 1
            if "/boom" in self.path:
                self.send_response(500); self.end_headers(); self.wfile.write(b"nope"); return
            if state["calls"] == 1:
                msg = {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "run_sql", "arguments": json.dumps({"query": "SELECT COUNT(*) FROM users"})}}]}
            elif state["calls"] == 2:
                msg = {"content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {
                    "name": "final_answer", "arguments": json.dumps(
                        {"status": "answered", "answer": "3000 users", "value": 3000})}}]}
            else:
                msg = {"content": "garbled", "tool_calls": [{"id": "c3", "type": "function", "function": {
                    "name": "run_sql", "arguments": "{not json"}}]}
            data = json.dumps({"choices": [{"message": msg}], "usage": {"prompt_tokens": 50, "completion_tokens": 7}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", state
    srv.shutdown()


def test_openai_compat_end_to_end_through_the_agent(db, mock_server, monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    url, state = mock_server
    p = OpenAICompatProvider(model="m", base_url=url, api_key="k123")
    r = run_agent("How many users?", db, p)
    assert r.verified and r.value == 3000 and r.steps == 2
    assert (r.usage.input_tokens, r.usage.output_tokens) == (100, 14)
    assert state["auth"][0] == "Bearer k123"
    b0 = state["bodies"][0]
    assert b0["temperature"] == 0 and b0["tools"][0]["type"] == "function" and b0["messages"][0]["role"] == "system"
    # second request must carry the assistant tool call and its tool result back
    roles = [m["role"] for m in state["bodies"][1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


def test_openai_compat_unparseable_arguments_are_flagged_not_crashed(mock_server, monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    url, state = mock_server
    state["calls"] = 2   # next response is the garbled one
    t = OpenAICompatProvider(model="m", base_url=url).complete("s", [{"role": "user", "content": "q"}], TOOLS)
    assert "_unparsed_arguments" in t.tool_calls[0].args


def test_openai_compat_http_error_surfaces_as_provider_error(mock_server, monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    url, _ = mock_server
    with pytest.raises(ProviderError, match="HTTP 500"):
        OpenAICompatProvider(model="m", base_url=url + "/boom").complete("s", [{"role": "user", "content": "q"}], TOOLS)


def test_openai_compat_requires_a_model(monkeypatch):
    monkeypatch.delenv("VA_MODEL", raising=False)
    with pytest.raises(ProviderError):
        OpenAICompatProvider()
