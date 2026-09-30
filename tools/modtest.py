#!/usr/bin/env python3
"""modtest — check that a ShellCraft module works with the framework.

    python tools/modtest.py modules/mymod.py            # one module (its .md/.skill sit next to it)
    python tools/modtest.py --all                       # every module in ./modules
    python tools/modtest.py modules/myip.py --network   # also run tests that need the internet
    python tools/modtest.py modules/mymod.py --preview  # show the MCP description + rendered man page

It checks the files and name, the import, the run(args, stdin) contract, runtime behavior
(no printing, no sys.exit, sane errors), the [[tests]] cases in the .skill file, the
structure of the .md and .skill files, and that every option and API key is documented.
The exit code is 1 if any check FAILs (with --strict, WARNs count too).
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import inspect
import io
import re
import shutil
import sys
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from core.stdio import utf8_stdio  # noqa: E402

import core.pipeline  # noqa: E402,F401 — registers every builtin so name clashes are detected
from core import params as params_mod  # noqa: E402
from core.builtins import BUILTINS  # noqa: E402
from core.context import to_text  # noqa: E402
from core.loader import RESERVED_NAMES, VALID_NAME, ModuleRegistry, ModuleSpec, SkillInfo  # noqa: E402
from core.modkit import ModuleError  # noqa: E402
from core.settings import SETTINGS  # noqa: E402

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
REQUIRED_SKILL_KEYS = ("summary", "when_to_use", "usage", "examples")
KNOWN_SKILL_KEYS = {"summary", "when_to_use", "usage", "args", "params", "examples", "notes", "tests"}
TEST_KEYS = {"args", "params", "stdin", "expect", "contains", "error", "network"}
MAX_SUMMARY = 200
MAX_DESCRIPTION = 3000
BAD_FLAG = "--modtest-no-such-flag"

_ADD_ARGUMENT = re.compile(r"add_argument\(([^)]*)\)", re.S)
_FLAG_LITERAL = re.compile(r"""["'](-{1,2}[A-Za-z0-9][\w-]*)["']""")


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


@dataclass
class Report:
    path: Path
    name: str
    checks: list[Check] = field(default_factory=list)
    spec: ModuleSpec | None = None
    skill_data: dict[str, Any] | None = None

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.checks.append(Check(status, name, detail))

    def count(self, status: str) -> int:
        return sum(c.status == status for c in self.checks)


@dataclass
class Outcome:
    value: Any = None
    error: BaseException | None = None
    printed: str = ""
    timed_out: bool = False


# ── running module code safely ───────────────────────────────────────────────

def invoke(fn: Callable[[list[str], str], Any], args: list[str], stdin: str, timeout: float) -> Outcome:
    """Call fn(args, stdin) in a worker thread, capturing stdout and enforcing a timeout."""
    outcome = Outcome()

    def target() -> None:
        try:
            outcome.value = fn(list(args), stdin)
        except BaseException as exc:  # noqa: BLE001 — SystemExit etc. must be reported, not raised
            outcome.error = exc

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(timeout)
    outcome.timed_out = worker.is_alive()
    outcome.printed = captured.getvalue()
    return outcome


# ── individual checks ────────────────────────────────────────────────────────

def check_name(report: Report) -> bool:
    name = report.name
    if not VALID_NAME.match(name) or name in RESERVED_NAMES:
        report.add(FAIL, "name", f"'{name}' is not a valid module name (letters, digits, _ or -; "
                                 "must start with a letter)")
        return False
    if name in BUILTINS:
        report.add(FAIL, "name", f"'{name}' is a builtin; builtins win, so this module could never run. "
                                 "Pick another name.")
        return False
    os_program = shutil.which(name)
    if os_program:
        report.add(WARN, "name", f"shadows the OS program {os_program} (the module will win)")
    else:
        report.add(PASS, "name", f"`{name}` is free")
    return True


def check_files(report: Report) -> None:
    for suffix, why in ((".md", "`man` shows nothing"), (".skill", "MCP clients get only a one-line summary")):
        path = report.path.with_suffix(suffix)
        if path.is_file():
            report.add(PASS, f"file {path.name}", "present")
        else:
            report.add(WARN, f"file {path.name}", f"missing: {why}")


def check_import(report: Report) -> bool:
    registry = ModuleRegistry(report.path.parent)
    try:
        report.spec = registry.load_file(report.name, report.path)
    except BaseException as exc:  # noqa: BLE001 — includes SystemExit at import time
        report.add(FAIL, "import", f"{type(exc).__name__}: {exc}")
        return False
    report.add(PASS, "import", "module loads like the shell loads it")
    return True


def check_contract(report: Report) -> bool:
    spec = report.spec
    try:
        inspect.signature(spec.run).bind(["x"], "")
    except TypeError:
        report.add(FAIL, "run() signature", f"run{inspect.signature(spec.run)} can't be called as run(args, stdin)")
        return False
    except ValueError:
        pass  # builtins/C functions without a signature: let the behavior checks decide
    report.add(PASS, "run() signature", "run(args, stdin)")
    options = spec.options
    source_has_flags = bool(source_flags(report.path.read_text(encoding="utf-8")))
    if options:
        labels = ", ".join(o.short or o.long for o in options)
        valued = sum(bool(o.values) for o in options)
        report.add(PASS, "Tab completion", f"{len(options)} switch(es): {labels}"
                                           + (f"; {valued} with value lists" if valued else ""))
    elif source_has_flags:
        report.add(WARN, "Tab completion", "the code defines options, but neither the .skill [[args]] nor an "
                                           ".md options table lists them, so Tab can't complete them")
    check_env_settings(report)
    if spec.module_summary:
        report.add(PASS, "SUMMARY", spec.module_summary)
    else:
        report.add(WARN, "SUMMARY", "no SUMMARY or module docstring (help/Tab completion show nothing)")
    return True


def check_env_settings(report: Report) -> None:
    """ENV_SETTINGS were validated on import; here: each variable is documented for users and AIs."""
    declared = report.spec.env_settings
    if not declared:
        return
    names = [s.name for s in declared]
    clash = [n for n in names if n.lower() in SETTINGS]
    if clash:
        report.add(FAIL, "API keys", f"{', '.join(clash)} clashes with an on/off setting name")
        return
    report.add(PASS, "API keys", ", ".join(f"{s.name} ({report.name} · {s.label})" for s in declared))
    for suffix in (".md", ".skill"):
        path = report.path.with_suffix(suffix)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        missing = [n for n in names if n not in text]
        if missing:
            report.add(WARN, f"API keys in {path.name}", f"not mentioned: {', '.join(missing)} "
                                                         f"(say how to set it: settings {missing[0]})")
        else:
            report.add(PASS, f"API keys in {path.name}", "every variable is documented")


def _judge(report: Report, label: str, outcome: Outcome, timeout: float, bad_flag: bool = False) -> None:
    if outcome.timed_out:
        report.add(FAIL, label, f"did not finish within {timeout:g}s")
    elif outcome.printed:
        report.add(FAIL, label, "printed to stdout; return your output instead "
                                f"(printing breaks pipes and MCP): {outcome.printed[:80]!r}")
    elif isinstance(outcome.error, SystemExit):
        report.add(FAIL, label, "called sys.exit(). Plain argparse does this; use core.modkit.ArgParser")
    elif isinstance(outcome.error, ModuleError):
        report.add(PASS, label, f"clean error: {outcome.error}")
    elif outcome.error is not None:
        report.add(WARN, label, f"raised {type(outcome.error).__name__}: {outcome.error}. Raise ModuleError "
                                "with a clear message instead")
    elif outcome.value is not None and not isinstance(outcome.value, str):
        report.add(FAIL, label, f"returned {type(outcome.value).__name__}; run() must return str")
    elif bad_flag:
        report.add(WARN, label, "unknown options are silently accepted (typos go unnoticed)")
    else:
        report.add(PASS, label, "returns text")


def check_behavior(report: Report, uses_network: bool, allow_network: bool, timeout: float) -> None:
    if uses_network and not allow_network:
        report.add(SKIP, "smoke tests", "module uses the network (a [[tests]] case has network = true); "
                                        "run with --network")
        return
    run = report.spec.run
    _judge(report, "run([], \"\")", invoke(run, [], "", timeout), timeout)
    _judge(report, "unknown option", invoke(run, [BAD_FLAG], "", timeout), timeout, bad_flag=True)


def check_tests(report: Report, allow_network: bool, timeout: float) -> None:
    tests = (report.skill_data or {}).get("tests")
    if not tests:
        report.add(WARN, "[[tests]]", "no test cases in the .skill file (add a few; see templates/module/template.skill)")
        return
    for i, case in enumerate(tests, start=1):
        label = f"test #{i}"
        if not isinstance(case, dict):
            report.add(FAIL, label, "each [[tests]] entry must be a table")
            continue
        args, stdin = case.get("args", []), case.get("stdin", "")
        if "params" in case:  # named values, turned into args exactly as an MCP call would be
            if "args" in case or not isinstance(case["params"], dict):
                report.add(FAIL, label, "`params` must be a table of named values, and not combined with `args`")
                continue
            try:
                args = report.spec.argv(case["params"])
            except params_mod.ParamError as exc:
                report.add(FAIL, label, f"params: {exc}")
                continue
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args) or not isinstance(stdin, str):
            report.add(FAIL, label, "`args` must be a list of strings and `stdin` a string")
            continue
        label = f"test #{i} {' '.join(args)}".rstrip()
        modes = [k for k in ("expect", "contains", "error") if k in case]
        if len(modes) != 1:
            report.add(FAIL, label, "set exactly one of: expect, contains, error")
            continue
        if case.get("network") and not allow_network:
            report.add(SKIP, label, "needs the network (run with --network)")
            continue

        outcome = invoke(report.spec.run, args, stdin, timeout)
        mode, wanted = modes[0], str(case[modes[0]])
        if outcome.timed_out or outcome.printed or isinstance(outcome.error, SystemExit):
            _judge(report, label, outcome, timeout)
        elif mode == "error":
            if isinstance(outcome.error, ModuleError) and wanted in str(outcome.error):
                report.add(PASS, label, f"error: {outcome.error}")
            elif outcome.error is not None:
                report.add(FAIL, label, f"expected a ModuleError containing {wanted!r}, "
                                        f"got {type(outcome.error).__name__}: {outcome.error}")
            else:
                report.add(FAIL, label, f"expected an error containing {wanted!r}, but it succeeded")
        elif outcome.error is not None:
            report.add(FAIL, label, f"raised {type(outcome.error).__name__}: {outcome.error}")
        else:
            got = to_text(outcome.value)
            if mode == "contains" and wanted in got:
                report.add(PASS, label, f"output contains {wanted!r}")
            elif mode == "expect" and got == wanted:
                report.add(PASS, label, "exact output matches")
            elif mode == "contains":
                report.add(FAIL, label, f"output lacks {wanted!r}; got {got[:120]!r}")
            else:
                diff = "\n".join(difflib.unified_diff(wanted.splitlines(), got.splitlines(), "expected", "got",
                                                      lineterm="", n=1))
                report.add(FAIL, label, f"output differs:\n{diff}")


