# Scripting upgrades checklist

The scripting improvements we're working through, bash side and Python side. Whoever finishes an
item (a person or an agent) ticks its box and fills in its Notes: what changed, which tests and
docs cover it, and anything left out. An item is done only when the full test suite passes.

## Bash side

- [x] **1. `set` options**: `set -e`, `-u`, `-x`, `-o pipefail`, `+X` to turn one off, `set -o` to list, `set --` for the arguments
  - Done when: `-e` stops the current line, function, sourced file, rc or func (never the shell); `-u` stops on an unset variable; `-x` prints each line before it runs; tests, `man set`, docs
  - Notes: `set` builtin in commands/misc.py (SET_DOC, in SPECIAL_BUILTINS); `ctx.options` + `ctx.errexit_off` (tests, left of `&&`/`||`, `!`, and functions called from them). `-e`: `_Runner.check_errexit` raises `_Abort(reported=True, stops=True)`; `PipelineResult.stopped` tells `startup.source` to skip the rest of the file (one `✗ file:N … (set -e: …)` line); a piped block stops but its tail runs; `$( )` stops only itself. `-u`: `parser.UnboundVariable` -> `pipeline.UnboundError` ("NAME: unbound variable", status 1), stops like `-e`; `$1`.., `$#`, `$@`, `$?` exempt. `-x`: `+ words` per pipeline segment and for `x=1`, via write_stderr. `pipefail`: earlier segments record their status. funcs `_snapshot` keeps options. `set --` / `set a b` replace the args (a top-level frame when outside a function). Tests: tests/test_set_options.py. Docs: scripting/functions.md "Stop on errors with set -e", scripting.md, cheatsheet, funcs.md, startup.md, CHANGELOG. Not done: `x=$(false)` doesn't trigger `-e`; `$((unset))` isn't checked by `-u`; `-x` prints `+` (no `++` for nesting).
- [x] **2. `trap`**: `trap 'CMD' EXIT|INT|ERR`, `trap - SIG`, `trap` to list
  - Done when: an EXIT trap runs when its function, sourced file or func ends (at the prompt: when the shell exits); INT on Ctrl-C; ERR on a failing command; `exit` in rc checked; tests, `man trap`, docs
  - Notes: new shellcraft/traps.py (frame(), interrupted(), failed(), session_exit()); `trap` builtin in commands/misc.py (TRAP_DOC, SPECIAL_BUILTINS, `-p`, `-l`, `''`, `-`, names any case / SIG prefix / 0 / 2). `ctx.traps` frames: call_function (bash and `def`, EXIT runs before `local`s are put back), `source` builtin files, funcs. The rc file, startup/*.sc and a profile's rc use the session frame (`startup.source(session=True)`) so `trap EXIT` there runs when you leave, as in ~/.bashrc. EXIT at the prompt: Shell._goodbye (exit and Ctrl-D). INT: innermost INT trap, run once per Ctrl-C (marked on the KeyboardInterrupt), then the unit stops as before. ERR: _Runner.check_errexit, run_line's simple path, and the error paths of source / funcs.run / Shell._execute; respects errexit_off, no recursion (ctx.in_trap), `$?` restored; a failing function call doesn't run it twice (ctx.err_ran_at). Trap output in a non-live function goes into the function's output. `exit` in rc bug was real (ShellExit caught as "startup failed"): cli._interactive now ends cleanly with the code, running EXIT traps. Completion offers EXIT/INT/ERR. Tests: tests/test_trap.py. Docs: scripting/functions.md "Clean up with trap", funcs.md, startup.md, scripting.md, cheatsheet, CHANGELOG. Not done: INT at an idle prompt (only while a command runs); trap output in a non-live function interrupted by Ctrl-C is lost.
- [ ] **3. `getopts`**: options for functions and funcs, like `backup -v -f notes.txt`
  - Done when: bash behaviour (`OPTARG`, `OPTIND`, `?` for unknown, leading `:` for silent), works in `def`; tests, `man getopts`, docs
  - Notes:
- [ ] **4. Check a script without running it**: `source -n FILE` and `func check NAME`
  - Done when: every problem is reported as `file:line: message` (unfinished blocks, bad indentation, unknown commands as warnings), nothing runs; tests, docs
  - Notes:
- [ ] **5. Run a func from outside ShellCraft**: `shellcraft func run NAME ARGS`
  - Done when: no banner or prompt, rc/startup/profile loaded (unless `--no-startup`), output on stdout, errors on stderr, the func's status is the exit code; tests, docs
  - Notes:

## Python side

- [ ] **6. Python assignment**: `x = EXPR`, `count += 1` and the other `op=` forms, in `-py` blocks and `def`
  - Done when: works in colon and brace blocks and `def`; `x = 5` at the prompt is unchanged; tests, docs
  - Notes:
- [ ] **7. `def` return values**: `return EXPR`, and `add(1, 2)` inside Python code
  - Done when: as a command, 0-255 is the status, True/False are 0/1, anything else is printed; `$(add 1 2)` captures it; `total = add(1, 2)` and `if is_weekend(day):` work; tests, docs
  - Notes:
- [ ] **8. `try:` / `except [as NAME]:` / `finally:`**
  - Done when: a failing command or a Python error jumps to `except`; `finally` always runs, also on break, continue and return; brace form works in bash functions; tests, docs
  - Notes:
- [ ] **9. `match` / `case`**: `match WORD:` with `case PATTERN:` arms
  - Done when: same matching as `case -py` (`"a" | "b"`, globs, `_`); tests, docs
  - Notes:

## Later

Not part of this round.

- [ ] Bash arrays: `a=(x y)`, `${a[@]}`, `${#a[@]}`, `a+=(z)`, `declare -A`, `mapfile`
- [ ] Python lists and dicts: `hosts = ["a", "b"]`, `.append()`, `len()`, `{"web": 80}`, sharing storage with bash arrays
