"""Switchboard command line."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import asyncpg
import typer

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.providers.factory import create_provider
from switchboard.adapters.providers.mock import MockProvider
from switchboard.adapters.retrievers.ingest import IngestResult, ingest_directory
from switchboard.config import load_settings
from switchboard.core.config import PriceEntry, ProviderConfig, Settings
from switchboard.core.errors import ConfigError, SwitchboardError
from switchboard.core.models import AgentSpec, EvalCase, ToolSpec
from switchboard.core.ports import EvalDeps, EvalSuite, ModelProvider
from switchboard.core.pricing import lookup_price
from switchboard.data.olist import (
    DEFAULT_EXPECTED_COUNTS,
    compare_counts,
    load_directory,
    read_expected,
)
from switchboard.eval.compare import (
    DEFAULT_COMPARE_NAME,
    DEFAULT_COMPARE_REPORTS_DIR,
    DEFAULT_ORCHESTRATORS,
    DEFAULT_PRICES_PATH,
    DEFAULT_PROVIDERS,
    DEFAULT_SUITES,
    build_comparison,
    load_zero_price_providers,
    parse_not_run_reasons,
    write_comparison,
)
from switchboard.eval.cost_guard import estimate_eval_cost
from switchboard.eval.datasets import dataset_path
from switchboard.eval.gate import DEFAULT_MAX_DROP, default_report_path, evaluate_gate
from switchboard.eval.harvest import (
    DEFAULT_CANDIDATE_DIR,
    HarvestResult,
    require_database_url,
)
from switchboard.eval.harvest import (
    harvest as harvest_feedback,
)
from switchboard.eval.manifest import DEFAULT_TOOL_MANIFEST, load_tool_specs
from switchboard.eval.report import ReportMeta, build_report, write_report
from switchboard.eval.runner import (
    DEFAULT_CASE_TIMEOUT_S,
    DEFAULT_CONCURRENCY,
    CaseRun,
    run_cases,
    select_cases,
)
from switchboard.eval.suite import SuiteContext, create_suite
from switchboard.registry import EVAL_SUITES, ORCHESTRATORS, Registry

app = typer.Typer(help="Switchboard command line.", no_args_is_help=True)
data_app = typer.Typer(help="Dataset commands.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation commands.", no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(eval_app, name="eval")


@data_app.command("load-olist")
def load_olist(
    directory: Annotated[
        Path,
        typer.Argument(
            help="Directory holding the nine Olist CSV files.",
            exists=True,
            file_okay=False,
        ),
    ],
    expected: Annotated[
        Path,
        typer.Option(
            "--expected",
            help="Row count baseline. A missing file only prints the counts.",
        ),
    ] = DEFAULT_EXPECTED_COUNTS,
    database_url: Annotated[
        str,
        typer.Option(
            "--database-url",
            envvar="SWITCHBOARD_DATABASE_ADMIN_URL",
            help="Bootstrap database URL (the application role cannot write olist).",
        ),
    ] = "",
) -> None:
    """Load the Olist CSV files into schema ``olist``."""
    if not database_url:
        raise typer.BadParameter("set SWITCHBOARD_DATABASE_ADMIN_URL or pass --database-url")
    try:
        actual = asyncio.run(load_directory(directory, database_url))
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{'table':<40}{'rows':>10}")
    for name, count in actual.items():
        typer.echo(f"{name:<40}{count:>10}")

    baseline = read_expected(expected)
    if baseline is None:
        typer.echo(f"no baseline at {expected}; record these counts and commit them")
        return
    mismatches = compare_counts(actual, baseline)
    if mismatches:
        for name, (wanted, got) in mismatches.items():
            typer.echo(f"MISMATCH {name}: expected {wanted}, got {got}", err=True)
        raise typer.Exit(1)
    typer.echo(f"row counts match {expected}")


@app.command("ingest")
def ingest_documents(
    path: Annotated[
        Path,
        typer.Argument(help="Directory with markdown documents.", exists=True, file_okay=False),
    ],
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant id.")] = "default",
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Directory with the YAML config.")
    ] = Path("config"),
    embedder_name: Annotated[
        str | None,
        typer.Option("--embedder", help="Override the configured embedder name."),
    ] = None,
) -> None:
    """Ingest markdown documents into a tenant's document index."""
    settings = load_settings(config_dir)
    chosen = embedder_name or settings.platform.embedder
    try:
        embedder_config = settings.providers.embedders[chosen]
    except KeyError as exc:
        raise typer.BadParameter(f"unknown embedder {chosen!r}") from exc
    embedder = create_embedder(chosen, embedder_config)
    database_url = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
    if not database_url:
        raise typer.BadParameter("set SWITCHBOARD_DATABASE_URL")

    async def run() -> IngestResult:
        pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=4)
        try:
            return await ingest_directory(pool, embedder, path, tenant)
        finally:
            await pool.close()

    try:
        result = asyncio.run(run())
    except (ConfigError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"documents added {result.added}, updated {result.updated}, unchanged {result.unchanged}"
    )
    typer.echo(
        f"chunks written {result.chunks_written}; tenant index version {result.index_version}"
    )