def check_markdown(report: Report, flags: list[list[str]]) -> None:
    path = report.path.with_suffix(".md")
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first == f"# {report.name}":
        report.add(PASS, ".md title", first)
    else:
        report.add(FAIL, ".md title", f"the first line must be '# {report.name}', found {first[:60]!r}")
    headings = {m.lower() for m in re.findall(r"^##\s+(.+?)\s*$", text, re.M)}
    wanted = [("Synopsis", {"synopsis", "usage"}), ("Examples", {"examples", "example"})]
    if flags:
        wanted.append(("Options", {"options", "arguments", "flags"}))
    for label, names in wanted:
        if headings & names:
            report.add(PASS, f".md ## {label}", "present")
        else:
            report.add(WARN, f".md ## {label}", f"no '## {label}' section")
    try:
        to_text(Markdown(text))
        report.add(PASS, ".md renders", "man page renders")
    except Exception as exc:  # noqa: BLE001
        report.add(FAIL, ".md renders", f"{type(exc).__name__}: {exc}")


def check_skill(report: Report) -> None:
    path = report.path.with_suffix(".skill")
    if not path.is_file():
        return
    raw = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        report.add(FAIL, ".skill TOML", f"invalid TOML ({exc}). It would be used as raw text, "
                                        "and [[tests]] would be ignored")
        return
    report.skill_data = data
    report.add(PASS, ".skill TOML", "parses")

    missing = [k for k in REQUIRED_SKILL_KEYS if not data.get(k)]
    if missing:
        report.add(FAIL, ".skill keys", f"missing or empty: {', '.join(missing)}")
    else:
        report.add(PASS, ".skill keys", ", ".join(REQUIRED_SKILL_KEYS))
    unknown = sorted(set(data) - KNOWN_SKILL_KEYS)
    if unknown:
        report.add(WARN, ".skill keys", f"unknown keys {unknown} will be appended to the description as-is")

    summary = str(data.get("summary", ""))
    if summary and ("\n" in summary.strip() or len(summary) > MAX_SUMMARY):
        report.add(WARN, ".skill summary", f"keep it to one line of at most {MAX_SUMMARY} characters "
                                           f"(now {len(summary)})")
    if not isinstance(data.get("examples", []), list):
        report.add(FAIL, ".skill examples", "`examples` must be a list of strings")
    for i, arg in enumerate(data.get("args", []), start=1):
        if not isinstance(arg, dict) or not arg.get("name") or not arg.get("description"):
            report.add(WARN, ".skill [[args]]", f"entry #{i} needs both `name` and `description`")
        elif stray := sorted(set(arg) - {"name", "description", "values"}):
            report.add(FAIL, ".skill [[args]]", _misplaced(stray, "[[args]]"))
        elif "values" in arg and (not isinstance(arg["values"], list)
                                  or not all(isinstance(v, str) for v in arg["values"])):
            report.add(FAIL, ".skill [[args]]", f"entry #{i}: `values` must be a list of strings")
    params = data.get("params", [])
    if params:
        if not isinstance(params, list):
            report.add(FAIL, ".skill [[params]]", "`params` must be an array of tables ([[params]])")
        elif errors := params_mod.problems(params):
            for error in errors:
                report.add(FAIL, ".skill [[params]]", error)
        else:
            report.add(PASS, ".skill [[params]]", f"{len(params)} typed parameter(s) for the MCP input schema")
        if data.get("args"):
            report.add(WARN, ".skill [[params]]", "both [[args]] and [[params]] are declared: MCP uses [[params]], "
                                                  "Tab completion uses [[args]]. Keep just [[params]]")
    for i, case in enumerate(data.get("tests", []), start=1):
        if isinstance(case, dict) and (stray := sorted(set(case) - TEST_KEYS)):
            report.add(FAIL, ".skill [[tests]]", f"test #{i}: " + _misplaced(stray, "[[tests]]"))

    description = SkillInfo.parse(raw).to_description()
    if len(description) > MAX_DESCRIPTION:
        report.add(WARN, ".skill length", f"description is {len(description)} characters; "
                                          f"aim for under {MAX_DESCRIPTION}")
    else:
        report.add(PASS, ".skill length", f"{len(description)} characters of MCP description")


