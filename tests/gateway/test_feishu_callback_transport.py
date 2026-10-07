"""Latest card.action.trigger callbacks use EVENT frames in the installed SDK."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as module


def test_sdk_ws_event_frame_returns_one_inline_resolution(monkeypatch):
    assert module._load_lark_oapi()
    import tools.approval as approvals
    monkeypatch.setattr(approvals, 'list_gateway_approvals', lambda key: [{"request_id": "request"}])
    adapter = module.FeishuAdapter(PlatformConfig())
    adapter._loop = SimpleNamespace(is_closed=lambda: False)
    adapter._approval_state[1] = {"session_key": "session", "chat_id": "chat", "message_id": "message",
                                  "created_at": time.time(), "request_id": "request", "owner_ids": ("ou_user",)}
    scheduled = []
    def submit(loop, coro):
        scheduled.append(True)
        coro.close()
        return True
    adapter._submit_on_loop = submit
    from lark_oapi.ws.client import Client
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame
    client = Client('local-test', 'not-a-secret', event_handler=adapter._build_event_handler())
    sent = []
    async def write(data):
        sent.append(data)
    client._write_message = write
    def frame():
        result = Frame(SeqID=1, LogID=1, service=1, method=1)
        for key, value in [('message_id','event'), ('trace_id','trace'), ('sum','1'), ('seq','0'), ('type','event')]:
            header = result.headers.add()
            header.key, header.value = key, value
        result.payload = json.dumps({"schema":"2.0", "header":{"event_id":"event", "event_type":"card.action.trigger"},
            "event":{"operator":{"open_id":"ou_user"}, "token":"local-action", "context":{
                "open_chat_id":"chat", "open_message_id":"message"}, "action":{
                "tag":"button", "value":{"hermes_action":"approve_once", "approval_id":1}}}}).encode()
        return result
    async def run():
        await client._handle_data_frame(frame())
        await client._handle_data_frame(frame())
    asyncio.run(run())
    assert len(scheduled) == 1
    first, replay = Frame(), Frame()
    first.ParseFromString(sent[0]); replay.ParseFromString(sent[1])
    first_response = json.loads(first.payload)
    replay_response = json.loads(replay.payload)
    assert first_response['code'] == 200
    assert 'toast' in json.loads(base64.b64decode(first_response['data']))
    assert 'toast' not in json.loads(base64.b64decode(replay_response['data']))
    assert 'card' not in json.loads(base64.b64decode(first_response['data']))