def _agent_specs(settings: Settings, registry: Registry) -> list[AgentSpec]:
    try:
        tenant = settings.tenants.tenants["default"]
    except KeyError as exc:
        raise ConfigError("the eval runner needs a tenant named 'default'") from exc
    specs: list[AgentSpec] = []
    for value in registry.agents().values():
        agent = value() if isinstance(value, type) else value
        spec = getattr(agent, "spec", None)
        if isinstance(spec, AgentSpec) and spec.name in tenant.agents.allow:
            specs.append(spec)
    if not specs:
        raise ConfigError("the default tenant allows no registered agents")
    return sorted(specs, key=lambda spec: spec.name)


def _tenant_tool_specs(settings: Settings, tools: list[ToolSpec]) -> list[ToolSpec]:
    """The default tenant's read-only tools, which the eval suites offer.

    A live run filters the MCP tool list by the tenant allowlist; the eval has
    no run context, so the same filter is applied here from the config. A
    manifest tool that no demo tenant allows (``query_readonly_sql@1``) stays
    out of the routing and toolcall prompts, as it stays out of ``list_tools``.
    """
    try:
        tenant = settings.tenants.tenants["default"]
    except KeyError as exc:
        raise ConfigError("the eval runner needs a tenant named 'default'") from exc
    allowed = set(tenant.tools.allow)
    return [spec for spec in tools if spec.wire_name in allowed]


def _eval_cost_target(
    settings: Settings,
    name: str,
    *,
    record: bool,
) -> tuple[str, ProviderConfig, PriceEntry | None]:
    """The provider whose calls an eval run would actually bill.

    A plain run bills ``--provider``. A ``--record`` run drives the mock from
    the default tenant's real provider, so that provider is the billable one.
    """
    if not record:
        try:
            config = settings.providers.providers[name]
        except KeyError as exc:
            available = ", ".join(sorted(settings.providers.providers))
            raise ConfigError(f"unknown provider {name!r}; configured: {available}") from exc
        return name, config, lookup_price(settings.prices, name, config.model)
    if name != "mock":
        raise ConfigError("--record writes fixtures for the mock provider; pass --provider mock")
    try:
        recorder_name = settings.tenants.tenants["default"].provider
    except KeyError as exc:
        raise ConfigError("the eval runner needs a tenant named 'default'") from exc
    if recorder_name == "mock":
        raise ConfigError("--record needs a real provider in the default tenant")
    try:
        recorder_config = settings.providers.providers[recorder_name]
    except KeyError as exc:
        raise ConfigError(f"recorder provider {recorder_name!r} is not configured") from exc
    return (
        recorder_name,
        recorder_config,
        lookup_price(settings.prices, recorder_name, recorder_config.model),
    )


