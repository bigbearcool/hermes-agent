"""One stream identity survives finalization and compatibility downgrade."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_late_draft_cannot_reopen_terminal_schema():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    calls = []
    def update(request):
        calls.append("final")
        return SimpleNamespace(success=lambda: True)
    def content(request):
        calls.append("late draft")
        return SimpleNamespace(success=lambda: False, code=300309, msg="streaming mode is closed")
    def settings(request):
        calls.append("reopen")
        return SimpleNamespace(success=lambda: True)
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(v1=SimpleNamespace(
        card=SimpleNamespace(update=update, settings=settings), card_element=SimpleNamespace(content=content))))
    async def run():
        assert (await adapter.edit_message("chat", "cardkit:card:im", "done", finalize=True)).success
        assert (await adapter.edit_message("chat", "cardkit:card:im", "obsolete draft")).success
        assert calls == ["final"]
    try:
        asyncio.run(run())
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)


def test_replayed_old_identity_stays_on_compatibility_transport():
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(), im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(patch=lambda request: SimpleNamespace(success=lambda: True)))))
    adapter._stream_cardkit_content_with_recovery = AsyncMock(side_effect=RuntimeError("explicit failure"))
    adapter._close_cardkit_card_before_fallback = AsyncMock()
    async def run():
        for value in ("first", "second"):
            result = await adapter.edit_message("chat", "cardkit:card:im", value,
                                               metadata={"__hermes_streaming": True})
            assert result.success
            assert result.message_id == "im"
        assert adapter._stream_cardkit_content_with_recovery.await_count == 1
        assert adapter._close_cardkit_card_before_fallback.await_count == 1
    try:
        asyncio.run(run())
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)
