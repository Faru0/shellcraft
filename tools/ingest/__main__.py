"""ingest — turn a Python script into a ShellCraft module, step by step.

    python -m tools.ingest                          # asks for everything
    python -m tools.ingest path/to/script.py        # start with this script
    python -m tools.ingest check modules/x.py       # only check a module (also: --all, --strict, --network)
    python ~/shellcraft/tools/ingest script.py      # the same, from any directory

The wizard asks for a module name, analyzes the script, and shows exactly what has to change so
ShellCraft can call it (a run(args, stdin) function, the script's code indented into it, and a few
small edits). You then apply the changes automatically, do them by hand from the instructions,
or let an AI (Claude or OpenAI) do them. Only the changes that are needed are made; if something
in the script can't be converted safely, the wizard stops and says what to fix by hand. Finally it
writes the .md (man page) and .skill (MCP description) and checks the result.

Your original script is never modified: the module is written to modules/<name>.py.
"""

from __future__ import annotations

import argparse
import getpass
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # also runnable from anywhere as: python <shellcraft>/tools/ingest
    sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.text import Text

from core.stdio import utf8_stdio
from tools.ingest import ai, docs
from tools.ingest.analyze import GUARD, KEEP, Analysis, analyze
from tools.ingest.check import FAIL, WARN, check_module, name_problem, render
from tools.ingest.convert import Conversion, ConvertError, convert, extra_changes

console = Console(highlight=False)


class Stop(Exception):
    """End the wizard early with this exit code (the reason was already shown)."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


# ── small UI helpers ────────────────────────────────────────────────────────

def say(text: str = "", style: str = "") -> None:
    console.print(Text(text, style=style) if style else text, soft_wrap=True)


def step(n: int, title: str) -> None:
    console.print()
    console.rule(Text(f"{n}. {title}", style="bold cyan"), align="left")


def show_diff(diff: str) -> None:
    if diff.strip():
        console.print(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True))
    else:
        say("(no changes)", "dim")


def default_name(path: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)
    return name if name[:1].isalpha() else f"m_{name}"


# ── wizard steps ────────────────────────────────────────────────────────────

def ask_script(given: str | None) -> Path | None:
    while True:
        raw = given or Prompt.ask("Path to your Python script")
        given = None
        path = Path(raw.strip().strip("'\"")).expanduser()
        if path.is_file() and path.suffix == ".py":
            return path.resolve()
        say(f"{path} is not an existing .py file.", "red")
        if not Confirm.ask("Try another path?", default=True):
            return None


def ask_name(script: Path, modules: Path, given: str | None) -> tuple[str, Path] | None:
    while True:
        name = (given or Prompt.ask("What should the command be called?", default=default_name(script))).strip()
        given = None
        if problem := name_problem(name):
            say(problem, "red")
            continue
        if program := shutil.which(name):
            say(f"Note: `{name}` is also the program {program}. Inside ShellCraft the module wins.", "yellow")
            if not Confirm.ask("Use this name anyway?", default=False):
                continue
        target = modules / f"{name}.py"
        if target.resolve() == script:
            say("Your script is already in the modules folder under this name. It will be changed in place; "
                f"a copy of the original is kept as {name}.py.orig.", "yellow")
            if not Confirm.ask("Continue?", default=False):
                continue
        elif target.exists():
            say(f"{target} already exists.", "yellow")
            if not Confirm.ask("Overwrite it?", default=False):
                continue
        return name, target


def summarize(analysis: Analysis) -> None:
    moved = sum(what != KEEP for _, what in analysis.plan)
    facts = [
        ("already a module", "yes: it has run(args, stdin)") if analysis.style == "module"
        else ("top-level statements", f"{len(analysis.plan)} ({moved} move into run())"),
        ("options found", ", ".join(o.long or o.short or o.dest for o in analysis.options) or "none"),
        ("reads stdin", "yes" if analysis.uses_stdin else "no"),
        ("network", "yes" if analysis.uses_network else "no"),
        ("writes files", "yes" if analysis.writes_files else "no"),
        ("API keys", ", ".join(analysis.env_vars) or "none"),
    ]
    for label, value in facts:
        console.print(Text.assemble((f"  {label:<22}", "dim"), value))
    for note in analysis.notes:
        say(f"  note: {note}", "yellow")


def report_conflicts(analysis: Analysis, extra: str | None = None) -> None:
    lines = Text()
    for c in analysis.conflicts:
        lines.append(f"line {c.line}: ", style="bold")
        lines.append(c.message + "\n")
    if extra:
        lines.append(extra + "\n")
    lines.rstrip()
    console.print(Panel(lines, title="Conflicts: these must be fixed by hand", border_style="red",
                        title_align="left"))
    say("Nothing was changed or written. Fix the lines above in your script, then run the wizard again.",
        "bold")


def choose_provider() -> tuple[ai.Provider, str, str] | None:
    """(provider, api key, model), asking for whatever isn't known."""
    choice = Prompt.ask("Which AI?", choices=list(ai.PROVIDERS), default="claude")
    provider = ai.PROVIDERS[choice]
    key = ai.stored_key(provider)
    if key:
        say(f"Using the {provider.env} key from your environment/settings.", "dim")
    else:
        key = getpass.getpass(f"{provider.env} (input hidden; used for this run only, not saved): ").strip()
        if not key:
            say("No key given.", "red")
            return None
    return provider, key, ai.model_for(provider, OPTIONS.model)