def _eval_provider(
    settings: Settings,
    registry: Registry,
    name: str,
    *,
    record: bool,
) -> tuple[ModelProvider, bool, str | None, PriceEntry | None]:
    try:
        config = settings.providers.providers[name]
    except KeyError as exc:
        available = ", ".join(sorted(settings.providers.providers))
        raise ConfigError(f"unknown provider {name!r}; configured: {available}") from exc
    price = lookup_price(settings.prices, name, config.model)

    if not record:
        # Evaluation measures the named provider; a configured fallback would
        # score a second provider under the first one's name (design 20.2).
        return (
            create_provider(registry, name, config, price, use_fallback=False),
            False,
            None,
            price,
        )

    if name != "mock":
        raise ConfigError("--record writes fixtures for the mock provider; pass --provider mock")
    if not config.fixtures:
        raise ConfigError("the mock provider needs a fixtures directory")
    try:
        recorder_name = settings.tenants.tenants["default"].provider
    except KeyError as exc:
        raise ConfigError("the eval runner needs a tenant named 'default'") from exc
    if recorder_name == "mock":
        raise ConfigError("--record needs a real provider in the default tenant")
    try:
        recorder_config = settings.providers.providers[recorder_name]
    except KeyError as exc:
        raise ConfigError(f"recorder provider {recorder_name!r} is not configured") from exc
    recorder = create_provider(
        registry,
        recorder_name,
        recorder_config,
        lookup_price(settings.prices, recorder_name, recorder_config.model),
        use_fallback=False,
    )
    provider = MockProvider(
        name="mock",
        model=config.model or recorder_config.model or "unknown",
        fixtures_dir=Path(config.fixtures),
        capabilities=config.capabilities,
        record_with=recorder,
    )
    # The report is a mock replay, so it carries no price: the fixture run made
    # no billable call in this process (design 12.4).
    return provider, True, recorder_name, price


