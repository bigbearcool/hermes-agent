"""Initial delivery cancellation must settle identity registration, loopback SDK."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


@pytest.mark.parametrize('cancel_at', ['create', 'reference', 'initial'])
def test_first_send_cancel_preserves_reconcilable_identity(cancel_at):
    assert module._load_lark_oapi()
    entered, release = threading.Event(), threading.Event()
    operations = []
    bodies = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            operations.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            phase = 'create' if '/cardkit/' in self.path else 'reference'
            if phase == cancel_at:
                entered.set()
                assert release.wait(5)
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'code':0,'data':
                {'card_id':'card'} if phase == 'create' else {'message_id':'im'}}).encode())
        def do_PUT(self):
            operations.append(self.path)
            bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            fail_initial = cancel_at == 'initial' and len(operations) == 3
            if fail_initial:
                entered.set(); assert release.wait(5)
            self.send_response(200); self.end_headers()
            if not fail_initial: self.wfile.write(b'{"code":0,"data":{}}')
        do_PATCH = do_PUT
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    from lark_oapi import Client, RequestOption
    sdk = Client.builder().app_id('local').app_secret('not-a-secret').domain(
        f'http://127.0.0.1:{server.server_port}').enable_set_token(True).timeout(2).build()
    option = RequestOption.builder().tenant_access_token('local-token').build()
    for obj, name in [(sdk.cardkit.v1.card,'create'), (sdk.im.v1.message,'create'),
                      (sdk.cardkit.v1.card_element,'content'), (sdk.cardkit.v1.card,'settings'),
                      (sdk.cardkit.v1.card,'update')]:
        original = getattr(obj, name)
        setattr(obj, name, lambda req, original=original: original(req, option))
    adapter = module.FeishuAdapter(PlatformConfig(extra={'streaming_mode':'cardkit'})); adapter._client = sdk
    async def run():
        from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
        consumer = GatewayStreamConsumer(adapter, 'chat', StreamConsumerConfig(
            edit_interval=.01, buffer_threshold=1, cursor=''))
        task = asyncio.create_task(consumer.run())
        consumer.on_delta('draft')
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel(); release.set()
        await task  # the real run() handler must adopt identity and finalize it
        assert consumer.message_id == 'cardkit:card:im'
        assert consumer.final_content_delivered
        assert consumer.delivered_final_matches('draft') is True
        assert 'card' in adapter._cardkit_terminal
        assert not adapter._live_streams
        assert sum('/im/' in path for path in operations) == 1
        assert sum(path == '/open-apis/cardkit/v1/cards/card' for path in operations) == 1
        from types import SimpleNamespace as NS
        from gateway.run_turn import GatewayTurnMixin
        response = {'final_response': 'draft'}
        turn_ctx = NS(stream_consumer_holder=[consumer], source=NS(chat_id='chat'), session_key='session')
        await GatewayTurnMixin()._run_agent_mark_streamed_delivery(response, turn_ctx)
        assert response['already_sent'] is True
        response = {'final_response': 'reconciled final'}
        await GatewayTurnMixin()._run_agent_mark_streamed_delivery(response, turn_ctx)
        assert response['already_sent'] is True
        assert 'reconciled final' in json.dumps(bodies[-1])
        # The actual queued reconciliation lane must also edit the adopted ID,
        # rather than send a second reference and overwrite the old live entry.
        from gateway.run import GatewayRunner
        runner = object.__new__(GatewayRunner)
        await runner._deliver_queued_first_response('complete final', source=turn_ctx.source,
            adapter=adapter, metadata=None, text_already_delivered=False,
            deliver_media=False, stream_consumer=consumer)
        assert sum('/im/' in path for path in operations) == 1
        assert not adapter._live_streams
        assert 'complete final' in json.dumps(bodies[-1])
    try: asyncio.run(run())
    finally:
        release.set(); adapter._get_sdk_executor().shutdown(wait=True)
        server.shutdown(); server.server_close(); thread.join()
