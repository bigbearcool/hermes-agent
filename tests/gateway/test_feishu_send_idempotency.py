"""Outbound retries and fallback must not create another visible reference."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_message_retry_preserves_uuid():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    attempts = []
    def create(request):
        attempts.append(request.request_body.uuid)
        if len(attempts) == 1:
            raise json.JSONDecodeError('lost ack after send', '', 0)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id='im'))
    adapter._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=create))))
    try:
        result = asyncio.run(adapter.send('chat', 'hello'))
        assert result.success
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)


def test_exhausted_ambiguous_reference_does_not_send_a_second_card():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace())
    adapter._create_cardkit_card = AsyncMock(return_value='card')
    adapter._feishu_send_with_retry = AsyncMock(side_effect=json.JSONDecodeError('lost reference ack', '', 0))
    result = asyncio.run(adapter.send('chat', 'draft', metadata={"__hermes_streaming": True}))
    assert not result.success
    assert 'uncertain' in result.error
    assert adapter._feishu_send_with_retry.await_count == 1


def test_failed_initial_compatibility_patch_does_not_create_another_card():
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    def fail(request):
        raise RuntimeError('patch refused')
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(), im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(patch=fail))))
    adapter._create_cardkit_card = AsyncMock(return_value='card')
    adapter._feishu_send_with_retry = AsyncMock(return_value=SimpleNamespace(success=lambda: True,
                                                          data=SimpleNamespace(message_id='im')))
    adapter._stream_cardkit_content_with_recovery = AsyncMock(side_effect=RuntimeError('explicit failure'))
    adapter._close_cardkit_card_before_fallback = AsyncMock()
    try:
        result = asyncio.run(adapter.send('chat', 'snapshot', metadata={"__hermes_streaming": True}))
        assert not result.success
        assert result.message_id == 'im'
        assert adapter._feishu_send_with_retry.await_count == 1
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)