@eval_app.command("run")
def eval_run(
    suite: Annotated[
        str,
        typer.Option(
            "--suite",
            help="Suite name: routing, toolcall, gate, rag, injection or cache.",
        ),
    ],
    provider: Annotated[
        str, typer.Option("--provider", help="Provider name from providers.yaml.")
    ] = "mock",
    orchestrator: Annotated[
        str,
        typer.Option(
            "--orchestrator",
            help=(
                "Orchestrator dimension; langgraph (default) or agent_loop for routing, "
                "toolcall and rag."
            ),
        ),
    ] = "langgraph",
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit", help="Score only the first n cases and mark the report as a smoke run."
        ),
    ] = None,
    record: Annotated[
        bool,
        typer.Option("--record", help="Record missing mock fixtures through the real provider."),
    ] = False,
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Directory with the YAML config.")
    ] = Path("config"),
    dataset_dir: Annotated[
        Path, typer.Option("--dataset-dir", help="Directory holding the dataset versions.")
    ] = Path("evals/datasets"),
    dataset_version: Annotated[
        str, typer.Option("--dataset-version", help="Dataset version folder, for example v1.")
    ] = "v1",
    split: Annotated[
        str,
        typer.Option("--split", help="auto, dev, test or all; auto scores test when present."),
    ] = "auto",
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directory for the markdown and JSON report.")
    ] = Path("evals/reports"),
    manifest: Annotated[
        Path, typer.Option("--manifest", help="Olist tool manifest for the toolcall suite.")
    ] = DEFAULT_TOOL_MANIFEST,
    concurrency: Annotated[
        int, typer.Option("--concurrency", help="Maximum cases in flight.")
    ] = DEFAULT_CONCURRENCY,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-case timeout in seconds.")
    ] = DEFAULT_CASE_TIMEOUT_S,
    confirm_cost: Annotated[
        bool,
        typer.Option(
            "--confirm-cost",
            help="Allow a paid provider after reviewing the printed cost estimate.",
        ),
    ] = False,
    cost_cap_usd: Annotated[
        float | None,
        typer.Option(
            "--cost-cap-usd",
            help="Override platform.yaml cost_cap_usd for this run.",
        ),
    ] = None,
) -> None:
    """Run one eval suite and write markdown and JSON reports."""
    started = time.perf_counter()
    try:
        settings = load_settings(config_dir)
        registry = Registry()
        available = registry.names(EVAL_SUITES)
        if suite not in available:
            raise ConfigError(
                f"unknown eval suite {suite!r}; registered: {', '.join(available) or 'none'}"
            )
        orchestrators = registry.names(ORCHESTRATORS)
        if orchestrator not in orchestrators:
            raise ConfigError(
                f"unknown orchestrator {orchestrator!r}; "
                f"registered: {', '.join(orchestrators) or 'none'}"
            )
        if orchestrator != "langgraph" and suite not in {"routing", "toolcall", "rag"}:
            raise ConfigError(
                f"the {suite!r} suite has no orchestrator dimension; "
                f"--orchestrator {orchestrator!r} is supported for routing, toolcall and rag"
            )

        manifest_tools = load_tool_specs(manifest)
        eval_suite: EvalSuite = create_suite(
            registry,
            suite,
            SuiteContext(
                dataset_root=dataset_dir,
                agent_specs=_agent_specs(settings, registry),
                tools=manifest_tools,
                max_plan_size=settings.platform.limits.max_plan_size,
                settings=settings,
                orchestrator=orchestrator,
                tenant_tools=_tenant_tool_specs(settings, manifest_tools),
                retriever_configured=settings.platform.retriever != "none",
                provider=provider,
            ),
        )
        cases: list[EvalCase] = eval_suite.load(dataset_version)
        selection = select_cases(cases, split=split, limit=limit)
        if suite in {"gate", "injection"}:
            model = "deterministic"
        elif suite == "cache":
            embedder_name = "mock" if provider == "mock" else settings.platform.embedder
            embedder_config = settings.providers.embedders[embedder_name]
            model = embedder_config.model or f"{embedder_config.adapter} (deterministic)"
        else:
            model = settings.providers.providers[provider].model or "unknown"
        chosen_threshold = None
        if suite == "cache":
            tenant_config = settings.tenants.tenants.get("default") or next(
                iter(settings.tenants.tenants.values())
            )
            chosen_threshold = tenant_config.cache.threshold

        # Print and enforce the cost guard before the provider is built, so a
        # refused run makes no network call and needs no API key.
        target_name, target_config, target_price = _eval_cost_target(
            settings, provider, record=record
        )
        cap = settings.platform.cost_cap_usd if cost_cap_usd is None else cost_cap_usd
        if cap < 0:
            raise ConfigError("--cost-cap-usd must be zero or positive")
        if target_config.adapter != "mock" and suite not in {"gate", "injection", "cache"}:
            if target_price is None:
                raise ConfigError(
                    f"provider {target_name!r} has no price entry in config/prices.yaml; "
                    "a paid run cannot be estimated"
                )
            estimate = estimate_eval_cost(
                suite=suite,
                provider=target_name,
                model=target_config.model,
                case_count=len(selection.cases),
                price=target_price,
                cap_usd=cap,
                confirmed=confirm_cost,
            )
            typer.echo(estimate.describe())
            refusal = estimate.refusal()
            if refusal is not None:
                typer.echo(f"error: {refusal}", err=True)
                raise typer.Exit(1)

        chosen_provider, recording, recorded_with, price = _eval_provider(
            settings, registry, provider, record=record
        )
        deps = EvalDeps(provider=chosen_provider)

        async def run_suite() -> list[CaseRun]:
            try:
                return await run_cases(
                    eval_suite,
                    selection.cases,
                    deps,
                    concurrency=concurrency,
                    timeout_s=timeout,
                )
            finally:
                aclose = getattr(eval_suite, "aclose", None)
                if callable(aclose):
                    await aclose()

        results = asyncio.run(run_suite())
        elapsed = time.perf_counter() - started
        report = build_report(
            ReportMeta(
                suite=suite,
                dataset_version=dataset_version,
                dataset_path=dataset_path(suite, dataset_version, dataset_dir).as_posix(),
                split=selection.split,
                orchestrator=orchestrator,
                provider=provider,
                model=model,
                generated_at=datetime.now(UTC),
                wall_time_s=elapsed,
                dataset_case_count=selection.dataset_case_count,
                split_case_count=selection.split_case_count,
                smoke=selection.limited,
                recording=recording,
                recorded_with=recorded_with,
                price=price,
                price_verified_on=settings.prices.verified_on if price is not None else None,
                chosen_threshold=chosen_threshold,
            ),
            results,
        )
        markdown_path, json_path = write_report(report, output_dir)
    except SwitchboardError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"suite {suite}, provider {provider}, model {model}, "
        f"dataset {dataset_version} split {selection.split}"
    )
    typer.echo(
        f"scored {len(selection.cases)} of {selection.split_case_count} cases in the split "
        f"({selection.dataset_case_count} in the dataset)"
    )
    if selection.limited:
        typer.echo("SMOKE RUN: --limit was used, this is not a full-suite result")
    if recording:
        typer.echo(f"recorded missing fixtures with {recorded_with}")
    for name, value in report["metrics"].items():
        typer.echo(f"{name}: {value}")
    if report["cost_usd"] is None:
        typer.echo("cost_usd: none (no price entry for this provider)")
    elif report["no_api_cost"]:
        typer.echo(f"cost_usd: {report['cost_usd']} (no API cost, local provider)")
    else:
        typer.echo(f"cost_usd: {report['cost_usd']}")
    typer.echo(
        f"latency p50 {report['latency_ms']['p50']} ms, p95 {report['latency_ms']['p95']} ms"
    )
    typer.echo(f"wall time {elapsed:.2f} s")
    typer.echo(f"wrote {markdown_path}")
    typer.echo(f"wrote {json_path}")


