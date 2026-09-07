"""Tests for the MOA progress indicator added in issue #59546.

The MoA facade (``MoAChatCompletions.create``) emits a sequence of display
events so a TUI / CLI / desktop surface can render progress as references
complete and the phase transitions from ``reference`` to ``aggregator``:

  - ``moa.progress`` — fired once per reference completion with
                       ``refs_done`` and ``refs_total`` (e.g. drives a
                       status-bar ``MOA: 2/3 refs done`` indicator)
  - ``moa.phase``    — emits ``phase="reference"`` with 0/N before fan-out,
                       then ``phase="aggregator"`` before synthesis

These tests exercise the real callback surface end-to-end through the
display hook (``reference_callback``) — no mocks on the dispatch path.
The LLM is stubbed via ``call_llm`` so the test does not depend on any
real provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _response(content="ok"):
    message = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None, model="fake")


@pytest.fixture
def moa_config(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        """
moa:
  default_preset: closed
  presets:
    closed:
      enabled: true
      reference_models:
        - provider: openrouter
          model: anthropic/claude-opus-4.8
        - provider: openrouter
          model: openai/gpt-5.5
        - provider: openrouter
          model: google/gemini-3-pro
      aggregator:
        provider: openrouter
        model: anthropic/claude-opus-4.8
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _collect_emits(facade):
    """Pull every event the facade dispatches into a flat list of (event, kwargs)."""
    captured: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        captured.append((event, kwargs))

    facade.reference_callback = _capture
    return captured


def test_moa_progress_fires_for_each_reference(moa_config, monkeypatch):
    """One ``moa.progress`` event per reference completion with monotonic counts."""
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        # Per-model stub: each reference returns a stable string so we can
        # assert labels flow through; the aggregator returns the acting text.
        if kwargs.get("task") == "moa_reference":
            return _response(f"advice from {kwargs.get('model', '?')}")
        return _response("acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    facade = MoAChatCompletions("closed")
    captured = _collect_emits(facade)

    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "clean the db"}],
    )

    progress_events = [(e, k) for (e, k) in captured if e == "moa.progress"]
    # 3 references configured in moa_config => 3 progress events.
    assert len(progress_events) == 3

    # Monotonic 1/3, 2/3, 3/3 — each event carries the current count and total.
    expected_counts = [(1, 3), (2, 3), (3, 3)]
    actual_counts = [
        (k["refs_done"], k["refs_total"]) for (_, k) in progress_events
    ]
    assert actual_counts == expected_counts

    # Every progress event names the source model slot (or close to it) so a
    # status bar can render ``MOA: 2/3 refs done — openai/gpt-5.5``.
    for _, kwargs in progress_events:
        assert "label" in kwargs
        assert kwargs["label"]
        assert kwargs["status"] == "done"


def test_moa_progress_marks_failed_reference(moa_config, monkeypatch):
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        if (
            kwargs.get("task") == "moa_reference"
            and kwargs.get("model") == "openai/gpt-5.5"
        ):
            raise RuntimeError("advisor unavailable")
        return _response("advice")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    facade = MoAChatCompletions("closed")
    captured = _collect_emits(facade)
    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "review"}],
    )

    status_by_label = {
        kwargs["label"]: kwargs["status"]
        for event, kwargs in captured
        if event == "moa.progress"
    }
    assert status_by_label["openrouter:openai/gpt-5.5"] == "failed"
    assert list(status_by_label.values()).count("done") == 2


def test_moa_phase_transitions_to_aggregator(moa_config, monkeypatch):
    """Reference 0/N is visible before fan-out, then aggregation takes over."""
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        if kwargs.get("task") == "moa_reference":
            return _response("advice")
        return _response("acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    facade = MoAChatCompletions("closed")
    captured = _collect_emits(facade)

    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "plan the migration"}],
    )

    phase_events = [(e, k) for (e, k) in captured if e == "moa.phase"]
    assert [kwargs["phase"] for _, kwargs in phase_events] == [
        "reference",
        "aggregator",
    ]
    initial = phase_events[0][1]
    assert initial["refs_done"] == 0
    assert initial["refs_total"] == 3
    assert len(initial["reference_labels"]) == 3
    final = phase_events[1][1]
    assert final["aggregator"] == "openrouter:anthropic/claude-opus-4.8"
    assert final["refs_done"] == 3
    assert final["refs_total"] == 3


def test_moa_progress_counts_match_n_references(moa_config, monkeypatch):
    """Progress counters equal ``len(reference_models)`` regardless of size."""
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        if kwargs.get("task") == "moa_reference":
            return _response("advice")
        return _response("acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    facade = MoAChatCompletions("closed")
    captured = _collect_emits(facade)

    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "summarize"}],
    )

    progress_events = [(e, k) for (e, k) in captured if e == "moa.progress"]
    totals = [k["refs_total"] for _, k in progress_events]
    # Every event reports the same total — the preset's reference-model count.
    assert totals and all(t == 3 for t in totals)
    # And the final done-count equals the total (fan-out finished).
    final = progress_events[-1][1]
    assert final["refs_done"] == final["refs_total"]


def test_moa_progress_event_order_matches_fanout(moa_config, monkeypatch):
    """The initial phase precedes progress and aggregation follows references."""
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        if kwargs.get("task") == "moa_reference":
            return _response("advice")
        return _response("acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    facade = MoAChatCompletions("closed")
    captured = _collect_emits(facade)

    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "rank the options"}],
    )

    assert captured[0][0] == "moa.phase"
    assert captured[0][1]["phase"] == "reference"
    aggregator_phase_index = next(
        index
        for index, (event, kwargs) in enumerate(captured)
        if event == "moa.phase" and kwargs["phase"] == "aggregator"
    )
    reference_indexes = [
        index
        for index, (event, _kwargs) in enumerate(captured)
        if event == "moa.reference"
    ]
    assert reference_indexes
    assert max(reference_indexes) < aggregator_phase_index
    aggregating_index = next(
        index
        for index, (event, _kwargs) in enumerate(captured)
        if event == "moa.aggregating"
    )
    assert aggregator_phase_index < aggregating_index


def test_moa_progress_callback_none_safe(moa_config, monkeypatch):
    """A missing ``reference_callback`` does not break the fan-out or create()."""
    from agent.moa_loop import MoAChatCompletions

    def fake_call_llm(**kwargs):
        if kwargs.get("task") == "moa_reference":
            return _response("advice")
        return _response("acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    # No callback attached — the facade's _emit is a no-op in that case.
    facade = MoAChatCompletions("closed")
    assert facade.reference_callback is None
    facade.create(
        model="closed",
        messages=[{"role": "user", "content": "noop"}],
    )
    # Turn still resolved cleanly; aggregator slot populated as usual.
    assert facade.last_aggregator_slot is not None
    assert facade.last_aggregator_slot["model"] == "anthropic/claude-opus-4.8"
