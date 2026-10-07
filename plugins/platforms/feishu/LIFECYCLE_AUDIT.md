# Feishu CardKit lifecycle audit and local verification

## Contract and implementation

OpenAPI references governing the existing mutation contract:

- https://open.feishu.cn/document/cardkit-v1/card/update
- https://open.feishu.cn/document/cardkit-v1/card-element/content
- https://open.feishu.cn/document/cardkit-v1/card/settings

`sequence` is strictly increasing, not gap-free. Creation has no sequence field. UUID identifies one logical operation: transport ambiguity replays exactly the same request, sequence, UUID and body. Exhausted ambiguous operations reserve their watermark. An explicit nonzero response, including a UUID conflict, is NOT success and does not establish a remote watermark. There is no special-case conversion of a UUID conflict to success. `test_feishu_mutation_budget.py` exercises this rejection contract. Normal error recovery attempts a same-IM-reference compatibility patch; if that patch fails, delivery remains failed with its known identity, never a second visible send.

Local ledgers explicitly distinguish `_cardkit_issued_sequences` (confirmed operations AND ambiguous reservations) from `_cardkit_confirmed_sequences` (successful acknowledgements only). Explicit rejection does not advance either. Both ledgers follow the bounded CardKit state lifecycle.

Terminal rich schema disables streaming. Terminal IDs reject late drafts but permit final reconciliation. Tool/segment boundaries retain the existing single-stream behavior. Compatibility mutations and the original virtual CardKit identity share one lock and IM routing state.

## Independent-review blocking findings repaired

1. HTTP `card.action.trigger` now uses the installed SDK event decoder, after the existing signature/token/body checks, and returns the SDK-serialized P2 response including card/toast. The old namespace decoder turned `action.value` into a namespace as well as discarding the returned P2 response. Real SDK event tests reproduce both problems. Existing WS tests exercise actual SDK EVENT protobuf response serialization.
2. The initial send settlement boundary includes card creation, reference send, initial content, and local live-identity registration. Shielding individual executor waits alone lost identity after cancellation. Loopback HTTP tests cancel during create, reference send, and an initial content request with an empty acknowledgement, then finalize the recovered identity through actual SDK HTTP methods. Known references remain recorded even when an initial compatibility patch fails.
3. Busy rails are keyed by chat/thread + real gateway session key + inbound turn identity, not a shared chat/thread pair. The generic native-rail opt-in propagates session/user/turn metadata; a busy input selects that session's newest active turn, not the new inbound message as a turn ID. Final reconciliation can retire a live identity even when its caller only has the message ID. Interleaved A/B/A-next tests ensure one user's interruption and an older final cannot target another user's card.

### Cancellation time budget

The installed SDK client retains its explicit 15-second per-request transport timeout. A shared cancellation signal crosses the complete shielded settlement operation. Once cancellation is requested, ambiguous retries and new send attempts after backoff are suppressed; an ambiguous initial content failure does not start compatibility/cleanup I/O. One already-running mutation may therefore settle for its remaining transport timeout, rather than starting two more 15-second retries. A send in creation/reference settlement may still finish its identity transaction (create, reference, first content), nominally up to three remaining transport calls, i.e. 45 seconds, to avoid orphaning a successfully-created visible message. Backoff already in progress can add its remaining bounded delay (at most 2 seconds); it never causes another attempt after cancellation. These are transport-phase budgets, not a hard wall-clock guarantee for pathological SDK threads, token acquisition, or operating-system stalls. No thread is falsely reported cancelled while still writing remotely.

## Interactive path