@eval_app.command("compare")
def eval_compare(
    reports_dir: Annotated[
        Path,
        typer.Option("--reports-dir", help="Directory holding the per-suite JSON reports."),
    ] = DEFAULT_COMPARE_REPORTS_DIR,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for the comparison markdown and JSON."),
    ] = DEFAULT_COMPARE_REPORTS_DIR,
    name: Annotated[
        str, typer.Option("--name", help="File name stem for the comparison output.")
    ] = DEFAULT_COMPARE_NAME,
    prices: Annotated[
        Path,
        typer.Option(
            "--prices",
            help="Price table used only to label a report written before it existed.",
        ),
    ] = DEFAULT_PRICES_PATH,
    suite: Annotated[
        list[str] | None,
        typer.Option("--suite", help="Suite to include; repeat for several."),
    ] = None,
    provider: Annotated[
        list[str] | None,
        typer.Option("--provider", help="Provider dimension; repeat for several."),
    ] = None,
    orchestrator: Annotated[
        list[str] | None,
        typer.Option("--orchestrator", help="Orchestrator dimension; repeat for several."),
    ] = None,
    not_run_reason: Annotated[
        list[str] | None,
        typer.Option(
            "--not-run-reason",
            help="provider=reason for a combination with no report, for example "
            "'deepseek=DEEPSEEK_API_KEY missing'.",
        ),
    ] = None,
) -> None:
    """Merge per-suite reports into one provider by orchestrator table (AC-13a).

    The command only reads reports and writes ``<name>.md`` and ``<name>.json``;
    it never rewrites a per-suite report or a baseline. A combination with no
    report is marked ``not run`` and can carry the reason passed with
    ``--not-run-reason``, so a pending paid provider is visible rather than
    silently absent.
    """
    try:
        if not name.strip() or Path(name).name != name:
            raise ConfigError("--name must be a plain file name without a directory")
        suites = tuple(dict.fromkeys(suite or DEFAULT_SUITES))
        providers = tuple(dict.fromkeys(provider or DEFAULT_PROVIDERS))
        orchestrators = tuple(dict.fromkeys(orchestrator or DEFAULT_ORCHESTRATORS))
        if not suites or not providers or not orchestrators:
            raise ConfigError("--suite, --provider and --orchestrator must not be empty")
        known = Registry().names(ORCHESTRATORS)
        unknown = [value for value in orchestrators if value not in known]
        if unknown:
            raise ConfigError(
                f"unknown orchestrator {unknown[0]!r}; registered: {', '.join(known) or 'none'}"
            )
        comparison = build_comparison(
            reports_dir=reports_dir,
            suites=suites,
            providers=providers,
            orchestrators=orchestrators,
            not_run_reasons=parse_not_run_reasons(not_run_reason),
            zero_price_providers=load_zero_price_providers(prices),
        )
        markdown_path, json_path = write_comparison(comparison, output_dir, name=name.strip())
    except SwitchboardError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"comparison suites {', '.join(suites)}; providers {', '.join(providers)}; "
        f"orchestrators {', '.join(orchestrators)}"
    )
    typer.echo(
        f"measured {comparison['measured']} of {len(comparison['rows'])} combinations; "
        f"not run {comparison['not_run']}; smoke {comparison['smoke']}"
    )
    for warning in comparison["warnings"]:
        typer.echo(f"warning: {warning}")
    typer.echo(f"wrote {markdown_path}")
    typer.echo(f"wrote {json_path}")


