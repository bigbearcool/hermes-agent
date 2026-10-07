"""Generic callback delivery identity is header.event_id, token only fallback."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_event_id_deduplicates_replays_not_distinct_token_reuse():
    adapter = FeishuAdapter(PlatformConfig())
    adapter._dispatch_synthetic_event = AsyncMock()
    def data(event_id,token):
        return NS(header=NS(event_id=event_id),event=NS(token=token,operator=NS(open_id='user'),
            context=NS(open_chat_id='chat'),action=NS(tag='button',value={'command':'/status'})))
    async def run():
        await adapter._handle_card_action_event(data('one','token'))
        await adapter._handle_card_action_event(data('one','changed-token'))
        await adapter._handle_card_action_event(data('two','token'))
        ids = [call.kwargs['message_id'] for call in adapter._dispatch_synthetic_event.call_args_list]
        assert ids == ['one','two']
    asyncio.run(run())
