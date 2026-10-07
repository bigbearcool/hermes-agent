"""Approval UI must report resolver truth on its original card."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


@pytest.mark.parametrize('outcome', [0,1,RuntimeError('resolver failed')])
def test_processing_then_resolver_truth_same_message(monkeypatch,outcome):
    assert module._load_lark_oapi()
    import tools.approval as approvals
    monkeypatch.setattr(approvals,'list_gateway_approvals',lambda key:[{'request_id':'request'}])
    def resolve(*args,**kwargs):
        if isinstance(outcome,Exception): raise outcome
        return outcome
    monkeypatch.setattr(approvals,'resolve_gateway_approval',resolve)
    adapter = module.FeishuAdapter(PlatformConfig())
    scheduled = []
    adapter._submit_on_loop = lambda loop,coro: scheduled.append(coro) or True
    adapter._run_blocking = AsyncMock(return_value=SimpleNamespace(success=lambda:True))
    adapter._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(patch=lambda req:None))))
    async def run():
        adapter._loop = asyncio.get_running_loop()
        adapter._approval_state[1] = {'session_key':'session','chat_id':'chat','message_id':'im',
            'created_at':__import__('time').time(),'request_id':'request','owner_ids':('owner',)}
        payload = {'schema':'2.0','header':{'event_id':'event','event_type':'card.action.trigger'},
            'event':{'operator':{'open_id':'owner'},
            'context':{'open_chat_id':'chat','open_message_id':'im'},
            'action':{'value':{'hermes_action':'approve_once','approval_id':1}}}}
        response = adapter._build_event_handler()._do_without_validation(json.dumps(payload).encode())
        assert response.card is None  # delayed callback ACK cannot overwrite terminal PATCH
        assert '处理中，请等待实际审批结果。' in response.toast.content
        await scheduled.pop()
        assert adapter._run_blocking.call_count == 2
        first = adapter._run_blocking.call_args_list[0].args[1]
        assert '处理中' in json.dumps(json.loads(first.request_body.content), ensure_ascii=False)
        request = adapter._run_blocking.call_args.args[1]
        assert request.message_id == 'im'
        card = json.loads(request.request_body.content)
        if outcome == 1:
            assert card == adapter._build_resolved_approval_card(choice='once',user_name='owner')
        else:
            assert card != adapter._build_resolved_approval_card(choice='once',user_name='owner')
        assert not adapter._approval_state
    asyncio.run(run())