def _misplaced(keys: list[str], table: str) -> str:
    top_level = [k for k in keys if k in KNOWN_SKILL_KEYS]
    if top_level:
        return (f"{', '.join(top_level)} ended up inside a {table} table. In TOML, a key written after a "
                "table header belongs to that table: move plain `key = value` lines above the first [[args]]")
    return f"unknown key(s) {', '.join(keys)} in {table}"


def source_flags(py_text: str) -> list[list[str]]:
    """Option groups from add_argument("-x", "--long", …) calls, e.g. [["-n", "--top"], ["-i"]]."""
    groups = []
    for call in _ADD_ARGUMENT.findall(py_text):
        head = call.split("=", 1)[0]  # only positional string args name the option
        flags = _FLAG_LITERAL.findall(head)
        if flags:
            groups.append(flags)
    return groups


def check_documented(report: Report, flags: list[list[str]]) -> None:
    for suffix in (".md", ".skill"):
        path = report.path.with_suffix(suffix)
        if not path.is_file() or not flags:
            continue
        text = path.read_text(encoding="utf-8")
        undocumented = [
            " / ".join(group) for group in flags
            if not any(re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) for flag in group)
        ]
        if undocumented:
            report.add(WARN, f"options in {path.name}", f"not documented: {', '.join(undocumented)}")
        else:
            report.add(PASS, f"options in {path.name}", f"all {len(flags)} option(s) documented")


