# ShellCraft — Upgrade Assessment

> Fourth full assessment: 2026-10-11, `main` @ `e9fe02f` (40 commits). Python 3.14.7, prompt_toolkit 3.0.53, Rich 15.0.0, mcp 2.2.0.
> 602 tests pass (354 test functions). 46 builtins (44 shown in `help`), 7 modules, ~11,100 lines of application code.
> Items marked **(verified)** were reproduced during this assessment, in a throw-away directory. Everything else comes from reading the code.
>
> **Done on 2026-10-12 as release 0.2.0.** Every item below is checked; [CHANGELOG.md](CHANGELOG.md) lists what changed, and [TODO.md](TODO.md) keeps what is still open afterwards (the `shellcraft/` package rename, true streaming between segments, the Windows verification pass, mypy's annotation debt).

This complements [TODO.md](TODO.md), which keeps the running list. It is organized by what to do next, in priority order: crashes first, then the TUI, then command gaps, then architecture and tooling.

---

## 1. Verdict

The project is in good shape. The architecture from the first assessment (parser → pipeline → UI, with `run_line()` shared by the REPL, MCP and tests) has absorbed loops, conditions, aliases, history expansion, 46 builtins and the MCP server without bending. The test suite is broad and fast (10 s). The docs are unusually thorough for a project this size.

**What stands out now**

1. **Two ways to crash the REPL with a traceback** (new, both verified): a redirect to `~nosuchuser`, and sitting in a directory that was deleted from outside. Neither is caught by `Shell.loop()`.
2. **`main.py` clears the screen in every mode** (new, verified): the `--mcp` stdio stream starts with `ESC[H ESC[2J ESC[3J`, and `--version` / `-h` wipe the terminal. This was added in the last-but-one commit and is the most urgent fix: any MCP client launched through `main.py --mcp` gets garbage before the first JSON frame.
3. **The TUI has reached the point where polish pays off.** The pager truncates long lines with no way to see the rest, `help` ignores its argument, the completion menu has hard-coded dark colors, and the prompt shows no timing or git information. Section 3 lists the upgrades in order of value.
4. **The TODO's open P0s are still open and still reproduce**: `&` passed through as an argument, `cp -r DIR DIR/sub` recursing until `RecursionError`, a malformed `config.json` crashing startup.
5. **No lint, no CI, no CHANGELOG, version still 0.1.0** after 40 commits. The packaging smoke test the TODO asks for would have caught the `main.py` regression's cousin (`core.commands` missing from the wheel) and would catch the next one.

---

## 2. Bugs

### P0 — crashes and wrong output

- [x] **`main.py` clears the screen before argument parsing, in every mode** **(verified)**: `python main.py --mcp </dev/null | od -c` starts with `033 [ H 033 [ 2 J 033 [ 3 J`; `python main.py --version` clears the terminal and then prints the version. Over stdio MCP, the client's JSON-RPC parser sees the escape sequence first. The installed `shellcraft` entry point (`core.cli:main`) does not clear, so the two launchers behave differently. Fix: delete the `os.system(...)` line from `main.py`; in `core/cli.py`, call `ui.console.clear()` only in the interactive branch (next to the banner), and gate it with a `clear_on_start` setting so it can be turned off. Add a test that `main.py --version` writes nothing but the version. — `main.py:12`, `core/cli.py:113`
- [x] **`echo hi > ~nosuchuser` kills the shell** **(verified)**: `Path(...).expanduser()` raises `RuntimeError: Could not determine home directory`, which `run_pipeline()` doesn't convert into a `PipelineError`, and `Shell.loop()` only catches `ParseError` / `PipelineError`. The same `expanduser()` inside a builtin (`cat ~nosuchuser`) is caught and shown as `RuntimeError: …`, which is ugly but survivable. Fix: in `run_pipeline()`, wrap the redirect target handling in `try/except (OSError, RuntimeError)` → `PipelineError("cannot write file: …")`; in `_io.read_file()` and the `files.py` commands, catch `RuntimeError` from `expanduser()` and raise `ModuleError("no such user")`. Then make `Shell.loop()` catch `Exception` as a last resort and show it with `show_error()` instead of dying. — `core/pipeline.py:124`, `core/shell.py:231`, `core/commands/_io.py:14`
- [x] **A deleted working directory kills the shell** **(verified)**: `rmdir` the cwd from another terminal, press Enter, and `_pretty_cwd()` raises `FileNotFoundError` from `os.getcwd()` inside the prompt callback. `pwd` fails the same way (as a caught error); `ls` prints nothing. Fix: `_pretty_cwd()` returns something like `(deleted)` when `os.getcwd()` fails, and `cd` / the prompt offer `cd ~`. — `core/shell.py:33`, `core/builtins.py:77`
- [x] **`&` is still passed on as an argument** **(verified again)**: `echo hi & echo there` prints `hi & echo there`; `rm -f a & ls` deletes both. Add `&` to `UNSUPPORTED` in the parser (it is not in `OPERATORS`, so this is one tuple entry plus a test). — `core/parser.py:24`
- [x] **`cp -r DIR DIR/sub` recurses until `RecursionError`** **(verified again)**: refuse a destination inside the source, as GNU `cp` does. — `core/commands/files.py:236`
- [x] **A malformed `config.json` crashes startup in every mode** **(verified again)**: `{"env": [1]}` → `AttributeError: 'list' object has no attribute 'items'` in `export_env()`. `load_config()` should type-check each known section (`env`, `settings`, `themes`, `aliases`, `disabled_modules`) and drop a bad one with a warning on stderr. — `core/config.py:41`, `core/settings.py:140`

### P1 — wrong results, polish

- [x] **`help NAME` ignores NAME** **(verified)**: `help grep` prints the whole table. Make `help NAME` print the one-line summary and usage (what `man -p NAME` shows without the body), and keep `help` alone as the table. Also hide the `for` / `if` keyword rows the same way `[` / `[[` are now hidden, or move them into a *keywords* footer line. — `core/builtins.py:96`
- [x] **`grep -m 0` prints one line** **(verified again)**: `match_lines` appends before it checks the limit. — `core/commands/text.py:35`
- [x] **A quoted `~` in a redirect target is still expanded** **(verified again)**: `echo hi > '~/f'` writes to the home directory. The tokenizer already expands unquoted `~`; drop the second `expanduser()` in the redirect. — `core/pipeline.py:124`
- [x] **A redirect silently creates missing directories** **(verified again)**: `echo a > typo/dir/f.txt` makes `typo/dir/`. Drop the `mkdir(parents=True)`. — `core/pipeline.py:129`
- [x] **`sort -u -k N` dedupes whole lines** **(verified)**: `echo -e 'a 1\nb 1\na 2' | sort -u -k 2` keeps all three lines; GNU keeps one per key. — `core/commands/text.py:358`
- [x] **`wc -c` counts characters, not bytes** **(verified)**: `echo é | wc -c` gives 2 (GNU: 3). Either count UTF-8 bytes for `-c` and add `-m` for characters, or document the difference in `man wc`. — `core/commands/text.py:293`
- [x] **History redaction can be bypassed** **(verified again)**: `\settings KEY abc` and `echo x ; settings KEY abc` are saved with the key in plain text. Redact per statement after splitting on `;` / `&&` / `||`, and strip a leading `\`. — `core/settings.py:169`
- [x] **`mcp restart` truncates the previous log**: `McpHttpChild.__init__` opens the log with `"wb"`, so the reason the last server died is gone once a new one starts. Append (`"ab"`) with a `--- started <time> ---` separator, or rotate once. — `core/mcp_child.py:60`
- [x] **Slow builtins show no spinner and no feedback**: builtins run on the main thread (`builtin.fn(ctx, …)`), only modules go through `ctx.runner`. `du /`, `find /` and `tree /` sit silent until done. Route builtins through the runner too (they are already thread-safe: none touch the console directly). — `core/pipeline.py:144`
- [x] **Two open shells overwrite each other's config; non-atomic write**: unchanged since the third assessment. Re-read, merge the changed key, write through a temp file + `os.replace`. — `core/config.py:46`
- [x] **Version defined twice** (`pyproject.toml`, `core/__init__.py`), still `0.1.0`. Use `[project] dynamic = ["version"]` reading `core.__version__`, and bump to `0.2.0` with the CHANGELOG below. — `pyproject.toml:7`

### Docs drift (quick fixes)

- [x] Every bundled `.skill` ends with `# Tests: run by tools/modtest.py` — `modtest` was replaced by `tools/ingest check` on 2026-10-06. — `modules/*.skill`
- [x] `DEVELOPMENT.md` layout tree: `winconsole_probe.py`'s line is followed by `ingest/`'s children (`__main__.py`, `analyze.py`, …), so the tree reads as if the probe were a package. — `DEVELOPMENT.md:56`
- [x] `DEVELOPMENT.md` permission table: `history` is `stateful` + `sensitive`, `mcp` and `alias` / `unalias` / `modules enable|disable` are `stateful`; the table lists neither. — `DEVELOPMENT.md:93`
- [x] `README.md` says "44 built-in commands" and TODO.md's header counts 44; there are 46 (44 shown in `help`). Say "44 commands" and leave the brackets out, or count them all. — `README.md:27`
- [x] TODO.md's header still says 586 tests; it is 602.

---

## 3. TUI upgrades

In order of value for the effort. Each one is independent.

### 3.1 Pager **(highest value)**

The pager (`core/output.py::page`) is good, but:

- **Long lines are cut off with no way to read them** — `Window(..., wrap_lines=False)` and no horizontal scroll. A `w` key to toggle wrapping (re-split `lines` into visual rows when on) and `←` / `→` to scroll horizontally when off. `man fetch` and wide `ls -l` output hit this today.
- **Show what is being paged**: a title row (`man grep`, or the command line) on the left of the status bar, so a user who scrolled far knows what they are looking at.
- **Themed match highlight**: searches use raw reverse video (`\x1b[7m`), ignoring the theme's `sc.match`. Use the theme's accent as background.
- **`?` for a key help overlay**, `d` / `u` (half page, like `less`), and mouse-wheel scrolling (`Application(mouse_support=True)` plus a `MouseEvent` handler on the body window).
- **Resize**: `body_height()` is read per render, so it follows resizes already; add `app.invalidate()` on `SIGWINCH` so the redraw happens at once rather than on the next key.

### 3.2 `help`

- `help NAME` (see Bugs). `help -k WORD` to search summaries (`help -k dns`).
- Replace the *kind* column with **section headers** (`Shell`, `Text`, `Files`, `Info`, `Modules`), which reads better than 50 rows of repeated `shell` / `text`, and show the usage string in a muted third column where the width allows.
- Add a footer: `N modules loaded, M disabled (modules -a)`, and the `⚠` warnings the banner shows, so `help` is a one-stop status page.
- On narrow terminals (< 80 columns), fall back to `name — summary` lines without a table.

### 3.3 Prompt

The two-line prompt is distinctive. What it lacks:

- **Duration of the last command** when it took longer than, say, 2 s (`⏱ 4.2s`), next to `[✗ N]`. Cheap: `time.monotonic()` around `run_line()` in `Shell.loop()`.
- **Git branch** (`⎇ main`, with `*` when dirty) when the cwd is in a repository. Read `.git/HEAD` by hand (no subprocess, no `git` dependency), cache per cwd for a second.
- **A right prompt** (`PromptSession(rprompt=…)`) with the clock, so the left side stays short.
- **A `prompt` setting or a small format string** (`{frame}{name}{user}{path}{git}{status}`) so users can drop the `user@host` segment on a single-user machine. Keep the default as it is.
- **Transient prompt**: collapse the two-line prompt to one line (`❯ command`) after Enter, so scrollback is dense. prompt_toolkit supports this through `PromptSession.prompt(..., )` with a custom `rprompt` / `erase_when_done=True` plus reprinting the command.

### 3.4 Input line

- **Syntax highlighting with a prompt_toolkit `Lexer`**: known command names in `sc.success`, unknown ones in `sc.error`, `|` `>` `;` `&&` in the accent color, quoted strings in `sc.path`. The completer already resolves names; reuse `BUILTINS`, `ctx.registry.names()` and the aliases. This is the single most visible upgrade.
- **Bottom toolbar** (`bottom_toolbar=`): a one-line hint while typing — the usage string of the command being typed (`grep [-i -v -c …] PATTERN [FILE...]`), which doubles as inline help, and the `● mcp :8765` marker could move there.
- **Ctrl-R is already bound** (prompt_toolkit's emacs search bindings are on by default, verified), but it is undocumented and the search toolbar is unstyled. Add `"search-toolbar"` / `"search-toolbar.text"` to `pt_style()` and a line in the README's *Typing helpers*.
- **Completion menu**: `completion-menu` backgrounds are hard-coded `#1c1c1c` / `#262626` (`core/themes.py:66`), which is wrong on light terminals and ignores the theme. Derive them from the theme (`muted` darkened) or add `menu_bg` / `menu_fg` to `ThemeDef` with defaults. Also consider `complete_style=CompleteStyle.MULTI_COLUMN` for the command list (46 builtins + modules in one column needs scrolling).
- **Quote-aware completion** and **positional value completion** (`queryCensys <Tab>` → `host cert search`) are still open from the TODO. — `core/completer.py`

### 3.5 Themes

- `theme preview NAME` (apply without saving), `theme reset`.
- **Light-terminal variants**: every preset assumes a dark background. `solarized-light` and `catppuccin-latte` are the natural first two; they need the completion-menu fix above to look right.
- Show theme warnings (invalid custom colors) in the banner, not only on stderr before it.

### 3.6 Feedback and errors

- **Spinner elapsed time**: after 3 s, append `(12 s)` to the spinner label; after 10 s, add `Ctrl-C cancels`. — `core/output.py:46`
- **Unknown switch errors show the usage line**: today `ls -t` says `ls: unrecognized arguments: -t  (see man ls)`. Adding the usage string from the `@builtin` registration to the panel saves a round trip. — `core/modkit.py:26`
- **"Did you mean" for switches** (`grep -ignore` → `-i, --ignore-case`) using the same `OptionSpec` data the completer has.
- **`clear_on_start` setting** (see Bugs) rather than the unconditional clear in `main.py`.

### 3.7 `ls`, `tree`, `grep` on screen

- `ls`: colors by extension class (archives, images, source, docs) in addition to dir/exec/link; `-t`, `-S`, `-r`, `-R`, `-d` are missing entirely **(verified: `ls -t` is rejected)**.
- `tree`: `-I PATTERN` to ignore (`tree -I __pycache__`), and `--du` sizes.
- `grep`: highlight the match in `-c` / `-l` modes is n/a, but `-n` line numbers in `sc.accent` instead of `sc.muted` read better.

---

## 4. Command and scripting gaps

Gaps a user coming from bash hits in the first ten minutes. All verified as "unrecognized arguments" or "not supported yet" unless noted.

| Area | Missing | Notes |
| --- | --- | --- |
| `grep` | `-r/-R`, `-A/-B/-C N` context, `-w`, `-x`, `-o`, `-e PAT` (several), `-E` as a no-op | `-r` and context lines are the most asked-for. |
| `ls` | `-t`, `-S`, `-r`, `-R`, `-d` | See 3.7. |
| `head` / `tail` | `head -c N`, `tail -f` | `tail -f` needs streaming output (Section 5). |
| `wc` | `-m` (chars) vs `-c` (bytes) | See Bugs. |
| `find` | `-size`, `-mtime`, `-newer`, `-not` / `!`, `-empty`, `-path` | |
| `sort` | `-h`, `-V`, `-s`, `-k N,M` | |
| `cat` | `-s`, `-A` | low |
| New builtins | `printf`, `seq`, `xargs`, `basename`, `dirname`, `realpath`, `sleep`, `stat`, `whoami`, `hostname`, `uname`, `time CMD`, `type` | `printf` + `seq` + `xargs` unlock most one-liners. `time` pairs with the prompt duration. |
| Scripting | `while` / `until`, `case`, `x=1` assignment, `$((…))`, `$HOME` (opt-in `export`), `$(…)` and globs on plain lines, piping a whole loop (`done \| sort`), `!N` / `!prefix` | Variables and `while` are the big two. |
| Redirection | `2>`, `2>&1`, `<`, `&` (reject first) | |

---

## 5. Architecture and performance

- **Startup is 0.73–0.85 s** on this machine (`import core.commands, core.shell, core.mcp_server`), of which **module loading is 0.20 s**. `core/cli.py` imports lazily already; the remaining cost is `rich`, `prompt_toolkit`, `requests` (imported by modules at load time) and the eager import of every module (`queryCensys` pulls in the Censys SDK if installed). Options: import modules lazily on first use (`ModuleSpec.run` resolves the import; `help` / completion only need `.skill` / `.md`), and import `requests` inside `run()` in the bundled modules.
- **Streaming pipelines** (TODO P1) are what `tail -f`, `yes | head`, and large `cat file | grep` need. A generator-returning `run()` with `str` still accepted keeps every existing module working.
- **`to_text()` flattens Rich output at a fixed width of 100** (`core/context.py:89`): `help | grep theme` wraps at 100 columns whatever the terminal. Pass `ctx.ui.console.width` when there is a UI.
- **`show()` renders twice** and loads the whole output into Rich before deciding to page (`core/output.py:75`). Reuse the captured ANSI for the print path; stream very large plain-text outputs (over, say, 5,000 lines) straight into the pager without a Rich pass.
- **Hot reload reloads every module on any change** and clears all `shellcraft_modules.*` from `sys.modules`, so a module-level cache in one module is lost when another module's `.md` is edited. Reload only the changed names, and don't re-import for a `.md`-only change (re-read the doc instead). — `core/loader.py:164`
- **`core` as a top-level package name** (TODO) will collide with something in site-packages eventually; `shellcraft/` is the fix, and 0.2.0 is the moment.
- **MCP boundary** (all still open in TODO): per-call timeout + output caps, bearer token on `--mcp-http`, per-module `expose = false`. The `mcp start` child orphaning on `kill -9` is also open.

---

## 6. Tests, CI, tooling

- **Add tests for what this assessment found**: `main.py --version` output is exactly the version line; redirect to `~nosuchuser` is a `PipelineError`; the prompt in a deleted cwd; the new `hidden` flag (no test was added with it: `help` output must not contain `[`, `man [` must still work).
- **Still missing** (TODO): completer, pager key handling, `show()` threshold, theme persistence, banner narrow fallback, packaging smoke test (build wheel → clean venv → `shellcraft --version`).
- **CI**: `.gitlab-ci.yml` (and a GitHub Actions twin for the mirror) running pytest on Linux + Windows, Python 3.11–3.14, the packaging smoke test and `tools/ingest check --all --strict`.
- **ruff + mypy** are not installed in `.venv` (verified). Add them to the `dev` extra with config in `pyproject.toml` and a pre-commit hook. The third assessment found no real ruff errors, so gating on ruff is cheap right away; mypy needs its 41 annotation fixes first.
- **CHANGELOG.md** and a `0.2.0` tag. The git log is detailed enough to write it from.
- **Windows verification pass** (TODO): the console rewrite from 2026-10-09 has still only been run on Linux.

---

## 7. Suggested order

**Week 1 — stop the bleeding (half a day)**
`main.py` clear → `cli.py` interactive branch; catch `RuntimeError` on redirects and `expanduser`; deleted-cwd prompt; `Shell.loop()` last-resort `except Exception`; reject `&`; `cp -r` self-copy; type-check `config.json`; `grep -m 0`; quoted `~`; redirect `mkdir`. Tests for each. Bump to 0.2.0 with a CHANGELOG.

**Week 2 — TUI polish**
Pager wrap toggle + title + themed highlight; `help NAME` and section headers; prompt duration + git branch; completion-menu colors from the theme; spinner elapsed time; usage line in switch errors.

**Week 3 — the bash muscle memory**
`grep -r/-A/-B/-C`, `ls -t/-S/-r/-R`, `printf`, `seq`, `xargs`, `basename`/`dirname`, `sleep`, `time`; then `while` and variable assignment in `script.py`.

**Later**
Syntax highlighting + bottom toolbar; streaming pipelines (`tail -f`); lazy module imports; `shellcraft/` package rename; MCP timeout/auth/exposure; CI with the packaging smoke test; light themes.

---

**Legend:** **P0** = fix soon (crash, wrong output, security) · **P1** = next iteration · **(verified)** = reproduced on 2026-10-11
