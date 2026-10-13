# ShellCraft — Upgrade Assessment (fifth)

> Fifth full assessment: 2026-10-12, GitLab copy `main` @ `e8744c0`, GitHub mirror `main` @ `c7e8297` (50 commits). Python 3.14.7, prompt_toolkit 3.0.53, Rich 15.0.0, mcp 2.2.0.
> 723 tests pass locally (10 s). 63 builtins registered (59 shown by `help`), 7 bundled modules. About 14,700 lines of application code (core 8,460, commands 3,084, modules 1,072, tools/ingest 2,080) and 5,836 lines of tests.
> Items marked **(verified)** were reproduced during this assessment. Everything else comes from reading the code and the project's own documents (README, DEVELOPMENT, TODO, CHANGELOG, the fourth assessment, TUI_UPGRADE, templates/README, the module manuals).

This one was asked for with two questions: *is anything critical?* and *what about split panes, or a builtin that opens a subshell?* Section 2 answers the first, Section 4 the second. The fourth assessment's items are all done ([CHANGELOG.md](CHANGELOG.md), 0.2.0), so this is a fresh look at what the last two days left behind.

---

## 1. Verdict

The shell itself is in the best shape it has been. The fourth assessment's list is closed, the 0.2.0 work held together under a lot of change (mouse support, hyperlinks, the `ingest` builtin, three new settings in one day), and the suite still runs in ten seconds. The documents are detailed and, with the small drift listed in Section 3, in sync with the code.

**What is critical is not in the code. It is around it.**

1. **The GitHub mirror's CI has never been green.** Every run since CI was added (8 of 8) fails in every job: lint, packaging, and all eight test jobs on Ubuntu and Windows. Nobody has been looking at the results. Two of the three causes were reproduced here and are one-line fixes; the third needs the log. Until this is green, the Windows test matrix is decoration, and half the users run Windows.
2. **The Windows verification pass is still not done**, and the console layer that half the users depend on has only ever run on Linux. CI, once green, does not cover it either: the interactive console (`CONIN$` / `CONOUT$`, mouse, Quick Edit, hyperlinks in conhost) is tested with fakes.
3. **The two copies are drifting.** The `findtime` module reached GitLab as a commit named `udpated` and GitHub as a separate commit; the mirror tracks a 112 MB `Install/` bundle that the GitLab copy does not have at all; a module ingested inside the shell lands in the tracked `modules/` folder of the checkout, which is how `findtime` got there in the first place.

Everything else is polish and roadmap.

---

## 2. Critical: fix this week

### 2.1 Make the GitHub CI green **(verified: 8 of 8 runs red)**

The public API shows every run of `.github/workflows/ci.yml` failing, from the first (`77557c3`, the CI commit itself) to the latest (`c7e8297`). Per job, the failing step is:

| Job | Failing step | Reproduced locally? |
| --- | --- | --- |
| `package` | *Install the wheel into a clean venv and run it* | **Yes.** `shellcraft --version` passes; the import check `python -c "assert find_spec('core.bundled_modules')"` fails because it runs with the checkout as the working directory, so `sys.path[0]` is the checkout and the source tree's `core/` (which has no `bundled_modules`) shadows the installed wheel. The wheel itself is correct: it contains `core/bundled_modules/*.py/.md/.skill` and `tools/ingest/`. |
| `lint` | *python tools/ingest check --all --strict* | **No.** It passes here in a fresh venv with only the `dev` extra, with and without the censys SDK, and with `CI=true`, `GITHUB_ACTIONS=true`, `TERM=dumb`. One candidate is the checker's "shadows the OS program" warning, which depends on what is on the runner's `PATH`; strict mode turns it into a failure. From `c7e8297` on, the `findtime` module's two warnings fail this step deterministically (see 2.4). |
| `test (*)` | *pytest -q* on every OS and Python | **Partly.** `tests/test_console.py::test_rich_falls_back_to_its_own_size_without_an_output` fails whenever `TERM=dumb` is set (Rich then ignores `COLUMNS`). Whether the runners set it is not proven; the job logs need repository admin rights, which the public API does not have. Everything else passes in a CI-like environment here (fresh venv, `env -i`, no `~/.shellcraft`). |