@eval_app.command("gate")
def eval_gate(
    suite: Annotated[str, typer.Option("--suite", help="Suite name: routing or toolcall.")],
    baseline: Annotated[
        Path,
        typer.Option(
            "--baseline",
            help="Committed baseline JSON, for example evals/baselines/routing.json.",
            exists=True,
            dir_okay=False,
        ),
    ],
    max_drop: Annotated[
        float,
        typer.Option(
            "--max-drop",
            help="Largest allowed drop from the baseline before the gate fails.",
        ),
    ] = DEFAULT_MAX_DROP,
    report: Annotated[
        Path | None,
        typer.Option(
            "--report",
            help="Current report JSON; defaults to evals/reports/<suite>-mock.json.",
        ),
    ] = None,
) -> None:
    """Compare a report against a committed baseline and fail on a regression."""
    report_path = report if report is not None else default_report_path(suite)
    try:
        result = evaluate_gate(
            suite=suite,
            baseline_path=baseline,
            report_path=report_path,
            max_drop=max_drop,
        )
    except SwitchboardError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"suite {result.suite}, metric {result.metric}")
    typer.echo(f"baseline {result.baseline:.4f} ({baseline})")
    typer.echo(f"current {result.current:.4f} ({report_path})")
    typer.echo(f"drop {result.drop:.4f}, allowed {result.max_drop:.4f}")
    if not result.passed:
        typer.echo(
            f"gate failed: {result.metric} dropped {result.drop:.4f}, "
            f"more than the allowed {result.max_drop:.4f}",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo("gate passed")


@eval_app.command("harvest")
def eval_harvest(
    since: Annotated[
        datetime,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help="Harvest feedback written on or after this UTC date (YYYY-MM-DD).",
        ),
    ],
    tenant: Annotated[
        str | None,
        typer.Option("--tenant", help="Only this tenant's feedback; default is every tenant."),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory for the unlabeled candidate JSONL file."),
    ] = DEFAULT_CANDIDATE_DIR,
    database_url: Annotated[
        str,
        typer.Option(
            "--database-url",
            envvar="SWITCHBOARD_DATABASE_URL",
            help="Application-role database URL that can read feedback and runs.",
        ),
    ] = "",
) -> None:
    """Write negatively rated runs to a candidate file (AC-24b).

    Only the run's newest feedback row counts, and only a rating of -1 is
    exported. The file name carries the since date and the tenant, and the file
    is rewritten on every run, so a second harvest writes the same bytes rather
    than a duplicate. Frozen datasets and baselines are never touched.
    """
    try:
        url = require_database_url(database_url)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    async def run_harvest() -> HarvestResult:
        pool = await asyncpg.create_pool(dsn=url, min_size=1, max_size=2)
        try:
            return await harvest_feedback(pool, since=since, tenant_id=tenant, directory=Path(out))
        finally:
            await pool.close()

    try:
        result = asyncio.run(run_harvest())
    except (SwitchboardError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"feedback rows since {result.since.date().isoformat()}: {result.feedback_rows}")
    typer.echo(f"tenant: {result.tenant_id or 'all tenants'}")
    typer.echo(f"negative candidates: {result.candidates}")
    typer.echo(f"wrote {result.path}")


if __name__ == "__main__":
    app()
