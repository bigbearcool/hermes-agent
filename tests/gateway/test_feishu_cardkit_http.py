"""Real lark_oapi HTTP/JSON decoder failure injection, loopback only."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_sdk_replays_empty_committed_response_without_new_identity():
    assert module._load_lark_oapi()
    bodies = []
    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            if len(bodies) != 1:
                self.wfile.write(b'{"code":0,"msg":"ok","data":{}}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    from lark_oapi import Client, RequestOption
    sdk = Client.builder().app_id('local-test').app_secret('not-a-secret').domain(
        f'http://127.0.0.1:{server.server_port}').enable_set_token(True).timeout(2).build()
    option = RequestOption.builder().tenant_access_token('local-test-token').build()
    adapter = module.FeishuAdapter(PlatformConfig(extra={"streaming_mode": "cardkit"}))
    adapter._client = sdk
    original = sdk.cardkit.v1.card_element.content
    sdk.cardkit.v1.card_element.content = lambda req: original(req, option)
    try:
        asyncio.run(adapter._stream_cardkit_content_with_recovery('123', 'full snapshot'))
        assert len(bodies) == 2
        assert bodies[0] == bodies[1]
        assert adapter._cardkit_issued_sequences['123'] == bodies[0]['sequence']
    finally:
        adapter._get_sdk_executor().shutdown(wait=True)
        server.shutdown()
        server.server_close()
        thread.join()