**Fixes**

- `package`: run the import check from another directory, or with `python -P` (3.11+, leaves the working directory off `sys.path`): `cd /tmp && /tmp/smoke/bin/python -c "..."`. — `.github/workflows/ci.yml`, `.gitlab-ci.yml`
- The `TERM` test: `monkeypatch.delenv("TERM", raising=False)` (or set `TERM=xterm`) inside the test, since it is about `COLUMNS`, not `TERM`. — `tests/test_console.py:212`
- Then open the Actions page for the next run and read the `lint` and one `test` log. If the strict checker fails on the OS-program warning, make that check `WARN` only outside strict mode, or exclude it from `--strict` (it describes the runner, not the module).
- Add a badge to the README so a red CI is visible, and look at it after each push to the mirror.
- The GitLab copy's `.gitlab-ci.yml` has the same package step; whether it runs at all on the private GitLab (and whether a Windows runner exists) is not visible from here.

### 2.2 Run the Windows pass **(open since the second assessment)**

The TODO's checklist is right; it has just never been run. What is new since it was written, and should be added to it: a completion-menu click, text selection at the prompt after the menu closed (Quick Edit restored), `settings mouse_always on` with a click on `⚡` and on the toolbar, Ctrl-click on an `ls` entry in Windows Terminal, the same in classic conhost (expect plain names there), `ingest check --all` inside the shell, and `shellcraft ingest --help` from PowerShell. Thirty minutes on one machine. Keep the `--diag` report in the repo under `docs/` so the next regression has a baseline.

### 2.3 Stop the two copies drifting **(verified)**

- `Install/` is tracked in the mirror only: 110 files, 113 MiB of objects, so every clone of the GitHub repo downloads them, and every refresh adds a copy. The TODO already says so. Decide once: move it to a GitHub Release asset (the natural home for an offline bundle) or Git LFS, then `git rm --cached -r Install` and add it to `.gitignore` in both copies. The trees match again afterwards.
- The patch-and-`git am` workflow keeps the code identical, but commits made by hand on one side (`e8744c0 udpated`) break it quietly. Make the GitLab copy the only place where commits are made and the mirror strictly a push target, or make the GitHub repo a true mirror (`git push --mirror`, or GitLab's repository mirroring, which keeps commit IDs identical).

### 2.4 `findtime` fails strict checking **(verified)**

Two warnings: unknown options are silently accepted, and the `.skill` has no `[[tests]]`. On the mirror's `main` since `c7e8297`, so the lint job stays red whatever 2.1 fixes. Either finish the module (reject unknown options in `run()`, add two `[[tests]]`, replace the wizard's `TODO` lines), or move it to a user modules folder (see 4.4) and out of the bundled set.

### 2.5 Smaller things that still deserve "soon"

- **Ctrl-C abandons the worker thread** (TODO P1): a module stuck in a network call keeps running after the prompt returns. A cooperative cancel flag in `modkit` is the real fix; a line in `templates/README.md` telling authors to pass timeouts is the cheap one. — `core/output.py::make_spinner_runner`
- **API keys in plain text** on Windows, where mode 600 means nothing (TODO P0, Security). The `keyring` backend is a half-day. Until then, say so in the README's *How keys are stored* section (it currently implies the file is protected).
- **mypy's 136 errors** are informational, and the number is not shrinking. Pick one module per week; gate `core/commands/` first, since it is the most self-contained.

---

## 3. Documents: drift found while reading

Small, and all in files this assessment read.

