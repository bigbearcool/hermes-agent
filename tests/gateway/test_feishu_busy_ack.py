"""Transient busy acknowledgements belong to the active stream, not a new card."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_busy_ack_updates_active_card_and_disappears_at_final():
    adapter = FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._send_message = AsyncMock(return_value=SendResult(success=True, message_id="cardkit:card:im"))
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace())
    edits = []
    async def edit(chat_id, message_id, content, *, finalize=False, metadata=None):
        edits.append((message_id, content, finalize))
        return SendResult(success=True, message_id=message_id)
    adapter._edit_message = edit
    async def run():
        await adapter.send("chat", "draft", metadata={"__hermes_streaming": True})
        assert await adapter.send_busy_ack("chat", "Interrupting current task", metadata=None)
        assert edits == [("cardkit:card:im", "draft\n\nInterrupting current task", False)]
        await adapter.edit_message("chat", "cardkit:card:im", "final", finalize=True)
        assert edits[-1] == ("cardkit:card:im", "final", True)
        assert not await adapter.send_busy_ack("chat", "new notice", metadata=None)
    asyncio.run(run())


def test_failed_busy_rail_is_consumed_without_second_message():
    adapter = FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._live_streams[("chat", "")] = ("cardkit:card:im", "draft", {"__hermes_streaming": True})
    adapter._edit_message = AsyncMock(return_value=SendResult(success=False, error="local failure"))
    assert asyncio.run(adapter.send_busy_ack("chat", "interrupt"))
    adapter._edit_message.assert_awaited_once()

