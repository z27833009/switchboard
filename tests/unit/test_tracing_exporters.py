"""Exporter selection and OTLP endpoint handling (design.md section 12.2).

The default stays the PostgreSQL exporter. ``otlp`` selects the OTLP/HTTP
exporter and needs ``OTEL_EXPORTER_OTLP_ENDPOINT``; ``none`` leaves the OTel
API a no-op. The "no Jaeger" case proves a process starts with the OTLP
exporter configured while the collector is absent, which is what makes the
tracing compose profile optional.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider

from switchboard.core.config import TracingSettings
from switchboard.core.errors import ConfigError
from switchboard.tracing import (
    PostgresSpanExporter,
    TracingOptions,
    configure_tracing,
    create_span_exporter,
    load_tracing_options,
    otlp_traces_url,
    resolve_tracing_options,
)
from switchboard.tracing import setup as tracing_setup

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "config" / "platform.yaml"


def test_platform_tracing_defaults_to_postgres() -> None:
    assert TracingSettings().exporter == "postgres"


def test_platform_tracing_rejects_an_unknown_exporter() -> None:
    with pytest.raises(ValueError, match="exporter"):
        TracingSettings.model_validate({"exporter": "zipkin"})


def test_missing_tracing_block_defaults_to_postgres() -> None:
    options = resolve_tracing_options({}, {})
    assert options == TracingOptions(exporter="postgres", otlp_endpoint=None)


def test_committed_platform_file_defaults_to_postgres() -> None:
    options = load_tracing_options(PLATFORM, {})
    assert options.exporter == "postgres"
    assert options.otlp_endpoint is None


@pytest.mark.parametrize("exporter", ["postgres", "otlp", "none"])
def test_environment_can_select_every_exporter(exporter: str) -> None:
    options = load_tracing_options(
        PLATFORM,
        {
            "SWITCHBOARD_TRACING_EXPORTER": exporter,
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318",
        },
    )
    assert options.exporter == exporter
    expected = "http://jaeger:4318" if exporter == "otlp" else None
    assert options.otlp_endpoint == expected


def test_otlp_without_an_endpoint_is_a_named_error() -> None:
    with pytest.raises(ConfigError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        resolve_tracing_options({"tracing": {"exporter": "otlp"}}, {})


def test_unknown_exporter_in_the_file_is_a_named_error() -> None:
    with pytest.raises(ConfigError, match="unknown tracing exporter 'zipkin'"):
        resolve_tracing_options({"tracing": {"exporter": "zipkin"}}, {})


def test_unknown_exporter_from_the_environment_is_a_named_error() -> None:
    with pytest.raises(ConfigError, match="unknown tracing exporter 'zipkin'"):
        resolve_tracing_options(
            {"tracing": {"exporter": "${SWITCHBOARD_TRACING_EXPORTER:-postgres}"}},
            {"SWITCHBOARD_TRACING_EXPORTER": "zipkin"},
        )


def test_non_mapping_tracing_block_is_refused() -> None:
    with pytest.raises(ConfigError, match="tracing must be a mapping"):
        resolve_tracing_options({"tracing": "postgres"}, {})


def test_postgres_exporter_is_built_from_the_dsn() -> None:
    exporter = create_span_exporter(
        "postgres", dsn="postgresql://user:pass@localhost:5432/switchboard"
    )
    assert isinstance(exporter, PostgresSpanExporter)


def test_postgres_without_a_dsn_stays_off() -> None:
    assert create_span_exporter("postgres", dsn="") is None


def test_none_builds_nothing() -> None:
    assert create_span_exporter("none", dsn="postgresql://ignored") is None


def test_otlp_exporter_is_built_from_the_endpoint() -> None:
    exporter = create_span_exporter("otlp", otlp_endpoint="http://jaeger:4318")
    assert isinstance(exporter, OTLPSpanExporter)


def test_otlp_without_an_endpoint_is_refused() -> None:
    with pytest.raises(ValueError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        create_span_exporter("otlp", otlp_endpoint="")


def test_unknown_exporter_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown tracing exporter 'zipkin'"):
        create_span_exporter("zipkin")


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://jaeger:4318", "http://jaeger:4318/v1/traces"),
        ("http://jaeger:4318/", "http://jaeger:4318/v1/traces"),
        ("http://jaeger:4318/v1/traces", "http://jaeger:4318/v1/traces"),
        ("https://collector.example.com/otlp", "https://collector.example.com/otlp/v1/traces"),
    ],
)
def test_otlp_endpoint_normalizes_to_the_traces_path(endpoint: str, expected: str) -> None:
    assert otlp_traces_url(endpoint) == expected


@pytest.mark.parametrize("endpoint", ["", "   ", "jaeger:4318", "ftp://jaeger:4318"])
def test_otlp_endpoint_must_be_an_http_url(endpoint: str) -> None:
    with pytest.raises(ValueError):
        otlp_traces_url(endpoint)


def test_provider_starts_and_shuts_down_without_a_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Jaeger: construction and shutdown must not need the network."""
    installed: list[TracerProvider] = []
    monkeypatch.setattr(tracing_setup, "_HANDLE", None)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    # Keep the failed export short; the point is that no collector is needed.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.5")

    handle = configure_tracing(
        service_name="switchboard-api",
        dsn="",
        exporter="otlp",
        otlp_endpoint="http://127.0.0.1:9",
    )
    assert handle is not None
    assert handle.exporter == "otlp"
    assert installed == [handle.provider]

    with handle.provider.get_tracer("test").start_as_current_span("run"):
        pass
    # The flush may report False while the collector is down; it must not raise.
    assert handle.force_flush(0.2) in (True, False)
    handle.shutdown()
