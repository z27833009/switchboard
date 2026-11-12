"""Evaluation harness: datasets, registered suites, runner and reports."""

from switchboard.eval.datasets import load_cases
from switchboard.eval.report import ReportMeta, build_report, render_markdown, write_report
from switchboard.eval.runner import CaseRun, Selection, run_cases, select_cases
from switchboard.eval.suite import SuiteContext, create_suite

__all__ = [
    "CaseRun",
    "ReportMeta",
    "Selection",
    "SuiteContext",
    "build_report",
    "create_suite",
    "load_cases",
    "render_markdown",
    "run_cases",
    "select_cases",
    "write_report",
]
