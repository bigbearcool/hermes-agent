"""An old prompt must never approve the next queued command."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_prompt_callback_binds_message_and_expires_before_inline_success():
    adapter = FeishuAdapter(PlatformConfig())
    state = {"session_key": "session", "chat_id": "chat", "message_id": "expected", "created_at": 0}
    wrong = SimpleNamespace(operator=SimpleNamespace(open_id="ou_user"), context=SimpleNamespace(
        open_chat_id="chat", open_message_id="other"))
    assert adapter._validate_card_action(event=wrong, state=state, label="approval", ident=1) is None
    expired = SimpleNamespace(operator=SimpleNamespace(open_id="ou_user"), context=SimpleNamespace(
        open_chat_id="chat", open_message_id="expected"))
    assert adapter._validate_card_action(event=expired, state=state, label="approval", ident=1) is None


def test_approval_resolution_targets_original_request(monkeypatch):
    import tools.approval as approvals
    calls = []
    monkeypatch.setattr(approvals, 'resolve_gateway_approval', lambda *a, **kw: calls.append((a, kw)) or 1)
    adapter = FeishuAdapter(PlatformConfig())
    adapter._approval_state[1] = {"session_key": "session", "chat_id": "chat", "request_id": "original"}
    asyncio.run(adapter._resolve_approval(1, "once", "user", open_id="ou_user", chat_id="chat"))
    assert calls == [(("session", "once"), {"request_id": "original"})]
