from unittest.mock import Mock

import pytest

from pipeline.creators import llm


def test_responses_request_parses_text_and_usage(monkeypatch):
    provider = llm.OpenAIProvider("private-key", model="gpt-5.6-sol", wire_api="responses")
    response = Mock(status_code=200)
    response.json.return_value = {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "article"}]},
    ], "usage": {"input_tokens": 23, "output_tokens": 42}}
    post = Mock(return_value=response)
    monkeypatch.setattr("httpx.post", post)
    result = provider.call("write", "gpt-5.6-sol", 6000)
    assert post.call_args.args[0].endswith("/responses")
    payload = post.call_args.kwargs["json"]
    assert payload["input"] == "write" and payload["max_output_tokens"] == 6000
    assert "max_tokens" not in payload
    assert result.text == "article" and result.output_tokens == 42


@pytest.mark.parametrize("body", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
])
def test_responses_empty_or_incomplete_is_not_a_draft(monkeypatch, body):
    response = Mock(status_code=200)
    response.json.return_value = body
    monkeypatch.setattr("httpx.post", Mock(return_value=response))
    provider = llm.OpenAIProvider("key", wire_api="responses")
    with pytest.raises(ValueError):
        provider.call("write", "gpt-4o", 100)


def test_audit_uses_model_actually_sent(monkeypatch, tmp_path):
    from pipeline import db
    provider = llm.OpenAIProvider("key", model="gpt-4o")
    monkeypatch.setattr(llm, "_PROVIDER", provider)
    monkeypatch.setattr(llm, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(llm, "_TIER_MAP", {"creative": "claude-sonnet-5"})
    monkeypatch.setattr(provider, "call", lambda prompt, model, max_tokens: llm.CompletionResult("text", 10, 20))
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    try:
        llm.complete("write", stage="project_master_draft", conn=conn)
        assert conn.execute("SELECT model FROM llm_calls").fetchone()[0] == "gpt-4o"
    finally:
        conn.close()