def ask_ai(provider: ai.Provider, key: str, model: str, prompt: str, what: str) -> str | None:
    say(f"This sends {what} to {provider.label} ({model}).", "yellow")
    if not Confirm.ask("Send it?", default=True):
        return None
    with console.status(f"waiting for {provider.label}…"):
        try:
            return ai.complete(provider, key, model, prompt)
        except ai.AIError as exc:
            say(f"AI request failed: {exc}", "red")
            return None


def apply_changes(analysis: Analysis, name: str, script: Path) -> str:
    """The new module source; raises Stop for manual mode or a conflict."""
    try:
        conversion = convert(analysis, name)
    except ConvertError as exc:
        report_conflicts(analysis, str(exc))
        raise Stop(1) from None

    say("To make your script work as a ShellCraft command, these changes are needed:\n")
    for i, text in enumerate(conversion.steps, start=1):
        console.print(Text.assemble((f" {i}. ", "bold"), text))
    say()
    show_diff(conversion.diff(analysis.source, name))
    say("\nprint() output becomes the command's output and sys.exit() becomes a clean error, through "
        "@script — so the rest of your code stays exactly as it is.", "dim")

    while True:
        say("\nHow do you want to apply them?\n"
            "  auto    make exactly these changes now (recommended)\n"
            "  manual  I'll edit the script myself (saves a .patch file too)\n"
            "  ai      send the script to Claude or OpenAI to make the changes")
        mode = Prompt.ask("Choose", choices=["auto", "manual", "ai"], default="auto")
        if mode == "auto":
            return conversion.source
        if mode == "manual":
            patch = Path.cwd() / f"{name}.patch"
            patch.write_text(conversion.diff(analysis.source, name), encoding="utf-8")
            say(f"\nSaved the changes as {patch} (apply with: patch -p1 < {patch.name}, or by hand from the "
                "numbered list above).", "bold")
            say(f"When you're done, run:  python -m tools.ingest {script}  again. It will then see a ready "
                "module and go straight to the docs and the check.")
            raise Stop(0)
        chosen = choose_provider()
        if chosen is None:
            continue
        prompt = ai.convert_prompt(analysis.source, name, conversion.steps, analysis.import_name)
        reply = ask_ai(*chosen, prompt, "your script's source code")
        if reply is None:
            continue
        try:
            candidate = ai.parse_code(reply)
            compile(candidate, f"{name}.py", "exec")
        except (ai.AIError, SyntaxError) as exc:
            say(f"The AI's answer can't be used: {exc}", "red")
            continue
        if problems := extra_changes(analysis.source, candidate, conversion.source):
            console.print(Panel("\n".join(problems[:30]), title="The AI changed more than it was asked to",
                                border_style="red", title_align="left"))
            say("Refusing to use it: only the listed changes are allowed. Pick auto or manual instead.", "bold")
            continue
        say("The AI's version makes only the requested changes:")
        show_diff(Conversion(candidate).diff(analysis.source, name))
        return candidate


def write_docs(analysis: Analysis, name: str, source: str, modules: Path) -> None:
    md_path, skill_path = modules / f"{name}.md", modules / f"{name}.skill"
    md, skill = docs.markdown(analysis, name), docs.skill(analysis, name)
    for path, text in ((md_path, md), (skill_path, skill)):
        if path.exists() and not Confirm.ask(f"{path.name} already exists. Replace it with a new draft?",
                                             default=False):
            continue
        path.write_text(text, encoding="utf-8")
        say(f"wrote {path}", "green")

    if not Confirm.ask("Have an AI turn the drafts into finished docs (better wording, examples and tests)?",
                       default=False):
        say(f"Review the TODO lines in {skill_path.name} when you have a moment.", "dim")
        return
    chosen = choose_provider()
    if chosen is None:
        return
    current_md = md_path.read_text(encoding="utf-8") if md_path.exists() else md
    current_skill = skill_path.read_text(encoding="utf-8") if skill_path.exists() else skill
    reply = ask_ai(*chosen, ai.docs_prompt(source, name, current_md, current_skill), "the module's source code")
    if reply is None:
        return
    try:
        new_md, new_skill = ai.parse_docs(reply)
        tomllib.loads(new_skill)
        if next((ln.strip() for ln in new_md.splitlines() if ln.strip()), "") != f"# {name}":
            raise ai.AIError(f"the .md doesn't start with '# {name}'")
    except (ai.AIError, tomllib.TOMLDecodeError) as exc:
        say(f"The AI's docs can't be used ({exc}); keeping the drafts.", "red")
        return
    console.print(Panel(Syntax(new_skill, "toml", theme="ansi_dark", word_wrap=True), title=skill_path.name))
    if Confirm.ask("Save the AI's .md and .skill?", default=True):
        md_path.write_text(new_md, encoding="utf-8")
        skill_path.write_text(new_skill, encoding="utf-8")
        say(f"wrote {md_path} and {skill_path}", "green")


