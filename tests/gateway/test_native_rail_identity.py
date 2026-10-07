"""Native status rails consume the actual gateway lane identity."""
import asyncio
from types import SimpleNamespace
from gateway.run_turn import GatewayTurnMixin
from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_turn_wiring_propagates_real_session_and_turn_to_native_rail():
    adapter = FeishuAdapter(PlatformConfig())
    host = SimpleNamespace(_run_agent_progress_threading=lambda *args: (None,None,None),
        hooks=None, _delivery_adapter_for=lambda source:adapter)
    ctx = SimpleNamespace(session_key='session:A', inbound_message_id='turn:A')
    source = SimpleNamespace(chat_id='group', user_id='A')
    runner = SimpleNamespace(_step_callback_sync=None,_event_callback_sync=None,_status_callback_sync=None)
    async def run():
        metadata = GatewayTurnMixin._run_agent_bind_turn_wiring(host,ctx,runner,source,'shared-reply-anchor',False)
        assert metadata['gateway_session_key'] == 'session:A'
        assert metadata['gateway_turn_id'] == 'turn:A'
        assert metadata['user_id'] == 'A'
    asyncio.run(run())
