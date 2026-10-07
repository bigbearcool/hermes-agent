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


def test_interleaved_group_busy_acks_are_session_and_turn_scoped():
    adapter = FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace())
    async def send(chat, content, reply, metadata):
        return SendResult(success=True, message_id='cardkit:' + content + ':im')
    adapter._send_message = send
    edits = []
    async def edit(chat, message, content, **kwargs):
        edits.append((message, content)); return SendResult(success=True, message_id=message)
    adapter._edit_message = edit
    async def run():
        def meta(session, turn):
            return {'__hermes_streaming':True, 'gateway_session_key':session, 'gateway_turn_id':turn}
        await adapter.send('group','A1',metadata=meta('A','1'))
        await adapter.send('group','B1',metadata=meta('B','1'))
        await adapter.send_busy_ack('group','stop A',metadata={'gateway_session_key':'A'})
        assert edits[-1] == ('cardkit:A1:im','A1\n\nstop A')
        await adapter.send('group','A2',metadata=meta('A','2'))
        await adapter.edit_message('group','cardkit:A1:im','old final',finalize=True)
        assert not any(live[0] == 'cardkit:A1:im' for live in adapter._live_streams.values())
        await adapter.send_busy_ack('group','stop B',metadata={'gateway_session_key':'B'})
        assert edits[-1] == ('cardkit:B1:im','B1\n\nstop B')
        await adapter.send_busy_ack('group','stop A2',metadata={'gateway_session_key':'A'})
        assert edits[-1] == ('cardkit:A2:im','A2\n\nstop A2')
    asyncio.run(run())


def test_concurrent_group_sends_and_busy_acks_keep_separate_cards():
    adapter = FeishuAdapter(PlatformConfig(extra={'streaming_mode':'cardkit'}))
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace())
    async def run():
        entered_a, entered_b = asyncio.Event(), asyncio.Event()
        async def send(chat, content, reply, metadata):
            if content == 'A':
                entered_a.set(); await entered_b.wait()
            else:
                entered_b.set(); await entered_a.wait()
            return SendResult(success=True,message_id=f'cardkit:{content}:im')
        adapter._send_message = send
        edits = []
        async def edit(chat, message, content, **kwargs):
            edits.append((message,content)); await asyncio.sleep(0)
            return SendResult(success=True,message_id=message)
        adapter._edit_message = edit
        await asyncio.gather(*(adapter.send('group',name,metadata={
            '__hermes_streaming':True,'gateway_session_key':name,'gateway_turn_id':'turn-'+name}) for name in ['A','B']))
        await asyncio.gather(*(adapter.send_busy_ack('group','stop '+name,
            metadata={'gateway_session_key':name}) for name in ['A','B']))
        assert sorted(edits) == [('cardkit:A:im','A\n\nstop A'),('cardkit:B:im','B\n\nstop B')]
    asyncio.run(run())

