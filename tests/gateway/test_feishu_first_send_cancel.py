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
            self.rfile.read(int(self.headers['Content-Length']))
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
        task = asyncio.create_task(adapter.send('chat','draft',metadata={'__hermes_streaming':True}))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel(); release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert len(operations) == 3  # no retry or compatibility I/O after cancellation
        live = list(adapter._live_streams.values())
        assert len(live) == 1
        assert live[0][0] == 'cardkit:card:im'
        result = await adapter.edit_message('chat', live[0][0], 'cancelled', finalize=True)
        assert result.success
        assert 'card' in adapter._cardkit_terminal
        assert not adapter._live_streams
    try: asyncio.run(run())
    finally:
        release.set(); adapter._get_sdk_executor().shutdown(wait=True)
        server.shutdown(); server.server_close(); thread.join()