def check_param_flags(report: Report, flags: list[list[str]]) -> None:
    """Every [[params]] flag must be one the code defines, or MCP calls using it would fail."""
    params = (report.skill_data or {}).get("params")
    if not params or not flags or not isinstance(params, list):
        return
    known = {flag for group in flags for flag in group}
    unknown = [f"'{p.get('name')}' uses {p[key]}" for p in params if isinstance(p, dict)
               for key in ("flag", "short") if isinstance(p.get(key), str) and p[key] not in known]
    if unknown:
        report.add(FAIL, ".skill [[params]] flags", f"{'; '.join(unknown)}, which the code doesn't define")
    else:
        report.add(PASS, ".skill [[params]] flags", "every flag exists in the code")


# ── driver ───────────────────────────────────────────────────────────────────

def test_module(py: Path, allow_network: bool = False, timeout: float = 10.0) -> Report:
    report = Report(path=py, name=py.stem)
    if not py.is_file() or py.suffix != ".py":
        report.add(FAIL, "file", f"{py} is not a .py file")
        return report
    report.add(PASS, f"file {py.name}", "present")
    check_files(report)
    check_skill(report)  # parsed early: [[tests]] tell us whether the module uses the network
    flags = source_flags(py.read_text(encoding="utf-8"))
    check_markdown(report, flags)
    check_documented(report, flags)
    check_param_flags(report, flags)
    if not check_name(report) or not check_import(report) or not check_contract(report):
        return report
    tests = (report.skill_data or {}).get("tests") or []
    uses_network = any(isinstance(t, dict) and t.get("network") for t in tests)
    check_behavior(report, uses_network, allow_network, timeout)
    check_tests(report, allow_network, timeout)
    return report