- Approval callbacks return a processing toast, not a premature Approved card. The async resolver first patches the original card to Processing, then patches that SAME message to the actual resolver result (approved/denied, expired when zero waiters resolved, or failed on exception). A delayed HTTP/WS callback acknowledgement cannot overwrite the terminal result because it contains no processing card. Replay schedules no second resolution. UI patch failures are logged, not fabricated success.
- Existing update-prompt callbacks retain their established path and owner/chat/message/expiry checks. This change does not redesign the detached update-process protocol.
- Native `send_clarify` is implemented in the topical `clarify_cards.py` sibling. Choice cards provide a form with native single/multi-select and submit, Other/free-text mode, and skip. Open-ended questions begin in the existing text-capture mode. Buttons resolve `tools.clarify_gateway` directly: no synthetic `/card` event, agent interruption, or injected user turn.
- Clarify state binds the exact waiter object/request, clarify ID, session, chat, message, owner IDs and creation time. Callbacks check the still-pending waiter, ownership, message/chat, authorization and expiry before resolving. Missing owner identity refuses the native card before publishing; the existing gateway text-fallback machinery remains available. A generic optional answer gate also rejects another user's typed answer to a bound native prompt in a shared session.
- The existing `retire_clarify_card` gateway surface handles text answers and timeout/cancellation notices. An adapter-owned waiter observer covers text resolution, cancellation, timeout and session reset, claims retirement once, and patches away controls. Observers are tracked and cancelled during disconnect. Local retirement remains fail-closed if the remote patch fails; failures are logged and stale buttons cannot resolve a different/new request.
- Generic callback synthetic commands use `header.event_id` as delivery identity and deduplication key; the callback token is only a fallback when that header is absent.
- Non-native-rail/non-answer-gate adapters retain their existing behavior. Telegram, Slack, Discord and WeCom streaming/clarify regressions are included in the local suite.

## Tests and evidence

```sh
HERMES_PYTHON=/Users/xiangyu/.hermes/hermes-agent/venv/bin/python scripts/run_tests.sh tests/gateway/test_feishu*.py tests/gateway/test_native_rail_identity.py tests/gateway/test_stream_consumer*.py tests/gateway/test_stream_final_contract.py tests/gateway/test_stream_abandon_on_turn_death.py tests/gateway/test_busy*.py tests/gateway/test_turn_runner*.py tests/gateway/test_clarify*.py tests/gateway/test_telegram_clarify_buttons.py tests/gateway/test_slack_clarify_buttons.py tests/gateway/test_discord_clarify_buttons.py --file-retries 0 -p feishu_test_runtime --tb=short
```

Latest verified result: **63 files, 534 tests passed, 0 failed**, without per-file retries. Full log: `/Users/xiangyu/.hermes/cache/scratch/feishu-review-final.log`. Baseline-aware Ruff on changed production files reports zero baseline or introduced findings: `/Users/xiangyu/.hermes/cache/scratch/feishu-review-lint.log`. Added-line static security patterns report zero findings; `git diff --cached --check` passes.

Red evidence in the same scratch directory: `feishu-webhook-red.log`, `feishu-http-action-red.log`, `feishu-send-red.log`, `feishu-busy-red.log`, `feishu-wiring-red.log`, `feishu-clarify-red.log`, `feishu-approval-red.log`, `feishu-approval-order-red.log`, `feishu-generic-red.log`, `feishu-budget-red.log`, `feishu-backoff-red.log`, `feishu-retire-red.log`, `feishu-terminal-identity-red.log`, `feishu-failed-reference-red.log`, `feishu-cancel-fallback-red.log`, `feishu-owner-red.log`, `feishu-turn-anchor-red.log`, and `feishu-text-owner-red.log`.

`feishu_test_runtime.py` remains the opt-in test-only shared-interpreter lexical stdlib/runtime-metadata allowance, not a production-state allowance. The test adapter loader now supplies an isolated package context so relative sibling imports behave like the real plugin loader. No graph update was needed because this worktree has no graphify graph.

## Remaining acceptance boundaries / no rollout

All verification is local: actual installed SDK decoding/serialization and loopback HTTP, plus gateway/fake-transport regressions. No real Feishu messages, credential/app/subscription changes, public listeners, gateway restarts, push, or main-branch edits were performed. The first runner invocation auto-provisioned a worktree-local isolated runtime before the shared interpreter was explicitly selected; subsequent evidence uses the requested shared main venv.

A fresh independent review of this repair commit and authorized real Feishu acceptance are still required before deployment. Real acceptance must verify native form rendering/client submission, current application `card.action.trigger` subscription/receiving mode, CardKit/IM scopes, two tool rounds, interrupt/final reconciliation, approval result and replay, and clarify single/multi/Other/skip/text/reset/timeout. Legacy CARD-frame configurations are not proven by EVENT-frame tests. No exactly-once guarantee is claimed across process crashes, dedup expiry, server UUID/idempotency violations, or unacknowledged remote writes. Prompt state is in-memory; restart invalidates old actions. An unrecoverable unknown reference acknowledgement is surfaced as uncertainty, never a guessed success or duplicate fallback send.
