"""Langfuse tracing is optional and never fatal. No test here reaches a real Langfuse host."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from treefleet import observability as obs
from treefleet.contracts import CursorUsage

SRC = str(Path(__file__).resolve().parents[1] / "src")
SENTINEL_PK = "pk-lf-sentinel-public-0001"
SENTINEL_SK = "sk-lf-sentinel-secret-0002"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(name, raising=False)
    obs.reset_for_tests()
    yield
    client = obs._client
    obs.reset_for_tests()
    if client is not None and hasattr(client, "shutdown"):
        try:
            client.shutdown()
        except Exception:
            pass


class _Boom:
    """A client whose every call raises, with the secret inside the message."""

    def flush(self) -> None:
        raise RuntimeError(f"flush failed for {SENTINEL_SK}")

    def start_as_current_observation(self, **_kwargs: Any) -> Any:
        raise RuntimeError(f"cannot start {SENTINEL_SK}")


def test_keys_absent_is_a_noop_and_imports_nothing_from_langfuse() -> None:
    code = (
        "import sys\n"
        "import treefleet.observability as o\n"
        "assert o.init_tracing('orchestrator') is False\n"
        "assert o.instrument_orchestrator() is False and o.instrument_worker() is False\n"
        "with o.run_trace('r1', 'goal'):\n"
        "    with o.cursor_span(model='m') as s:\n"
        "        s.finish(duration_s=1.0)\n"
        "o.flush()\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('langfuse','openinference','google','crewai')]\n"
        "assert not bad, bad\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_")}
    env["PYTHONPATH"] = SRC
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=60, shell=False
    )
    assert done.returncode == 0, done.stderr


def test_one_key_only_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", SENTINEL_PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "  ")
    assert obs.init_tracing("orchestrator") is False
    assert obs.enabled() is False


def test_flush_that_raises_does_not_raise(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(obs, "_client", _Boom())
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        obs.flush()
    assert "RuntimeError" in caplog.text
    assert SENTINEL_SK not in caplog.text


def test_failing_spans_never_change_the_body(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(obs, "_client", _Boom())
    ran: list[str] = []
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        with obs.run_trace("r1", "goal"):
            with obs.cursor_span(model=None) as span:
                span.finish(duration_s=0.5, usage=CursorUsage(inputTokens=1))
                ran.append("body")
        with pytest.raises(ValueError, match="kept"):
            with obs.run_trace("r1"):
                raise ValueError("kept")
    assert ran == ["body"]
    assert SENTINEL_SK not in caplog.text


def test_client_constructor_failure_is_logged_without_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import langfuse

    def explode(**_kwargs: Any) -> None:
        raise RuntimeError(f"bad credentials {SENTINEL_PK} {SENTINEL_SK}")

    monkeypatch.setattr(langfuse, "Langfuse", explode)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", SENTINEL_PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL_SK)
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        assert obs.init_tracing("orchestrator") is False
    assert obs.enabled() is False
    assert SENTINEL_PK not in caplog.text
    assert SENTINEL_SK not in caplog.text


def test_instrumentor_import_failure_does_not_fail_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(obs, "_client", object())
    monkeypatch.setitem(sys.modules, "openinference.instrumentation.google_adk", None)
    monkeypatch.setitem(sys.modules, "openinference.instrumentation.crewai", None)
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        assert obs.instrument_orchestrator() is False
        assert obs.instrument_worker() is False
    assert "instrumentation skipped" in caplog.text


def test_traced_run_shares_one_session_id(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", SENTINEL_PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL_SK)
    # Nothing listens on port 9, so the Langfuse exporter can never reach a real host.
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        assert obs.init_tracing("orchestrator", tracer_provider=provider) is True
        with obs.run_trace("run-abc", "Add a docstring", role="orchestrator"):
            pass
        with obs.run_trace("run-abc", role="worker", task_id="t1"):
            with obs.cursor_span(model="auto", task_id="t1") as span:
                span.finish(
                    duration_s=2.5,
                    usage=CursorUsage(inputTokens=10, outputTokens=5, cacheReadTokens=3),
                    exit_code=0,
                )
        obs.flush(timeout_s=2.0)
    spans = exporter.get_finished_spans()
    assert len(spans) >= 3
    sessions = {
        (s.attributes or {}).get("session.id") or (s.attributes or {}).get("langfuse.session.id") for s in spans
    }
    assert sessions == {"run-abc"}
    cursor = [s for s in spans if s.name == obs.CURSOR_SPAN_NAME]
    assert len(cursor) == 1
    attrs = dict(cursor[0].attributes or {})
    assert attrs.get("langfuse.observation.type") == "generation"
    assert attrs.get("langfuse.observation.model.name") == "auto"
    assert '"input": 10' in str(attrs.get("langfuse.observation.usage_details"))
    for record in caplog.records:
        assert SENTINEL_SK not in record.getMessage()
        assert SENTINEL_PK not in record.getMessage()
    for span in spans:
        assert SENTINEL_SK not in str(span.attributes)
        assert "Add a docstring" not in str(span.attributes)


def test_worker_env_carries_langfuse_names_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from treefleet.worker.spawner import worker_env

    assert not any(k.startswith("LANGFUSE_") for k in worker_env("tok"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", SENTINEL_PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL_SK)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    env = worker_env("tok")
    assert env["LANGFUSE_PUBLIC_KEY"] == SENTINEL_PK
    assert env["LANGFUSE_BASE_URL"] == "http://127.0.0.1:9"


def test_langfuse_names_stay_out_of_run_json() -> None:
    from treefleet.sessions import SECRET_ENV_NAMES

    assert {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"} <= set(SECRET_ENV_NAMES)


class _FakeInstrumentor:
    seen: list[dict[str, Any]] = []

    def instrument(self, **kwargs: Any) -> None:
        _FakeInstrumentor.seen.append(kwargs)


def _fake_adk(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import types

    _FakeInstrumentor.seen = []
    module = types.ModuleType("openinference.instrumentation.google_adk")
    module.GoogleADKInstrumentor = _FakeInstrumentor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openinference.instrumentation.google_adk", module)
    monkeypatch.setattr(obs, "_client", object())
    return _FakeInstrumentor.seen


def test_content_is_hidden_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_adk(monkeypatch)
    assert obs.instrument_orchestrator() is True
    config = seen[0]["config"]
    for name in ("hide_inputs", "hide_outputs", "hide_input_messages", "hide_output_messages", "hide_prompts"):
        assert getattr(config, name) is True, name


def test_content_is_captured_only_when_toggle_is_exactly_one(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("0", "true", "yes", "", " 1", "11"):
        monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", value)
        assert obs.capture_content() is False, value
    seen = _fake_adk(monkeypatch)
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "1")
    assert obs.capture_content() is True
    assert obs.instrument_orchestrator() is True
    assert "config" not in seen[0]


def test_hidden_config_redacts_real_span_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from openinference.instrumentation import OITracer
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    def spans_for(config: Any) -> str:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = OITracer(provider.get_tracer("t"), config=config)
        with tracer.start_as_current_span("llm") as span:
            span.set_attribute("input.value", "PROMPT-BODY")
            span.set_attribute("output.value", "MODEL-ANSWER")
            span.set_attribute("llm.input_messages.0.message.content", "PROMPT-BODY")
            span.set_attribute("llm.output_messages.0.message.content", "MODEL-ANSWER")
            span.set_attribute("llm.model_name", "deepseek-chat")
            span.set_attribute("llm.token_count.prompt", 7)
        return str(exporter.get_finished_spans()[0].attributes)

    hidden = spans_for(obs._trace_config())
    assert "PROMPT-BODY" not in hidden and "MODEL-ANSWER" not in hidden
    assert "deepseek-chat" in hidden and "'llm.token_count.prompt': 7" in hidden
    from openinference.instrumentation import TraceConfig

    shown = spans_for(TraceConfig())
    assert "PROMPT-BODY" in shown


def test_unsupported_trace_config_field_does_not_fail_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_adk(monkeypatch)
    monkeypatch.setattr(obs, "_HIDE_FIELDS", (*obs._HIDE_FIELDS, "hide_field_from_the_future"))
    assert obs.instrument_orchestrator() is True
    assert seen[0]["config"].hide_inputs is True


def test_instrumentation_is_skipped_when_content_cannot_be_hidden(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _fake_adk(monkeypatch)
    monkeypatch.setattr(obs, "_HIDE_FIELDS", ("hide_field_from_the_future",))
    with caplog.at_level(logging.DEBUG, logger="treefleet.observability"):
        assert obs.instrument_orchestrator() is False
    assert seen == []


def test_run_trace_goal_is_not_recorded_unless_content_is_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    def run(capture: str | None) -> str:
        obs.reset_for_tests()
        if capture is None:
            monkeypatch.delenv("LANGFUSE_CAPTURE_CONTENT", raising=False)
        else:
            monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", capture)
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        # Langfuse keeps one client per public key, so each run needs its own key to get its own provider.
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"{SENTINEL_PK}-{capture}")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL_SK)
        monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
        assert obs.init_tracing("orchestrator", tracer_provider=provider)
        with obs.run_trace("run-g", "GOAL-TEXT-XYZ"):
            with obs.cursor_span(model="auto") as span:
                span.finish(duration_s=1.0, usage=CursorUsage(inputTokens=1), exit_code=0)
        return " ".join(str(s.attributes) for s in exporter.get_finished_spans())

    hidden = run(None)
    assert "run-g" in hidden and "GOAL-TEXT-XYZ" not in hidden
    shown = run("1")
    assert "run-g" in shown and "GOAL-TEXT-XYZ" in shown


def test_capture_toggle_is_forwarded_to_the_worker_but_never_to_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    from treefleet.cursor_run import _minimal_env
    from treefleet.worker.spawner import worker_env

    assert "LANGFUSE_CAPTURE_CONTENT" not in worker_env("tok")
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", SENTINEL_PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL_SK)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    assert worker_env("tok")["LANGFUSE_CAPTURE_CONTENT"] == "1"
    cursor_env = _minimal_env()
    assert not [k for k in cursor_env if k.startswith("LANGFUSE_")]
