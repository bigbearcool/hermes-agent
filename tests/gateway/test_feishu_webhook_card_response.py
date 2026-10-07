"""HTTP callback must return the actual installed SDK response wire data."""
import asyncio
import json
from types import SimpleNamespace
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_webhook_preserves_sdk_card_and_toast():
    assert module._load_lark_oapi()
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    response = P2CardActionTriggerResponse({"toast": {"type": "info", "content": "processing"},
        "card": {"type": "raw", "data": {"elements": []}}})
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._on_card_action_trigger = lambda data: response
    payload = json.dumps({"schema":"2.0", "header": {"event_id":"event", "event_type": "card.action.trigger"}, "event": {}}).encode()
    async def read(n):
        nonlocal payload
        result, payload = payload, b''
        raise asyncio.IncompleteReadError(result, n)
    request = SimpleNamespace(remote='127.0.0.1', headers={}, content_length=len(payload),
        content=SimpleNamespace(readexactly=read))
    result = asyncio.run(adapter._handle_webhook_request(request))
    wire = json.loads(result.text)
    assert 'card' in wire, (wire, module.__file__)
    assert wire['card']['type'] == 'raw'
    assert wire['toast']['content'] == 'processing'


def test_http_callback_decodes_action_value_as_sdk_mapping(monkeypatch):
    assert module._load_lark_oapi()
    import time
    import tools.approval as approvals
    monkeypatch.setattr(approvals, 'list_gateway_approvals', lambda key: [{'request_id':'request'}])
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._loop = SimpleNamespace(is_closed=lambda:False)
    adapter._approval_state[1] = {'session_key':'session','message_id':'im','chat_id':'chat',
        'created_at':time.time(),'request_id':'request','owner_ids':('owner',)}
    scheduled = []
    def submit(loop,coro):
        scheduled.append(coro.__name__); coro.close(); return True
    adapter._submit_on_loop = submit
    payload = json.dumps({'schema':'2.0','header':{'event_id':'one','event_type':'card.action.trigger'},
        'event':{'operator':{'open_id':'owner'},'context':{'open_chat_id':'chat','open_message_id':'im'},
            'action':{'value':{'hermes_action':'approve_once','approval_id':1}}}}).encode()
    async def read(n): raise asyncio.IncompleteReadError(payload,n)
    result = asyncio.run(adapter._handle_webhook_request(SimpleNamespace(remote='127.0.0.1',
        headers={},content_length=len(payload),content=SimpleNamespace(readexactly=read))))
    assert scheduled == ['_resolve_approval']
    assert '处理中' in json.dumps(json.loads(result.text), ensure_ascii=False)
