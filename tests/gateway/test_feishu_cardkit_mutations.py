"""Mutation contracts at the real SDK request boundary (no Feishu traffic)."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


@pytest.mark.parametrize("operation", ["content", "settings", "update"])
def test_ambiguous_mutation_replays_identical_request(operation):
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    seen = []
    def mutate(request):
        seen.append(request)
        if len(seen) == 1:
            raise json.JSONDecodeError("empty response after commit", "", 0)
        return SimpleNamespace(success=lambda: True)
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(v1=SimpleNamespace(
        card=SimpleNamespace(settings=mutate, update=mutate),
        card_element=SimpleNamespace(content=mutate))))
    async def run():
        if operation == "content":
            await adapter._stream_cardkit_content_with_recovery("card", "hello")
        elif operation == "settings":
            await adapter._set_cardkit_streaming_mode("card", False)
        else:
            await adapter._update_cardkit_card("card", adapter._build_final_cardkit_card("done"))
    try:
        asyncio.run(run())
        assert len(seen) == 2
        assert seen[0] is seen[1]
        assert seen[0].request_body.uuid == seen[1].request_body.uuid
        assert adapter._cardkit_issued_sequences["card"] == seen[-1].request_body.sequence
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)


@pytest.mark.parametrize("renderer", ["cardkit", "card"])
def test_cancelled_edit_does_not_release_card_before_worker_settles(renderer):
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": renderer}))
    started, release = threading.Event(), threading.Event()
    seen = []
    def mutate(request):
        seen.append(getattr(request.request_body, 'sequence', len(seen) + 1))
        if len(seen) == 1:
            started.set()
            assert release.wait(5)
        return SimpleNamespace(success=lambda: True)
    adapter._client = SimpleNamespace(cardkit=SimpleNamespace(v1=SimpleNamespace(
        card=SimpleNamespace(update=mutate), card_element=SimpleNamespace(content=mutate))),
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(patch=mutate))))
    message_id = "cardkit:card:im" if renderer == "cardkit" else "im"
    async def run():
        first = asyncio.create_task(adapter.edit_message("chat", message_id, "draft", metadata={"__hermes_streaming": True}))
        await asyncio.to_thread(started.wait, 5)
        first.cancel()
        second = asyncio.create_task(adapter.edit_message("chat", message_id, "final", finalize=True, metadata={"__hermes_streaming": True}))
        await asyncio.sleep(.05)
        try:
            assert len(seen) == 1, "cancelled blocking request overlaps terminal mutation"
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
        assert seen == [1, 2]
    try:
        asyncio.run(run())
    finally:
        release.set()
        adapter._get_sdk_executor().shutdown(wait=True)