- `README.md:27` and `TODO.md:4` say **58** builtins; `help` shows **59** (63 registered, `[` / `[[` hidden, `for` / `if` listed as keywords) after `ingest`. — fix the number, or stop counting in the README and say "about 60".
- `DEVELOPMENT.md:66`: `tools/ingest/` is described as "not a shell command". It is one now (`ingest`, `shellcraft ingest`).
- `TODO.md:149`: "`Install/` adds ~112 MB of binaries to the git history" is true for the GitHub mirror only; the GitLab copy does not contain `Install/`.
- `TODO.md:4`: 715 tests → 723; "the `tools/ingest` script" → the `ingest` command.
- `TUI_UPGRADE.md`: a design study, not a plan. Its Option A sample uses `MouseEventType.DRAG`, which does not exist in prompt_toolkit (drags arrive as `MOUSE_MOVE` with a button). Either move it to `docs/` with a one-line verdict at the top (Section 4.3 below), or delete it; as a top-level file next to the README it reads as roadmap.
- `templates/README.md:56`: "The module is written to `modules/<name>.py`" — inside the shell it is the shell's modules folder, which today is the checkout's `modules/` (4.4).
- `CHANGELOG.md`: the *Unreleased* section has grown to a release's worth (mouse, links, `ingest`, the port change). Cut 0.2.1 or 0.3.0 before the next feature lands, so the mirror's users see a tag.

---

## 4. Feature upgrades: subshells, jobs, and panes

The question was "split the TUI to run ShellCraft side by side, or add a builtin that creates a subshell". Three different features hide in it, and they are worth doing in this order.

### 4.1 `sub`: hand the terminal to a program **(recommended first, small)**

Today an OS program runs with its output captured (`subprocess.run(..., capture_output=True)`), so `vim`, `htop`, `ssh`, `python`, and a plain `bash` cannot be used from inside ShellCraft. The TODO lists that under *Won't do* for the capture path, and that is right: the fix is not to change the capture path but to add one builtin that does the opposite.

```
sub                      # your login shell (bash, zsh, pwsh, cmd), back to ShellCraft on exit
sub bash                 # or any program, with the keyboard and the screen handed to it
sub vim notes.md
sub htop
sub python -i
```

Design:

- A `stateful=True, direct=True` builtin, interactive shell only, like `tail -f` and `ingest`. `stateful` keeps it away from MCP for free.
- Between prompts, prompt_toolkit's application is not running and the console is in cooked mode on both platforms (on Windows, `raw_mode` exits when `prompt()` returns), so `subprocess.run(argv, cwd=os.getcwd())` with inherited stdin, stdout and stderr is enough. Flush the Rich console first. No PTY, no emulator, no screen management: the real terminal draws, and Windows Terminal or conhost handles the child's console modes.
- The exit code becomes `$?`; the shell's cwd is unchanged (a child cannot change its parent's). After the child exits, re-read nothing: ShellCraft's state is its own.
- With no arguments: `$SHELL` on POSIX, `%COMSPEC%` on Windows, preferring `pwsh` when found. `settings system_commands` is not needed, because `sub` is explicit.
- Ctrl-C goes to the child, as in bash; Ctrl-Z job control is out of scope.
- Tests: `sub` with `sys.executable -c "import sys; sys.exit(3)"` → `$?` is 3; refused over MCP; refused with no terminal.

About sixty lines plus the man page. It answers "launch a subshell or sub-program" for most of what people mean by it, and it works the same on both platforms.

### 4.2 Jobs: background commands and a jobs bar **(next, medium)**

`cmd &` is parsed and rejected today, which was the right first step. The full feature, in bites that each ship on their own:

1. **Job model**: `cmd &` and a `spawn` form for OS programs, each with a pid, a start time, an exit status, and output written to `~/.shellcraft/jobs/N.log` (trimmed like `mcp-http.log`). `jobs`, `kill %N`, and a done notification printed above the prompt via prompt_toolkit's patch-stdout. The MCP child in `core/mcp_child.py` is already this pattern for one process (process group, parent watch, log, prompt marker); generalize it.
2. **The bar**: a `jobs` prompt segment (`⚙ 2 running`) and, with `mouse_always`, clickable entries in the bottom toolbar. Clicking opens the job's log in the pager with a follow mode, which is `tail -f` on the log file.
3. **Attach** (POSIX first): start a job on a pseudo-terminal, and `attach %N` hands the real terminal to it until a detach key (`Ctrl-]`). Windows needs `pywinpty`; ship "view the log" there until someone asks.
4. **Cleanup policy**: kill jobs on exit, with a warning listing them; `settings jobs_survive on` to orphan them instead.

