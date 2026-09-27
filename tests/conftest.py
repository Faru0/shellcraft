import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.context import ShellContext  # noqa: E402
from core.loader import ModuleRegistry  # noqa: E402

MODULES_DIR = ROOT / "modules"


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry(MODULES_DIR)
    reg.load()
    return reg


@pytest.fixture
def ctx(registry, tmp_path, monkeypatch) -> ShellContext:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    return ShellContext(registry=registry)
