# ShellCraft — TODO

> **What is left to do, and only that.** Everything finished is in [CHANGELOG.md](CHANGELOG.md).
> Consolidated on 2026-10-13 from the old TODO, the fourth and fifth upgrade assessments, the
> Windows 11 console analysis and the TUI split-pane notes; those documents are in the git history
> (`git log --diff-filter=D --name-only -- '*.md'`), where their full reasoning can still be read.
>
> **State:** 0.3.0 (tagged `v0.3.0` in both copies). 65 builtins shown by `help`, 7 bundled modules,
> about 770 tests (Linux, Python 3.14). Two copies: the private GitLab one and the GitHub mirror,
> which also carries the offline `Install/` bundle and the `findtime` module.

**Legend:** **P0** = do next (broken, or blocks Windows users) · **P1** = soon · **P2** = when there is time ·
**On hold** = decided to leave for now

---

## P0 — Windows 11

Half the users are on Windows, and none of the Windows code has run on a Windows machine yet.
Findings from a code-level analysis (2026-10-13); items 1–3 and 5 are a few lines each and can be
done from Linux without changing Linux behaviour.

| # | What | Where | Fix |
| --- | --- | --- | --- |
| 1 | The footer inside a subshell is padded to the full width; the Windows console wraps the last cell at once, so the footer creeps a row | `shellcraft/footer.py` `size()` / `panel_lines()` | take the width from the app session's output (`cols - 1`), as Rich does |
| 2 | An attached program's output is written without pending wrap; full-width lines and PSReadLine / vim redraws drift a row | `shellcraft/jobs.py` `_windows_terminal()` | while attached, set `PROCESSED \| WRAP \| VT \| DISABLE_NEWLINE_AUTO_RETURN` on `CONOUT$`; restore after |
| 3 | Emoji / non-BMP characters typed into a subshell are sent as CESU-8 | `shellcraft/jobs.py` key reader | re-pair surrogates before encoding (`encode("utf-16","surrogatepass").decode("utf-16")`) |
| 4 | `direct` mode loses non-BMP input at the prompt and the mouse on the classic console | prompt_toolkit's VT reader | default to `legacy-keys` on every Windows console (`shellcraft/console.py::console_mode`) |
| 5 | `alert` is only `alert.cmd` on Windows; Git Bash inside `sub bash` won't find it | `shellcraft/alerts.py::shim_dir` | also write the POSIX `alert` script there |
| 6 | Ctrl+Backspace deletes one character, not a word | prompt_toolkit | document Alt+Backspace |
| 7 | `⚡` is one cell wide in the classic console; `⤷` is missing from Consolas | fonts | note in the README (Cascadia Mono, or `prompt` without `name`) |
| 8 | Ctrl / Shift+arrow modifiers are dropped in the no-VT-input subshell fallback | `shellcraft/jobs.py::win_key` | map `ControlKeyState` to `ESC [ 1;5 C` style |

**Verification pass** (30 minutes; Windows Terminal and the classic console, each with the default
mode, then `$env:SHELLCRAFT_CONSOLE = "direct"` and `"legacy-keys"`):

1. `python tools\winconsole_probe.py`: type, Backspace in the middle, Tab twice near the bottom, the
   four arrows, Ctrl+←, Shift+→, resize. Keep its report.
2. `python main.py --diag`: keep the report (window and buffer sizes, both console modes).
3. `help`, `man grep` (↑ ↓ ← → `w` `q`), `ls -l` full width: no blank line after full-width rows.
4. `é`, `ü` and an emoji from Win+. at the prompt; `echo` prints them back.
5. `h` then click a completion; `settings mouse_always on`, click `⚡`, wheel over the input line.
6. `sub pwsh`: `dir` (long lines), ↑ for history, ← →, Backspace, an emoji, `Ctrl-]`, `Ctrl-] 1`,
   wheel scroll, `exit`. The footer stays on the last rows.
7. `sub cmd`, `sub bash` (Git): the same, plus `alert -l warning hello` inside each.
8. `alert`, `alert --list`, `alert -c`; `sub shellcraft` refuses `sub` inside.
9. `python main.py --mcp < NUL` and `> NUL`: no traceback.
10. Paste three lines into the prompt; `~/.shellcraft/rc` with `name = input(…)` at startup.
11. `Install\setup.ps1` on a clean machine (only syntax-checked so far).

## P1 — Robustness and packaging