Keeps the shell a line REPL with full scrollback. A few days spread over the four bites.

### 4.3 Side-by-side panes **(not recommended)**

`TUI_UPGRADE.md` describes a multiplexer: a full-screen Application with draggable splits, each pane either a ShellCraft session or a PTY with an in-process VT100 emulator. It is feasible with prompt_toolkit (the pager already runs that way, and drags do arrive as `MOUSE_MOVE` events), but:

- It turns every output path into a full-screen redraw: `show()`, the spinner thread, `tail -f`, the transient prompt. That is most of `core/shell.py` and `core/output.py`.
- Users lose the terminal's scrollback, selection and search, and people in tmux, Zellij or Windows Terminal panes get a second multiplexer inside the first.
- A PTY pane needs `pyte` on Linux and `pywinpty` (compiled) on Windows, and `pyte` is rough with vim, truecolor and alternate screens.
- Tests go from pipeline tests to snapshot tests of a screen.

What people actually get from "side by side" is covered by 4.1 (a program on the terminal) and 4.2 (jobs with a bar), plus two cheap additions: set the terminal window title to `shellcraft · ~/path` (OSC 2) so two ShellCrafts in two terminal panes are distinguishable, and a `--title NAME` option / `title` setting for people who run several on purpose. If a real need for panes shows up later, the jobs model is what a pane would display, so nothing in 4.2 is wasted.

### 4.4 A user modules folder **(small, and it closes a trap)**

`ingest` inside the shell writes to `ctx.registry.directory`. From a checkout that is the tracked `modules/` folder, so a personal module becomes an untracked file in the repo, fails strict CI, and ends up committed by accident (`findtime`). The TODO's *packaging leftovers* item is the fix: load `~/.shellcraft/modules` next to the bundled set, make it `ingest`'s default destination, show its modules under their own header in `help` and `modules`, and let a user module shadow a bundled one by name. Half a day, and `pip install` users get a place for their modules at the same time.

### 4.5 Other upgrades, in the order they would be felt

- **Startup rc file** `~/.shellcraft/rc` (TODO P2): aliases and `export` lines that outlive the session. Now that variables exist, people will ask.
- **`source FILE`** and shell functions: the two scripting gaps the TODO lists as open.
- **A release**: 0.2.1 or 0.3.0 with a tag on both remotes, so the mirror's users can pin something.
- **`shellcraft/` package rename** (TODO P0): still the right call, still breaking. Do it with the user modules folder, since module authors have to touch their imports anyway (`core.modkit` → `shellcraft.modkit`, with a `core` shim kept for one release).
- **True streaming between segments** (TODO P1): `yes | head`, `tail -f | grep`. A thread per segment with a queue is enough; it is the one architectural item left.
- **Hyperlinks cost**: `ls` on 5,000 files went from 44 ms to 107 ms with `links` on **(verified)**, because of a `resolve()` per entry. Fine for now; if large listings matter, build the URI from `os.path.abspath` instead of resolving symlinks.

---

## 5. Suggested order

**This week (one day)**
CI: the package step's working directory, the `TERM` test, read the lint log, fix or move `findtime`. Watch the run go green on Ubuntu and Windows. Decide what to do with `Install/`. Fix the four doc counts.

**Next (half a day each)**
The Windows pass with the new items on the checklist. `sub`. The user modules folder with `ingest` pointing at it. A tagged release.

**Then**
Jobs in four bites. `rc`, `source`, functions. The package rename with the `core` shim. mypy gating per package.

**Not planned**
Panes. The reasons are in 4.3; `TUI_UPGRADE.md` should say so at the top or move to `docs/`.

---

**Legend:** **(verified)** = reproduced on 2026-10-12 · CI facts come from the public GitHub Actions API for `Faru0/shellcraft`; job logs were not readable without admin rights.
