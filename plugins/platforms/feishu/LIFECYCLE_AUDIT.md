# Feishu CardKit lifecycle audit and local verification

## Contract and implementation

Official OpenAPI references checked during this change:

- https://open.feishu.cn/document/cardkit-v1/card/update
- https://open.feishu.cn/document/cardkit-v1/card-element/content
- https://open.feishu.cn/document/cardkit-v1/card/settings

`sequence` is strictly increasing, not gap-free. `uuid` is an idempotent operation ID. Creation has no sequence field; first mutation uses 1. A transport timeout or an empty/unparseable acknowledgement does not prove rejection: replay the identical request, including sequence/uuid/body. If transport retries exhaust, reserve that sequence so a later operation does not reuse it. Explicit 300317 rejects are not evidence of the server's actual watermark; do not guess it by incrementing five times.

The SDK transport now has an explicit 15-second timeout. Executor cancellation is delayed until its actual HTTP worker settles, preserving the per-identity lock and contextvars across timeout/cancellation. The complete logical CardKit mutation (including replay and local commit) is also shielded. Real SDK + loopback HTTP regression injects an empty response after the first PUT and asserts identical serialized replay.

Terminal rich schema uses `streaming_mode=false`; terminal IDs reject late drafts, but allow final reconciliation. Live closed-window recovery alone may reopen streaming. Tool/segment boundaries already obey the adapter's single-stream capability; existing consumer regressions cover those paths without modifying their cross-platform behavior.

Compatibility transition patches the same IM reference; it never withdraws that message or sends a second fallback because patching failed. The original virtual ID and compatibility IM ID share one lock and routing state. Outbound IM send retries also preserve one UUID. If reference-send acknowledgements remain ambiguous after retries, fail explicitly rather than send a new compatibility reference with a different identity.

Busy acknowledgements target the active card by chat/thread, are serialized against edits, and are superseded by later progress/final content. A failed status-rail update must not generate a permanent separate interrupt acknowledgement. Without an active stream, the original gateway reply behavior is retained.

## Interactive path

- Existing approval and update-prompt buttons remain separate interactive cards; streaming cards have no invented business actions.
- Existing `register_p2_card_action_trigger` is connected through the adapter event handler in both configured transport paths. Installed `lark_oapi.ws.client.Client._handle_data_frame` dispatches current EVENT frames through `do_without_validation` and serializes the inline card response. `test_feishu_callback_transport.py` drives that real SDK protobuf path and replays the event: one resolution is scheduled, replay contains no second inline card mutation. This is not evidence that an application's backend subscriptions are configured.
- Prompt state binds chat/message, creation time, owner IDs and the original approval request ID. The gateway supplies these bindings only for Feishu. Callbacks validate source, user authorization, prompt expiry, pending request identity and a claimed flag under a thread lock before synchronous acknowledgement. Resolution targets that request, not all later approvals in the session.
- Generic callback actions retain the existing synthetic-event path and token TTL deduplication. Clarification remains the existing gateway text/choice mechanism; there is no dedicated Feishu `send_clarify` button implementation to connect. No new buttons or public HTTP endpoint were added.

Required real-environment acceptance: confirm latest `card.action.trigger` subscription and receiving mode in the application's backend, installed SDK compatibility, CardKit write/IM scopes, then exercise two tool rounds, interruption, final reconciliation, approval click/replay/wrong-user and update-prompt click. Do not infer old CARD-frame callback support from EVENT-frame tests. Legacy callback settings may require migration by the application owner. Webhook mode requires its existing configured reachable/verified endpoint; this change opens no listener or port and changes no callback credentials.

## Tests and evidence

Command from this worktree (shared interpreter is optional):

```sh
HERMES_PYTHON=/Users/xiangyu/.hermes/hermes-agent/venv/bin/python scripts/run_tests.sh tests/gateway/test_feishu*.py tests/gateway/test_stream_consumer*.py tests/gateway/test_stream_final_contract.py tests/gateway/test_stream_abandon_on_turn_death.py tests/gateway/test_busy*.py tests/gateway/test_turn_runner*.py --file-retries 0 -p feishu_test_runtime --tb=short
```

Verified result: **46 files, 448 tests passed, 0 failed**. Full local evidence: `/Users/xiangyu/.hermes/cache/scratch/feishu-cardkit-final.log`. Red evidence includes `feishu-send-red.log`, `feishu-cardkit-compat-red.log`, and earlier lifecycle regression logs in that scratch directory. `git diff --check` passed.

`feishu_test_runtime.py` is an opt-in test-only workaround for a shared interpreter physically nested in another `.hermes` checkout: it permits interpreter stdlib loading and exact runtime metadata probes, not production profile state or secrets. No real Feishu traffic, app configuration changes, gateway restart, or push was performed. Real Feishu acceptance is still required. No graph update was run because this checkout has no graphify graph.

## Remaining boundaries

These changes do not guarantee delivery across process crashes, dedup retention expiry, or SDK/server idempotency violations. An unrecoverable ambiguous reference acknowledgement is surfaced as failure rather than claiming exactly-once delivery. Prompt state remains in-memory; restart invalidates old buttons. This is a tested implementation, not a production rollout or remote platform read-back.
