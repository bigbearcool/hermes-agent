"""Cancellation stops ambiguity retries; issued is not confirmed."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_cancelled_ambiguous_mutation_does_not_start_another_retry():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def blocking(function, request):
        calls.append(request)
        entered.set(); await release.wait()
        raise json.JSONDecodeError('lost ack','',0)
    adapter._run_blocking = blocking
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(v1=SimpleNamespace(card_element=SimpleNamespace(content=None))))
    async def run():
        task = asyncio.create_task(adapter._stream_cardkit_content_with_recovery('card','draft'))
        await entered.wait(); task.cancel(); await asyncio.sleep(0); release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert len(calls) == 1
        assert adapter._cardkit_issued_sequences['card'] == 1
        assert adapter._cardkit_confirmed_sequences.get('card',0) == 0
    asyncio.run(run())


def test_cancel_during_send_backoff_does_not_issue_next_attempt():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._client = SimpleNamespace()
    calls = []
    async def run():
        first = asyncio.Event()
        async def raw(**kwargs):
            calls.append(kwargs); first.set()
            raise json.JSONDecodeError('lost ack','',0)
        adapter._send_raw_message = raw
        task = asyncio.create_task(adapter.send('chat','hello'))
        await first.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert len(calls) == 1
    asyncio.run(run())


def test_explicit_uuid_conflict_is_not_confirmed_success():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(v1=SimpleNamespace(card_element=SimpleNamespace(content=None))))
    async def blocking(*args):
        return SimpleNamespace(success=lambda:False,code=300313,msg='uuid conflict')
    adapter._run_blocking = blocking
    with pytest.raises(RuntimeError,match='uuid conflict'):
        asyncio.run(adapter._stream_cardkit_content_with_recovery('card','draft'))
    assert adapter._cardkit_confirmed_sequences.get('card',0) == 0
