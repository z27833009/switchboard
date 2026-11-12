"""Switchboard command line."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from switchboard.data.olist import (
    DEFAULT_EXPECTED_COUNTS,
    compare_counts,
    load_directory,
    read_expected,
)

app = typer.Typer(help="Switchboard command line.", no_args_is_help=True)
data_app = typer.Typer(help="Dataset commands.", no_args_is_help=True)
app.add_typer(data_app, name="data")


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


if __name__ == "__main__":
    app()
