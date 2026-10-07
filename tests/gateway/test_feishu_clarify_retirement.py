"""Text, reset, timeout and callback ownership retire native clarify cards."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module
from tools import clarify_gateway as cg


@pytest.mark.parametrize('ending',['text','cancel','reset','timeout','disconnect'])
def test_clarify_retirement_and_task_cleanup(ending):
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._client = NS(im=NS(v1=NS(message=NS(patch=lambda req:None))))
    adapter._run_blocking = AsyncMock(return_value=NS(success=lambda:True))
    adapter._feishu_send_with_retry = AsyncMock(return_value=NS(success=lambda:True,data=NS(message_id='im')))
    cg.register('cid','session','question',['one'])
    async def run():
        adapter._loop = asyncio.get_running_loop()
        await adapter.send_clarify('chat','question',['one'],'cid','session',metadata={'user_id':'owner'})
        if ending == 'disconnect':
            await adapter.disconnect()
            assert not adapter._clarify_tasks
            assert not adapter._clarify_state
            return
        if ending == 'text': cg.resolve_text_response_for_session('session','1')
        elif ending == 'cancel': cg.resolve_gateway_clarify('cid',cg.CANCELLED)
        elif ending == 'reset': cg.clear_session('session')
        else: assert cg.wait_for_response('cid',.001) is None
        await asyncio.sleep(.3)
        assert not adapter._clarify_state
        assert not adapter._clarify_tasks
        request = adapter._run_blocking.call_args.args[1]
        assert request.message_id == 'im'
        card = json.loads(request.request_body.content)
        assert 'hermes_clarify_action' not in json.dumps(card)
    try: asyncio.run(run())
    finally: cg.clear_session('session')


def test_shared_session_text_cannot_answer_another_users_native_prompt():
    from gateway.run_inbound import GatewayInboundMixin
    adapter = module.FeishuAdapter(PlatformConfig())
    entry = cg.register('cid','session','question',['one'])
    adapter._clarify_state['cid'] = {'session_key':'session','chat_id':'chat','message_id':'im',
        'created_at':__import__('time').time(),'owner_ids':('owner',),'entry':entry}
    host = NS(_delivery_adapter_for=lambda source:adapter,
        _pending_event_audio_paths=lambda event:[],_prepare_clarify_reply_text=AsyncMock(return_value='1'))
    source = NS(user_id='outsider',chat_id='chat')
    try:
        asyncio.run(GatewayInboundMixin._hm_clarify_reply(host,NS(),source,'session'))
        assert not entry.event.is_set()
    finally: cg.clear_session('session')