def wizard(script_arg: str | None, name_arg: str | None, modules: Path) -> int:
    console.print(Panel("Turn a Python script into a ShellCraft command.\n"
                        "Your original file is never changed; the module goes to "
                        f"{modules}.", title="ShellCraft ingest", border_style="cyan", title_align="left"))
    step(1, "Your script")
    script = ask_script(script_arg)
    if script is None:
        return 1
    try:
        source = script.read_text(encoding="utf-8")
        analysis = analyze(script, source)
    except (UnicodeDecodeError, SyntaxError) as exc:
        say(f"Can't read {script} as Python: {exc}", "red")
        return 1

    step(2, "Name")
    picked = ask_name(script, modules, name_arg)
    if picked is None:
        return 1
    name, target = picked

    step(3, "Analysis")
    summarize(analysis)
    if analysis.conflicts:
        report_conflicts(analysis)
        return 1

    step(4, "Code changes")
    if analysis.style == "module":
        say("It already has run(args, stdin): no code changes needed.", "green")
        new_source = source
    else:
        new_source = apply_changes(analysis, name, script)
    if new_source != source or target.resolve() != script:
        if not Confirm.ask(f"Write the module to {target}?", default=True):
            say("Nothing written.")
            return 1
        modules.mkdir(parents=True, exist_ok=True)
        if target.resolve() == script:
            target.with_name(f"{name}.py.orig").write_text(source, encoding="utf-8")
        target.write_text(new_source if new_source.endswith("\n") else new_source + "\n", encoding="utf-8")
        say(f"wrote {target}", "green")

    step(5, "Documentation")
    write_docs(analysis, name, new_source, modules)

    step(6, "Check")
    smoke = Confirm.ask('Run a quick test call, run([], ""), as part of the check? It executes your code.',
                        default=True)
    report = check_module(target, smoke=smoke)
    render(console, report)
    say()
    if report.failed:
        say("Fix the FAIL lines above, then re-check with:", "bold")
    else:
        say("Done. Start ShellCraft (or type `reload` in a running one), then try:", "bold")
        say(f"  {name} …        man {name}")
        if analysis.style != "module" and any(w == GUARD for _, w in analysis.plan):
            say("Note: the module no longer runs as `python file.py` on its own; your original script still does.",
                "dim")
        say("Re-check any time with:", "bold")
    say(f"  python -m tools.ingest check {target}")
    return 1 if report.failed else 0


# ── command line ────────────────────────────────────────────────────────────

OPTIONS = argparse.Namespace(model=None)


def check_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ingest check",
                                     description="Check ShellCraft modules against the framework.")
    parser.add_argument("paths", nargs="*", type=Path, help="module .py files")
    parser.add_argument("--all", action="store_true", help="check every module in ./modules")
    parser.add_argument("--network", action="store_true", help="also run tests that need the internet")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds per call (default 10)")
    opts = parser.parse_args(argv)
    paths = list(opts.paths)
    if opts.all:
        paths += sorted(p for p in (ROOT / "modules").glob("*.py") if not p.name.startswith("_"))
    if not paths:
        parser.error("give one or more module .py files, or --all")
    failed = 0
    for py in paths:
        report = check_module(py, allow_network=opts.network, timeout=opts.timeout)
        render(console, report)
        failed += report.count(FAIL) + (report.count(WARN) if opts.strict else 0) > 0
    say(f"{len(paths) - failed}/{len(paths)} module(s) passed", "bold red" if failed else "bold green")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    utf8_stdio()  # a report piped or redirected on Windows would otherwise be cp1252
    if argv[:1] == ["check"]:
        return check_main(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m tools.ingest", description=__doc__.splitlines()[0])
    parser.add_argument("script", nargs="?", help="the Python script to turn into a module")
    parser.add_argument("--name", help="the command name (asked for when left out)")
    parser.add_argument("--modules", type=Path, default=ROOT / "modules", help="where modules go (default ./modules)")
    parser.add_argument("--model", help="AI model to use instead of the default")
    opts = parser.parse_args(argv)
    OPTIONS.model = opts.model
    try:
        return wizard(opts.script, opts.name, opts.modules.resolve())
    except Stop as stop:
        return stop.code
    except (KeyboardInterrupt, EOFError):
        say("\nStopped. Nothing more was written.", "yellow")
        return 130


if __name__ == "__main__":
    sys.exit(main())