- [ ] **Move `Install/` out of git** (the GitHub copy only: 110 files, ~113 MB of history, larger
  with every refresh). Needs a decision and a tool this machine doesn't have yet (`gh`, or Git LFS):
  publish the bundle as a GitHub Release asset (or `git lfs track "Install/**"`), then
  `git rm --cached -r Install`, add `Install/` to `.gitignore`, and point `Install/README.md`'s
  readers at the release. After that the two copies' trees are the same file for file.

## P1 — Shell features

- [ ] **True streaming between pipeline segments** (`yes | head`, `tail -f | grep`): a thread per
  segment with a queue. The one architectural item left. — `shellcraft/pipeline.py`
- [ ] **Subshells, leftovers**: `cmd &` as a spelling of `sub cmd`; a follow mode for a running
  command job's output. — `shellcraft/jobs.py`
- [ ] **Startup, open questions** (the rc file is done; these are about `~/.shellcraft/startup/*.py`):
  long-running scripts as `sub` jobs (a marker line, or a separate folder?); a dependency order
  beyond name order; per-script timeouts and arguments; whether a script may stop the shell from
  starting. Untested on Windows. — `shellcraft/startup.py`
- [ ] **Shell grammar**: functions and `local`, `select`, `&&` / `||` inside `[[ ]]`, `>&2`,
  `%VAR%`. More Python in the rc file beyond `input()` / `int(input())` if wanted.
- [ ] **A `diag` builtin**: the `--diag` report on demand, after resizing the window.

## P2 — Ideas

- [ ] **Remove the `core` compatibility shim** in 0.4 (`core/`: three files; the package is
  `shellcraft` since 0.3.0).

- [ ] **Module scaffolding**: `new-module NAME` copying the template trio, renamed.
- [ ] **Modules as packages**: `modules/<name>/` folders, per-module requirements, version and author
  metadata in `.skill`.
- [ ] **Structured data mode**: JSON lines between modules with `select`, `where`, `sort-by` and a
  `table` renderer, Nushell style.
- [ ] **`ask "…"`**: a Claude-powered assistant that uses the loaded modules as tools.
- [ ] **More modules**: `json`, `hash`, `whois`, `weather`.
- [ ] **Window title**: set it to `shellcraft · ~/path` (OSC 2), so several ShellCrafts in terminal
  tabs are told apart; a `title` setting.
- [ ] **Hyperlinks cost** (`links` setting, off by default): `ls` on 5,000 files takes 107 ms instead
  of 44 ms because of a `resolve()` per entry; use `os.path.abspath` if it matters.

## On hold — CI / CD

Left for later on purpose (2026-10-13). The GitHub Actions runs have all failed since CI was added;
the GitLab pipeline has not been checked.

- [ ] **Make the GitHub CI green.** Known causes: the packaging smoke test runs its import check from
  inside the checkout, so the source tree's `shellcraft/` shadows the installed wheel (run it from `/tmp`,
  or with `python -P`); `tests/test_console.py::test_rich_falls_back_to_its_own_size_without_an_output`
  fails when `TERM=dumb` (`monkeypatch.delenv("TERM")`); the `lint` job's module checker failed on
  `findtime` (fixed in 0.3.0) and may need its log read (repository admin rights). Same package step in `.gitlab-ci.yml`.
- [ ] **mypy's annotation debt** (136 errors, none a real bug): informational in CI today; gate
  `shellcraft/commands/` first, then the rest.
- [ ] A CI badge in the README once it is green.

---

## Won't do

Recorded on purpose, so they are not proposed again.

- **Split panes / a multiplexer inside ShellCraft** (the old `TUI_UPGRADE.md`). It would turn every
  output path into a full-screen redraw, take away the terminal's scrollback and selection, and
  duplicate tmux / Windows Terminal panes. `sub`, the subshell panel and the footer cover the need.
- **The OS-program fallback stays opt-in and capture-only** (`system_commands`): a nonzero exit
  aborts the pipeline, interactive programs misbehave, stderr is dropped on success, colours are
  lost. `sub PROGRAM` is the way to run interactive programs.
- **No sandbox for MCP file reads** (`--root DIR`): an AI client can read what the user can read;
  writes and redirects stay blocked.
- **The bundled modules stay as they are**: no SSRF blocking in `fetch`, no per-module quota opt-out
  for `queryDns` / `queryCensys`, no further features for `queryCensys`, `queryDns`, `queryCert`,
  `ip2geo` or `myip`.
- **No rebranding**: the ShellCraft name, prompt and banner stay fixed.
