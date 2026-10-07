"""Read a Python script and work out what ShellCraft needs from it.

Everything here is static (ast + symtable): the script is never imported or run. The result says
which top-level statements stay where they are and which move into run(), which small edits make
it read `args`/`stdin` instead of sys.argv/sys.stdin, what options and errors it has (for the
docs), and every CONFLICT: something that can't be converted safely and must be fixed by hand.
"""

from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEEP, MOVE, GUARD = "keep", "move", "guard"  # what happens to each top-level statement

INTERACTIVE = {"input", "getpass"}  # need a terminal: there is none in a pipe or an MCP call
GUI_MODULES = {"tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "pygame", "curses", "kivy"}
NETWORK_MODULES = {"requests", "urllib", "http", "socket", "httpx", "aiohttp", "urllib3", "ftplib", "smtplib"}
UNSAFE_CALLS = {
    ("os", "_exit"): "os._exit() kills the whole shell or MCP server, not just this command",
    ("os", "fork"): "os.fork() in a module would fork the whole shell",
    ("signal", "signal"): "signal.signal() only works on the main thread; modules run in a worker thread",
}


@dataclass
class Conflict:
    line: int
    message: str


@dataclass
class Edit:
    """Replace source text (one line only) — `col`/`end_col` are character offsets."""

    line: int
    col: int
    end_col: int
    new: str
    why: str


@dataclass
class Option:
    flags: list[str]  # ["-n", "--top"]; empty for a positional
    dest: str  # snake_case name
    kind: str = "string"  # string | integer | number | boolean | array
    metavar: str = ""
    choices: list[str] = field(default_factory=list)
    default: Any = None
    help: str = ""
    required: bool = False

    @property
    def positional(self) -> bool:
        return not self.flags

    @property
    def short(self) -> str | None:
        return next((f for f in self.flags if not f.startswith("--")), None)

    @property
    def long(self) -> str | None:
        return next((f for f in self.flags if f.startswith("--")), None)


@dataclass
class Analysis:
    path: Path
    source: str
    tree: ast.Module
    style: str = "script"  # "module" (already has run(args, stdin)) | "script"
    plan: list[tuple[ast.stmt, str]] = field(default_factory=list)  # (statement, KEEP | MOVE | GUARD)
    edits: list[Edit] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    docstring: str = ""
    description: str = ""
    options: list[Option] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    uses_network: bool = False
    writes_files: bool = False
    uses_stdin: bool = False
    import_name: str = "script"  # how the @script decorator is imported (renamed on a clash)

    def conflict(self, node: ast.AST | int, message: str) -> None:
        line = node if isinstance(node, int) else getattr(node, "lineno", 0)
        self.conflicts.append(Conflict(line, message))


# ── small AST helpers ───────────────────────────────────────────────────────

def dotted(node: ast.AST) -> str:
    """'sys.argv' for Attribute(Name('sys'), 'argv'); '' when it isn't a plain dotted name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def text_of(node: ast.AST | None) -> str:
    """A string literal, or an f-string with its {placeholders} kept as text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                out.append("{" + ast.unparse(part.value) + "}")
        return "".join(out)
    return ""


def is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return False
    sides = [test.left, test.comparators[0]]
    names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
    mains = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
    return bool(names and mains)


def mentions_main_guard(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and any(
        isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test))


def bound_names(node: ast.stmt) -> set[str]:
    """Names a top-level statement binds at module level."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in node.names if a.name != "*"}
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            names.add(sub.name)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            names.add(sub.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in sub.names if a.name != "*"}
    return names


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def globals_used_by_defs(source: str, path: Path, top_level: set[int]) -> dict[str, int]:
    """Module-level names that the top-level functions and classes (by line) read or declare
    `global`, with a line hint. Functions nested in code that moves into run() become closures,
    so their names don't count."""
    table = symtable.symtable(source, str(path), "exec")
    used: dict[str, int] = {}

    def walk(t: symtable.SymbolTable, line: int) -> None:
        for sym in t.get_symbols():
            if sym.is_global() or sym.is_declared_global():
                used.setdefault(sym.get_name(), line)
            elif t.get_type() == "class" and sym.is_referenced() and not sym.is_assigned():
                used.setdefault(sym.get_name(), line)
        for child in t.get_children():
            walk(child, line)

    for child in table.get_children():
        if child.get_lineno() in top_level:
            walk(child, child.get_lineno())
    return used


def is_docstring(node: ast.stmt, index: int) -> bool:
    return index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
        and isinstance(node.value.value, str)


def run_signature_ok(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if isinstance(fn, ast.AsyncFunctionDef):
        return False
    a = fn.args
    positional = a.posonlyargs + a.args
    required = len(positional) - len(a.defaults)
    return (len(positional) >= 2 or a.vararg is not None) and required <= 2 \
        and not [k for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]


# ── the analysis ────────────────────────────────────────────────────────────

def analyze(path: Path, source: str | None = None) -> Analysis:
    source = path.read_text(encoding="utf-8") if source is None else source
    tree = ast.parse(source, filename=str(path))  # SyntaxError is the caller's to report
    result = Analysis(path=path, source=source, tree=tree)
    result.docstring = (ast.get_docstring(tree) or "").strip()
    _collect_docs_facts(result)

    body = tree.body
    run_def = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"),
                   None)
    if run_def is not None:
        if run_signature_ok(run_def):
            result.style = "module"
            result.plan = [(n, KEEP) for n in body]
            params = run_def.args.posonlyargs + run_def.args.args
            result.uses_stdin = len(params) > 1 and params[1].arg in loaded_names(run_def)
            _check_unsafe(result, moved=set())
            return result
        result.conflict(run_def, "the script already defines run() with a different signature. ShellCraft calls "
                                 "run(args, stdin); rename your function (e.g. to run_job) first")
    for node in body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "run" in bound_names(node):
            result.conflict(node, "the name `run` is already used at module level (often `from subprocess import "
                                  "run`). Adding def run() would replace it; import it under another name first")
    if any("script" in bound_names(n) for n in body):
        result.import_name = "shellcraft_script"

    _plan(result)
    moved = {id(n) for n, what in result.plan if what != KEEP}
    _check_unsafe(result, moved)
    _plan_edits(result, moved)
    if not any(what != KEEP for _, what in result.plan) and not result.conflicts:
        result.conflict(1, "the script defines things but never runs anything at the top level (no code and no "
                           "`if __name__ == \"__main__\":` block), so there is nothing to put in run()")
    result.conflicts.sort(key=lambda c: c.line)
    return result


def _plan(result: Analysis) -> None:
    """Decide KEEP / MOVE / GUARD for each top-level statement."""
    body = result.tree.body
    defs = {n.lineno for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    used = globals_used_by_defs(result.source, result.path, defs)
    kept_names: set[str] = set()
    kept_vars: set[str] = set()  # bound by kept assignments (not imports or defs)
    plan: list[tuple[ast.stmt, str]] = []
    for i, node in enumerate(body):
        if is_docstring(node, i) or isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                                      ast.AsyncFunctionDef, ast.ClassDef)):
            what = KEEP
        elif is_main_guard(node):
            what = GUARD if not node.orelse else MOVE
            if node.orelse:
                result.conflict(node, "the `if __name__ == \"__main__\":` block has an else branch; "
                                      "remove the else part so the block can become run()")
        elif mentions_main_guard(node):
            what = MOVE
            result.conflict(node, "this `if` tests __name__ in an unusual way. Inside a module __name__ is never "
                                  "\"__main__\", so it can't be converted automatically; make it a plain "
                                  "`if __name__ == \"__main__\":` block")
        elif isinstance(node, ast.Try) and all(isinstance(s, (ast.Import, ast.ImportFrom, ast.Pass, ast.Assign))
                                               for s in node.body + [h for x in node.handlers for h in x.body]):
            what = KEEP  # try: import x / except ImportError: x = None
        elif _is_definition(node, used, kept_vars):
            what = KEEP
            kept_vars |= bound_names(node)
        else:
            what = MOVE
        if what == KEEP:
            kept_names |= bound_names(node)
        plan.append((node, what))
    result.plan = plan

    # Code that moves into run() binds local names. A function that reads one as a global would break.
    moved_binds: dict[str, ast.stmt] = {}
    for node, what in plan:
        if what != KEEP:
            stmts = node.body if what == GUARD else [node]
            for stmt in stmts:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    continue  # importing again inside run() is harmless
                for name in bound_names(stmt):
                    moved_binds.setdefault(name, stmt)
    for name, stmt in moved_binds.items():
        if name in used:
            result.conflict(stmt, f"`{name}` is set here, in code that moves into run(), but the function or class "
                                  f"at line {used[name]} reads it as a global. Pass it in as a parameter, or set it "
                                  "inside the function")
        elif name in kept_names:
            result.conflict(stmt, f"`{name}` is also defined at module level; inside run() this assignment would "
                                  "make it a local and hide the module-level one. Rename one of them")
    # A kept statement must not need anything that only exists once run() has started.
    seen_moved: set[str] = set()
    for node, what in plan:
        if what == KEEP:
            needs = loaded_names(node) if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                 ast.ClassDef)) else set()
            if clash := sorted(needs & seen_moved):
                result.conflict(node, f"this stays at module level but uses {', '.join(clash)}, which is only set "
                                      "by code that moves into run()")
        else:
            for stmt in (node.body if what == GUARD else [node]):
                seen_moved |= bound_names(stmt)


