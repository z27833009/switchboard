"""Enforce the core dependency rule from design.md section 2.

The import-linter contract in ``pyproject.toml`` catches imports of sibling
packages and of the frameworks the project uses. This test states the rule
exactly: every top-level module imported by ``switchboard.core`` is either in
the standard library or is pydantic.
"""

import ast
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "switchboard" / "core"
ALLOWED_THIRD_PARTY = {"pydantic"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def is_allowed(module: str) -> bool:
    if module.split(".")[0] in sys.stdlib_module_names:
        return True
    if module in ALLOWED_THIRD_PARTY:
        return True
    # Core modules may import each other; nothing else under switchboard.
    return module == "switchboard.core" or module.startswith("switchboard.core.")


def test_core_imports_only_stdlib_and_pydantic() -> None:
    violations: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        for module in imported_modules(path):
            if is_allowed(module):
                continue
            violations.append(f"{path.relative_to(CORE.parent.parent.parent)} imports {module}")
    assert not violations, "core dependency rule broken:\n" + "\n".join(violations)
