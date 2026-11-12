"""Scaffold check: the package and its framework-free core are importable."""

import switchboard
import switchboard.core


def test_package_imports() -> None:
    assert switchboard.__version__
    assert switchboard.core.__name__ == "switchboard.core"