def _is_definition(node: ast.stmt, used: dict[str, int], kept_vars: set[str]) -> bool:
    """Assignments that functions depend on, and setup calls on such objects, stay at module level.

    `parser = ArgumentParser()` + `parser.add_argument(...)` must not move into run() when a
    function uses `parser`: run() is called many times in one process, and argparse would then
    complain that the option is already defined."""
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        bases = {dotted(t).split(".")[0] for t in targets if isinstance(t, (ast.Attribute, ast.Subscript))}
        bases |= {dotted(t.value).split(".")[0] for t in targets if isinstance(t, ast.Subscript)}
        if _reads_input(node.value):
            return False  # per-call input (args, stdin) can't be computed once at import time
        if bool(names & set(used)) or bool(bases & kept_vars):
            return True
        # Plain constants (URL = "…", LIMITS = {…}, TOTAL = A + 1) stay put: moving them changes nothing.
        value = node.value
        return bool(names) and not bases and value is not None \
            and not any(isinstance(n, (ast.Call, ast.Await, ast.Yield, ast.NamedExpr)) for n in ast.walk(value)) \
            and loaded_names(value) <= kept_vars | _CONSTANT_NAMES
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        func = node.value.func
        base = dotted(func).split(".")[0] if isinstance(func, ast.Attribute) else ""
        return bool(base) and base in kept_vars
    return False


