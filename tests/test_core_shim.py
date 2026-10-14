"""The `core` compatibility shim (the package is `shellcraft` since 0.3.0)."""

import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_names_are_the_shellcraft_modules():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import core
        import core.modkit
        from core.modkit import ArgParser, ModuleError
        from core.pipeline import run_line as old_run_line

    import shellcraft
    import shellcraft.modkit
    from shellcraft.pipeline import run_line

    assert core.modkit is shellcraft.modkit and ArgParser is shellcraft.modkit.ArgParser
    assert ModuleError is shellcraft.modkit.ModuleError and old_run_line is run_line
    assert core.__version__ == shellcraft.__version__


def test_an_old_module_still_loads(tmp_path):
    from shellcraft.loader import ModuleRegistry

    (tmp_path / "old.py").write_text("from core.modkit import ArgParser, ModuleError\n\n"
                                     "def run(args, stdin):\n    ArgParser('old').parse_args(args)\n    return 'still works'\n")
    reg = ModuleRegistry(tmp_path)
    reg.load()
    assert reg.warnings == [] and reg.get("old").run([], "") == "still works"


def test_python_m_core_cli_and_core_alerts_still_run(tmp_path):
    env = {"PYTHONPATH": str(ROOT), "SHELLCRAFT_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-m", "core.cli", "--version"], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout.startswith("ShellCraft ")
    alert = subprocess.run([sys.executable, "-m", "core.alerts", "hello"], capture_output=True, text=True, env=env)
    assert alert.returncode == 0 and list((tmp_path / "alerts").glob("*.json"))