STYLE = {PASS: "green", WARN: "yellow", FAIL: "bold red", SKIP: "dim"}
ICON = {PASS: "✓", WARN: "!", FAIL: "✗", SKIP: "-"}


def render(console: Console, report: Report) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column()
    order = {FAIL: 0, WARN: 1, SKIP: 2, PASS: 3}
    for check in sorted(report.checks, key=lambda c: order[c.status]):
        table.add_row(Text(f"{ICON[check.status]} {check.status}", style=STYLE[check.status]),
                      Text(check.name), Text(check.detail))
    verdict = "FAILED" if report.count(FAIL) else "PASSED"
    title = Text.assemble((f"{report.name} ", "bold"), (verdict, STYLE[FAIL if report.count(FAIL) else PASS]),
                          (f"  {report.count(PASS)} pass · {report.count(WARN)} warn · "
                           f"{report.count(FAIL)} fail · {report.count(SKIP)} skipped", "dim"))
    console.print(Panel(table, title=title, title_align="left", border_style="red" if report.count(FAIL) else "green"))


def preview(console: Console, report: Report) -> None:
    if report.spec is None:
        return
    console.print(Panel(Text(report.spec.description), title="MCP tool description (what AI clients read)",
                        border_style="cyan"))
    if report.spec.doc_md:
        console.print(Panel(Markdown(report.spec.doc_md), title=f"man {report.name}", border_style="cyan"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="modtest", description="Check ShellCraft modules against the framework.")
    parser.add_argument("paths", nargs="*", type=Path, help="module .py files")
    parser.add_argument("--all", action="store_true", help="test every module in ./modules")
    parser.add_argument("--network", action="store_true", help="also run checks that need the internet")
    parser.add_argument("--preview", action="store_true", help="show the MCP description and man page")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds per call (default 10)")
    opts = parser.parse_args(argv)

    paths = list(opts.paths)
    if opts.all:
        paths += sorted(p for p in (ROOT / "modules").glob("*.py") if not p.name.startswith("_"))
    if not paths:
        parser.error("give one or more module .py files, or --all")

    utf8_stdio()  # a report piped or redirected on Windows would otherwise be cp1252
    console = Console(highlight=False)
    failed = 0
    for py in paths:
        report = test_module(py, allow_network=opts.network, timeout=opts.timeout)
        render(console, report)
        if opts.preview:
            preview(console, report)
        failed += report.count(FAIL) + (report.count(WARN) if opts.strict else 0) > 0
    console.print(Text(f"{len(paths) - failed}/{len(paths)} module(s) passed", style="bold red" if failed else "bold green"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