def _reads_input(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for sub in ast.walk(node):
        if dotted(sub) in {"sys.argv", "sys.stdin"}:
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in {"parse_args", "parse_known_args"}:
            return True
    return False


_CONSTANT_NAMES = {"True", "False", "None", "__file__", "__name__"}


def _check_unsafe(result: Analysis, moved: set[int]) -> None:
    """Things that can't work inside a module, wherever they are."""
    for node in ast.walk(result.tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                top = mod.split(".")[0]
                if top in GUI_MODULES:
                    result.conflict(node, f"`{top}` opens a window or takes over the terminal; a module must "
                                          "return text instead")
                elif top == "multiprocessing":
                    result.conflict(node, "multiprocessing re-imports the script in child processes and needs the "
                                          "`__main__` guard; it can't run inside a module call")
                elif top in NETWORK_MODULES:
                    result.uses_network = True
        elif isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in INTERACTIVE or name in {"getpass.getpass", "sys.stdin.readline"}:
                result.conflict(node, f"{name}() waits for someone to type an answer. There is no keyboard in a "
                                      "pipe or an MCP call; take the value as an option or from stdin instead")
            elif name in {"fileinput.input"}:
                result.conflict(node, "fileinput reads sys.argv/sys.stdin directly; read the FILE args or the "
                                      "`stdin` text instead")
            elif tuple(name.split(".")) in UNSAFE_CALLS:
                result.conflict(node, UNSAFE_CALLS[tuple(name.split("."))])
            elif name == "open" and len(node.args) > 1 and isinstance(literal(node.args[1]), str) \
                    and set(literal(node.args[1])) & set("wax+"):
                result.writes_files = True

    for node, what in result.plan:
        if what == KEEP:
            continue
        stmts = node.body if what == GUARD else [node]
        for stmt in stmts:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.While) and literal(sub.test) is True \
                        and not any(isinstance(x, (ast.Break, ast.Return)) for x in ast.walk(sub)):
                    result.conflict(sub, "`while True:` with no break never finishes, so the command would never "
                                         "return its output. Give the loop an end")


def _plan_edits(result: Analysis, moved: set[int]) -> None:
    """sys.argv / sys.stdin / parse_args() inside moved code → args / stdin."""
    lines = result.source.splitlines()
    func_defs = {n.name: n for n, what in result.plan
                 if what == KEEP and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    moved_stmts = []
    for node, what in result.plan:
        if what == GUARD:
            moved_stmts += node.body
        elif what == MOVE:
            moved_stmts.append(node)

    handled: set[int] = set()  # ids of sys.argv / sys.stdin nodes an edit takes care of

    def edit(node: ast.AST, new: str, why: str) -> None:
        if node.lineno != node.end_lineno:
            result.conflict(node, f"{why}, but the expression spans several lines; change it by hand")
            return
        line = lines[node.lineno - 1]
        col = len(line.encode("utf-8")[:node.col_offset].decode("utf-8", "replace"))
        end = len(line.encode("utf-8")[:node.end_col_offset].decode("utf-8", "replace"))
        result.edits.append(Edit(node.lineno, col, end, new, why))
        for sub in ast.walk(node):
            handled.add(id(sub))

    def empty_call_parens(call: ast.Call, inner: str, why: str) -> None:
        # Replace the "()" of a no-argument call (after the function expression) with "(inner)".
        line = lines[call.lineno - 1]
        if call.func.end_lineno != call.end_lineno:
            result.conflict(call, f"{why}, but the call spans several lines; change it by hand")
            return
        b = line.encode("utf-8")
        start = len(b[:call.func.end_col_offset].decode("utf-8", "replace"))
        end = len(b[:call.end_col_offset].decode("utf-8", "replace"))
        result.edits.append(Edit(call.lineno, start, end, f"({inner})", why))

    # 1. In the code that moves into run()
    main_candidates: dict[str, list[ast.Call]] = {}
    for stmt in moved_stmts:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                name = dotted(sub.func)
                if isinstance(sub.func, ast.Attribute) and sub.func.attr in {"parse_args", "parse_known_args"} \
                        and not sub.args and not sub.keywords:
                    empty_call_parens(sub, "args", f"{sub.func.attr}() would read sys.argv; give it the `args` list")
                elif name == "sys.stdin.read" and not sub.args:
                    edit(sub, "stdin", "sys.stdin.read() → the `stdin` text")
                    result.uses_stdin = True
                elif name == "sys.stdin.readlines" and not sub.args:
                    edit(sub, "stdin.splitlines(keepends=True)", "sys.stdin.readlines() → lines of `stdin`")
                    result.uses_stdin = True
                elif name == "len" and len(sub.args) == 1 and dotted(sub.args[0]) == "sys.argv":
                    edit(sub, "(len(args) + 1)", "len(sys.argv) counts the program name too")
                elif isinstance(sub.func, ast.Name) and sub.func.id in func_defs and not sub.args \
                        and not sub.keywords:
                    main_candidates.setdefault(sub.func.id, []).append(sub)
            elif isinstance(sub, ast.Subscript) and dotted(sub.value) == "sys.argv" and id(sub) not in handled:
                index = sub.slice
                if isinstance(index, ast.Slice) and literal(index.lower) == 1 and index.upper is None \
                        and index.step is None:
                    edit(sub, "args", "sys.argv[1:] → `args`")
                elif isinstance(literal(index), int) and literal(index) >= 1:
                    edit(sub, f"args[{literal(index) - 1}]", f"sys.argv[{literal(index)}] → args[{literal(index) - 1}]")
            elif isinstance(sub, ast.For) and dotted(sub.iter) == "sys.stdin":
                edit(sub.iter, "stdin.splitlines(keepends=True)", "looping over sys.stdin → lines of `stdin`")
                result.uses_stdin = True

        for sub in ast.walk(stmt):
            name = dotted(sub) if isinstance(sub, ast.Attribute) else ""
            if name in {"sys.argv", "sys.stdin"} and id(sub) not in handled:
                result.conflict(sub, f"{name} is used here in a way that can't be rewritten automatically. In a "
                                     f"module, use the `{'args' if name == 'sys.argv' else 'stdin'}` parameter of run()")
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and sub.id in {"args", "stdin"}:
                parent_ok = _is_param_rebind(stmt, sub)
                if not parent_ok:
                    result.conflict(sub, f"`{sub.id}` is the name of a run() parameter; this assignment would "
                                         "overwrite it. Rename the variable")

    # 2. Inside functions: one fixable pattern — main() parsing sys.argv, called from moved code.
    for name, fn in func_defs.items():
        uses = [s for s in ast.walk(fn) if isinstance(s, ast.Attribute) and dotted(s) in {"sys.argv", "sys.stdin"}]
        parses = [s for s in ast.walk(fn) if isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
                  and s.func.attr in {"parse_args", "parse_known_args"} and not s.args and not s.keywords]
        for use in uses:
            what = dotted(use)
            result.conflict(use, f"{what} is used inside {name}(). A module gets its input from run(args, stdin); "
                                 f"pass `{'args' if what == 'sys.argv' else 'stdin'}` into {name}() by hand")
        if not parses:
            continue
        calls = main_candidates.get(name, [])
        params = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        if len(parses) > 1 or not calls or params or fn.args.vararg or fn.args.kwarg:
            result.conflict(parses[0], f"{name}() calls {parses[0].func.attr}() without arguments, so it reads "
                                       f"sys.argv. Give {name}() an `argv` parameter, pass it to "
                                       f"{parses[0].func.attr}(argv), and call {name}(args) from run()")
            continue
        header = lines[fn.lineno - 1]
        open_paren = header.find(f"{name}(", fn.col_offset)
        close = header.find(")", open_paren)
        if open_paren < 0 or close < 0 or header[open_paren + len(name) + 1:close].strip():
            result.conflict(fn, f"can't find the empty parameter list of {name}(); add an `argv=None` parameter by hand")
            continue
        result.edits.append(Edit(fn.lineno, open_paren + len(name), close + 1, "(argv=None)",
                                 f"{name}() gets the argument list instead of reading sys.argv"))
        empty_call_parens(parses[0], "argv", f"{parses[0].func.attr}() in {name}() parses the `argv` it is given")
        for call in calls:
            empty_call_parens(call, "args", f"run() passes its `args` to {name}()")

    result.edits.sort(key=lambda e: (e.line, e.col))


def _is_param_rebind(stmt: ast.stmt, target: ast.Name) -> bool:
    """`args = parser.parse_args()` and `stdin = sys.stdin.read()` are fine: they read before rebinding."""
    for sub in ast.walk(stmt):
        if isinstance(sub, ast.Assign) and any(t is target for t in sub.targets):
            call = sub.value
            if isinstance(call, ast.Call):
                if isinstance(call.func, ast.Attribute) and call.func.attr in {"parse_args", "parse_known_args"}:
                    return True
                if dotted(call.func).startswith("sys.stdin.") or dotted(call.func) == "len":
                    return True
    return False


def _collect_docs_facts(result: Analysis) -> None:
    """Options, description, error messages and env vars, for the .md/.skill drafts."""
    seen_errors: set[str] = set()

    def error(msg: str) -> None:
        msg = " ".join(msg.split())
        if msg and msg not in seen_errors:
            seen_errors.add(msg)
            result.errors.append(msg)

    for node in ast.walk(result.tree):
        if not isinstance(node, (ast.Call, ast.Subscript)):
            continue
        if isinstance(node, ast.Subscript):
            if dotted(node.value) == "os.environ" and isinstance(literal(node.slice), str):
                _add_env(result, literal(node.slice))
            continue
        name = dotted(node.func)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else name
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        if attr == "ArgumentParser" or name == "ArgParser":
            result.description = result.description or text_of(kwargs.get("description"))
        elif attr == "add_argument":
            opt = _option(node, kwargs)
            if opt is not None:
                result.options.append(opt)
        elif attr == "add_subparsers":
            result.notes.append("uses argparse sub-commands; the docs draft lists only the top-level options")
        elif attr == "error" and node.args:
            error(text_of(node.args[0]))
        elif name == "sys.exit" and node.args and text_of(node.args[0]):
            error(text_of(node.args[0]))
        elif name in {"os.environ.get", "os.getenv"} and node.args and isinstance(literal(node.args[0]), str):
            _add_env(result, literal(node.args[0]))
        elif name == "print" and node.args and any(k.arg == "file" and dotted(k.value) == "sys.stderr"
                                                   for k in node.keywords):
            error(text_of(node.args[0]))
    for node in ast.walk(result.tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            error(text_of(node.exc.args[0]))


def _add_env(result: Analysis, name: str) -> None:
    if name.isupper() and name not in result.env_vars and name not in {"HOME", "PATH", "USER", "TERM", "PWD"}:
        result.env_vars.append(name)


def _option(call: ast.Call, kwargs: dict[str, ast.AST]) -> Option | None:
    names = [literal(a) for a in call.args]
    if not names or not all(isinstance(n, str) for n in names):
        return None
    flags = [n for n in names if n.startswith("-")]
    dest = literal(kwargs.get("dest")) or (
        max(flags, key=len).lstrip("-") if flags else names[0])
    dest = str(dest).replace("-", "_").lower()
    action = literal(kwargs.get("action")) or "store"
    nargs = literal(kwargs.get("nargs"))
    type_node = kwargs.get("type")
    type_name = type_node.id if isinstance(type_node, ast.Name) else ""
    kind = "string"
    if action in {"store_true", "store_false", "count", "help", "version", "store_const"}:
        kind = "boolean"
    elif action in {"append", "extend"} or nargs in {"*", "+"} or isinstance(nargs, int) and nargs > 1:
        kind = "array"
    elif type_name == "int":
        kind = "integer"
    elif type_name == "float":
        kind = "number"
    if action in {"help", "version"}:
        return None
    choices = literal(kwargs.get("choices"))
    metavar = literal(kwargs.get("metavar"))
    return Option(
        flags=flags,
        dest=dest,
        kind=kind,
        metavar=metavar if isinstance(metavar, str) else "",
        choices=[str(c) for c in choices] if isinstance(choices, (list, tuple)) else [],
        default=literal(kwargs.get("default")),
        help=text_of(kwargs.get("help")).replace("%(default)s", str(literal(kwargs.get("default")))),
        required=bool(literal(kwargs.get("required"))) or (not flags and nargs not in {"?", "*"}),
    )
