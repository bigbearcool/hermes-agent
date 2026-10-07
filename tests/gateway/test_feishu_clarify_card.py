"""Native Feishu clarify uses the gateway waiter, never a synthetic /card turn."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module
from tools import clarify_gateway as cg


@pytest.mark.parametrize('multi,action,value,expected', [
    (False,'submit','0','one'), (True,'submit',['0','1'],'["one", "two"]'),
    (False,'skip',None,cg.SKIPPED), (False,'other',None,None)])
def test_sdk_clarify_callback_resolves_real_waiter(multi,action,value,expected):
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(patch=lambda request: None))))
    adapter._run_blocking = AsyncMock(return_value=SimpleNamespace(code=0))
    adapter.send = AsyncMock(return_value=module.SendResult(success=True,message_id='im'))
    adapter._feishu_send_with_retry = AsyncMock(return_value=SimpleNamespace(code=0,data=SimpleNamespace(message_id='im')))
    adapter._finalize_send_result = lambda *args: module.SendResult(success=True,message_id='im')
    entry = cg.register('clarify','session','question',['one','two'],multi_select=multi)
    async def run():
        adapter._loop = asyncio.get_running_loop()
        result = await adapter.send_clarify('chat','question',['one','two'],'clarify','session',
            metadata={'__hermes_prompt_user_ids':('ou_owner',)})
        assert result.success
        card = json.loads(adapter._feishu_send_with_retry.call_args.kwargs['payload'])
        assert 'form' in json.dumps(card)
        payload = {'schema':'2.0','header':{'event_id':'event','event_type':'card.action.trigger'},
            'event':{'operator':{'open_id':'ou_owner'},'context':{'open_chat_id':'chat','open_message_id':'im'},
            'action':{'tag':'button','value':{'hermes_clarify_action':action,'clarify_id':'clarify'},
                      'form_value':{'selection':value}}}}
        adapter._build_event_handler()._do_without_validation(json.dumps(payload).encode())
        if action == 'other':
            assert entry.awaiting_text and not entry.event.is_set()
            assert cg.resolve_text_response_for_session('session','free answer')
        else:
            assert entry.event.is_set()
            assert entry.response == expected
        await asyncio.sleep(.3)
        assert 'clarify' not in adapter._clarify_state
    adapter.edit_message = AsyncMock(return_value=module.SendResult(success=True,message_id='im'))
    try: asyncio.run(run())
    finally: cg.clear_session('session')


@pytest.mark.parametrize('bad', ['user','chat','message','expiry','request'])
def test_clarify_callback_rejects_foreign_or_retired_binding(bad):
    assert module._load_lark_oapi()
    import time
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._loop = SimpleNamespace(is_closed=lambda:False)
    entry = cg.register('cid','session','q',['one'])
    adapter._clarify_state['cid'] = {'session_key':'session','chat_id':'chat','message_id':'im',
        'created_at':time.time(),'owner_ids':('owner',),'entry':entry}
    if bad == 'expiry': assert cg.wait_for_response('cid', .001) is None
    if bad == 'request': cg.clear_session('session'); cg.register('cid','session','new q',['one'])
    payload = {'schema':'2.0','header':{'event_id':'event','event_type':'card.action.trigger'},
        'event':{'operator':{'open_id':'intruder' if bad == 'user' else 'owner'},
            'context':{'open_chat_id':'foreign' if bad == 'chat' else 'chat',
                       'open_message_id':'forwarded' if bad == 'message' else 'im'},
            'action':{'value':{'hermes_clarify_action':'submit','clarify_id':'cid'},'form_value':{'selection':'0'}}}}
    try:
        response = adapter._build_event_handler()._do_without_validation(json.dumps(payload).encode())
        assert response.card is None
        pending = cg.get_pending_for_session('session',include_choice_prompts=True)
        if bad == 'expiry':
            assert pending is None
        else:
            assert pending is not None and not pending.event.is_set()
    finally: cg.clear_session('session')


@pytest.mark.parametrize('timeout', [3600, 0])
def test_live_clarify_waiter_accepts_owner_after_dedup_ttl(timeout):
    import threading
    import time
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._loop = SimpleNamespace(is_closed=lambda: False)
    entry = cg.register('late', 'session', 'q', ['one'])
    adapter._clarify_state['late'] = {'session_key': 'session', 'chat_id': 'chat',
        'message_id': 'im', 'created_at': time.time() - 901,
        'owner_ids': ('owner',), 'entry': entry}
    answers = []
    waiter = threading.Thread(target=lambda: answers.append(cg.wait_for_response('late', timeout)))
    waiter.start()
    payload = {'schema': '2.0', 'header': {'event_id': 'late-event', 'event_type': 'card.action.trigger'},
        'event': {'operator': {'open_id': 'owner'},
            'context': {'open_chat_id': 'chat', 'open_message_id': 'im'},
            'action': {'value': {'hermes_clarify_action': 'submit', 'clarify_id': 'late'},
                'form_value': {'selection': '0'}}}}
    try:
        response = adapter._build_event_handler()._do_without_validation(json.dumps(payload).encode())
        assert entry.event.is_set(), 'A live waiter must not expire at the replay-cache TTL'
        waiter.join(2)
        assert not waiter.is_alive()
        assert answers == ['one']
        assert response.card is not None
    finally:
        cg.clear_session('session')
        waiter.join(2)


def test_missing_owner_does_not_publish_an_unbound_native_prompt():
    assert module._load_lark_oapi()
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._feishu_send_with_retry = AsyncMock(return_value=SimpleNamespace(success=lambda:True,data=SimpleNamespace(message_id='im')))
    cg.register('cid','session','question',['one'])
    async def run():
        result = await adapter.send_clarify('chat','question',['one'],'cid','session')
        assert not result.success
        assert adapter._feishu_send_with_retry.await_count == 0
    try: asyncio.run(run())
    finally: cg.clear_session('session')
