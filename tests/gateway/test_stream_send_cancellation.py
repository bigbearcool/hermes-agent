"""Platform-neutral send settlement must preserve cancellation semantics."""
import asyncio
from types import SimpleNamespace

import pytest

from gateway.platforms.base import SendCancelledError, SendResult
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig


@pytest.mark.parametrize('settled', [False, True])
@pytest.mark.parametrize('success', [False, True])
def test_first_send_cancellation_is_not_a_successful_return(settled, success):
    result = SendResult(success=success, message_id='committed' if settled else None)
    cancellation = SendCancelledError(result) if settled else asyncio.CancelledError()

    async def send(**kwargs):
        raise cancellation

    adapter = SimpleNamespace(send=send)
    consumer = GatewayStreamConsumer(adapter, 'chat', StreamConsumerConfig())

    async def run():
        with pytest.raises(asyncio.CancelledError) as caught:
            await consumer._first_send('draft', finalize=False)
        assert caught.value is cancellation
        assert consumer.message_id == ('committed' if settled else None)
        assert not consumer.final_content_delivered
        if settled and not success:
            assert consumer._last_sent_text == ''  # partial delivery still needs an edit

    asyncio.run(run())


def test_send_settlement_repeated_cancellation_keeps_result():
    from plugins.platforms.feishu.cardkit_mutations import await_settled

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        result = SendResult(success=True, message_id='committed')

        async def worker():
            entered.set()
            await release.wait()
            return result

        task = asyncio.create_task(await_settled(worker(), cancelled_error_factory=SendCancelledError))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(SendCancelledError) as caught:
            await task
        assert caught.value.result is result
        assert task.cancelled()

    asyncio.run(run())
