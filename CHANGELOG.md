# Changelog

All notable changes to ShellCraft. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Changed

- **AI access is on by default.** An AI client connected over MCP now runs any command in your
  shell at once, as a `✦ AI:` job with its trace above your prompt, instead of asking for
  `review approve` on everything that wasn't read-only. It is the new saved setting
  `ai_access` (`ai_run` still works as its name). `✦ run` shows in the prompt while it is on, and
  `settings ai_access off` or `ai off` goes back to asking. `request_session_grant` answers at
  once when access is already on.

- **Faster where it was felt.** Plain output no longer goes through Rich line by line: 20,000
  lines print in 2 ms instead of 211, and `ls -l` of a thousand entries in 26 ms instead of 196.
  Pipelines move lines between segments in chunks instead of one queue operation per line:
  `seq 1 100000 | grep 1` takes 245 ms instead of 499, and a four-stage pipeline 220 instead of
  734. A subshell's output reaches the terminal before the terminal emulator parses it, so a
  build or `find /` in a `sub` no longer falls seconds behind. `find` reads each directory once
  with `os.scandir` instead of stat-ing every entry again for `-type` and the walk: five times
  faster on a big tree. Nothing looks or behaves differently.

### Added

- **A policy for what an AI may run.** `~/.shellcraft/ai-policy.toml` (a commented
  `ai-policy.toml.sample` is in your folder) holds rules that deny, ask about or allow a command by
  pattern, for one role, in one folder, until a date; roles with their own limits; a budget of module
  calls; and how long a module's answer is reused. A server started with `--role NAME` offers
  only what the role may use, and the shell checks the role again before it runs a command.
  `monitor`, `orchestrator` and `worker` are built in. Every tool takes an optional `agent` name,
  shown in the footer (`✦ AI worker-2: …`).
- **The shell tells an AI what happens.** It keeps a numbered record of every command (line, exit
  status, time, folder), every job that ended and every Ctrl-C. `get_events` reads it after a
  cursor and can wait for the next event, so a monitor no longer polls the review queue on a timer;
  `search_output` searches the screen and says which command printed each match. `watch on-fail`,
  `watch match REGEX` and `watch job-died` (or `[[trigger]]` in `ai-policy.toml`) turn the events
  you care about into flags in the review queue, with a footer alert.
- **A task board for several AI agents.** An orchestrator puts tasks on it (`create_task`) and
  workers take them (`claim_task`), report (`update_task`) and give them back (`release_task`).
  A claim is exclusive and lasts a lease that every update renews, so a dead worker's task
  frees itself; tasks can wait for others. You see it with `tasks`, the prompt shows `✦ N tasks`,
  and a blocked or failed task raises an alert. The board is also the resource `shellcraft://tasks`.
- **A case file the agents share.** `add_finding`, `list_findings`, `get_finding` and
  `update_finding` keep one finding per IP, domain, hash, user or host: a second agent finding the
  same thing merges its evidence into it and raises the severity or confidence if it is higher. `findings`
  reads, adds, rates and exports the case as a Markdown or JSON report, `findings case NAME`
  keeps separate cases, and a new high or critical finding raises an alert.
- **Module answers are remembered for five minutes.** Two agents asking the same module the same
  question spend one API call. `fresh: true` asks again, errors are never kept, editing a module
  forgets its answers, and `[cache]` in `ai-policy.toml` sets the time or excludes modules.
- **An AI can ask you a question.** `ask_user` puts a question in your footer (`AI asks: ...`
  with numbered choices if it gave some); you answer with `review answer ID 2` or in words, and
  the AI, usually waiting, gets it at once. `tell -r ID MESSAGE` replies inside any item's thread,
  which `review show` prints and the AI reads. `get_review_status` and `get_flagged_items` now
  carry the answer and the thread.
- **Agents and skills for working as a team.** `ai/agents/` gains an `orchestrator` (splits a job
  into tasks, starts workers, merges the case file, asks you when it needs a decision) and a
  `worker` (claims one task, does it, records findings), and the `monitor` now waits on
  `get_events` and your `watch` flags instead of polling on a timer. New skills `task-board` and
  `case-file`; `shellcraft-copilot`, `review-loop` and `soc-triage` cover the new tools.
- **`ai`** shows the state and policy, `ai log` lists every tool call an AI made and how it was
  answered (read-only, granted, approval, denied), `ai policy`, `ai roles`, `ai on` and `ai off`.
  The log is `~/.shellcraft/ai-audit.jsonl`; your `review approve` and `reject` answers are in it.
- **`diag` knows about Ctrl-C and about Windows 11.** A **recent interrupts** section records every
  Ctrl-C of the session: where it landed (a running command, its output being printed, the
  prompt, the gap between commands), what was running and for how long, the console modes at
  that moment on Windows, and what the terminal had delivered by the next prompt (typed text
  kept, mouse or cursor reports dropped). The **system** section names the real Windows release
  (11 24H2, build) and the process chain up to the terminal host (WindowsTerminal.exe,
  conhost.exe, Code.exe); a **signal handlers** section says how Ctrl-C and Ctrl-Break are
  handled. `diag --save [FILE]` writes the report to a file (default `~/.shellcraft/diag.txt`).
  `--timing` ends its table with the same host line.

### Fixed

- **The close button is a plain `×`.** Alerts and finished subshells in the footer used `✖`, which
  Cascadia and Consolas don't have, so Windows 11 drew it from an emoji font: a box, or a wide glyph
  that pushed the line out of place and moved its click target. It is now `×`, which every console
  font has, and the space on either side of it takes the click too.
- **A bad option is one line.** `shellcraft --testingoption` (or `sub shellcraft --testingoption`)
  prints `shellcraft: unrecognized arguments: ...` on stderr and exits with 2, instead of argparse's
  usage block and its own exit.
- **The early banner on Windows.** It is drawn with the shell's own console setup
  (`build_console`) at the terminal's width, instead of a separate Rich console.
- **Ctrl-C in the first moments of a start** (while prompt_toolkit loads) printed a Python
  traceback and exited with status 2. It now ends the start quietly with status 130.
- **Mouse and cursor reports delivered while a command runs** reached the next prompt as text,
  minus their escape prefix: `<64;10;5M`, `12;34R`. Before each prompt, what the terminal
  delivered meanwhile is parsed the way the prompt would, the reports are dropped, and typed
  text, Enter and pastes are kept as typeahead, as in bash.
- **A Ctrl-C between commands** left its `^C` echo on the line and the prompt started right
  after it. The prompt now starts on the next line.
- **An interrupted print** could leave the terminal inside a colored span, mid-line. Colors are
  reset and the line ended before `^C interrupted`.
- **Ctrl-Z** inserted a `^Z` character into the line. It does nothing now.
- The terminal's `NOFLSH` flag is set for the session on Linux and macOS, so a Ctrl-C no longer
  makes the tty driver discard queued output, which could cut an escape sequence in half and
  show its tail as text.
- A stress harness (`tools/stress/`) drives the shell through a pseudo-terminal with Ctrl-C,
  mouse reports, bad bytes and resizes at random moments; `STRESS-TEST.md` has the findings.

### Added

- **Every new profile comes with an empty `startup/` folder**, whether it's made by `profile new`,
  a starter, `profile save`, `profile copy` or `profile import`. No need to create the folder by
  hand before adding `.sc` files.

- **Comments at the end of a line**: `cd ~/projects   # where I work`. A line starting with `#` is a
  comment, and so is everything after a `#` (or `##`…) with whitespace before and after it, at the
  prompt, in rc files, funcs, profiles and the aliases file. A `#` touching other text stays text,
  so `#05d9e8`, `#TODO`, `$#` and `page#top` need no quotes; quoted text is never a comment. The
  prompt colors comments by the same rule. (Before, ` # note` reached the command as arguments.)

- **A real user manual** in `docs/`, with a front page that says where to start. New pages:
  Getting started, Your ~/.shellcraft folder (every file, what it's for, when it's read),
  Aliases and history, Startup, Settings and themes, Command line, Cheat sheet, Troubleshooting,
  and for module authors Your first module (from three lines to a web API client, step by step),
  Ingest a script (the wizard's real questions and what it writes where), Add modules you already
  have, and the Module reference. Funcs, profiles, subshells, keyboard and MCP were rewritten in
  the same plain voice. The README is now short: a pitch, install, a quick start and where to go.
  `tests/test_docs.py` checks every link, and runs the tutorial's code.

- **Every starter file opens with a `# How to use this file` header**: what it's for, how to switch it
  on, and where to read more. A func file may now have its own `#` notes above its header; they are
  kept when ShellCraft rewrites the file (`func set`, `func add`).
- **Starter files in `~/.shellcraft`**, the way Linux fills a new home folder from `/etc/skel`:
  an `rc` and an `aliases` file that are all comments with ready lines to uncomment, an example func
  (`funcs/example.func.sample`) and two example startup scripts with a README
  (`startup/*.py.sample`). Each is written once, the first time the shell starts without it, so
  existing setups get them too and a file you delete stays deleted. `shellcraft --init` writes
  any that are missing (never over your files); `--init --force` refreshes the examples. They
  replace `templates/startup/`.

- **A loading line while the shell starts**, under the banner and in a `sub shellcraft` subshell
  (which used to show a black screen): a progress bar with the step it's on, measured against the
  last start, or a spinner (`settings loader bar|spinner`, bar by default). It vanishes the moment
  the prompt is ready, and a start faster than a tenth of a second never shows it.

- **`curl`**, a builtin (`man curl`, `docs/curl.md`): HTTP requests with curl's own command line,
  built on Python `requests`. Combined flags (`-fsSL`), methods, headers (add, remove, empty), every `-d`
  form, `--json`, `-F` forms and uploads, `-T`, basic / digest / bearer auth, cookies and cookie
  jars, `-L`, `-k`, http(s) proxies (and `HTTP_PROXY` / `NO_PROXY`), timeouts, `--retry`, `-o` / `-O`,
  `-i`, `-I`, `-v` (to the error stream), `-w`, `-f`, `-r`, and curl's exit statuses. `--jq PATH`
  picks a JSON field. AI clients run it through the pipeline tool, where it writes no files. What
  curl does that it doesn't yet (SOCKS, HTTP/2, globbing…) is listed in its manual.

- **Pasting a browser's "Copy as cURL"**: the bash form runs as it is (the parser now reads `$'…'`
  strings, and a line ending in `\` continues on the next), and so does Windows' cmd form
  (`^"…^"` quoting, `^` line ends).

- **`command NAME`**, as in bash: runs the OS program even when a builtin or module has its name
  (`command curl …`); `command -v NAME` prints its path.

### Removed

- **Python startup scripts.** `.py` files in `~/.shellcraft/startup/` aren't run any more, and with
  them go the `$SHELLCRAFT_ENV_FILE` hand-back, the `# shellcraft: background, restart, timeout`
  header lines and `startup stop / start / restart`. `startup` lists a leftover `.py` and says
  what to do: move its commands into a `.sc` file, or start it from one with `sub python FILE.py`.

- **The `fetch` and `filter` modules.** `curl` replaces `fetch` for URLs (`curl URL --jq PATH`,
  `curl -s URL | head -n 20`), and `cat` reads files. `grep` does everything `filter` did, with the
  same switches (`-i`, `-v`, `-c`, `-n`, `-F`, `-m`). A `disabled_modules` entry naming either is
  ignored.

- **Funcs** (`func`, `docs/funcs.md`): named scripts of ShellCraft lines, run on demand like the rc
  file. `func run NAME ARGS` runs the steps in your shell as if typed (`$1`, `$@`), stops at the first
  failure, and prints a one-line result; `-i` runs it isolated (directory, variables, env, aliases,
  theme and settings put back, nothing saved), `-b` in the background, `-q` / `-v` quiet or not. A
  small header sets the description, `system` (needs OS commands), `output` (show / quiet, with `@`
  steps always shown), `command` (typing the name runs it) and `usage`. `func new NAME` asks step by
  step (or `-e` for the editor); without `--save` a func lasts for the session. `list`, `show`,
  `edit`, `add` / `insert` / `rm` / `move`, `set`, `rename`, `copy`, `delete`, `export`, `import`.
  The default setup's funcs work in every profile; a profile's own win; profiles export them.

- **`settings editor`:** the editor `func edit` and `profile edit` open (`settings editor code` for
  VS Code). Terminal editors run in a subshell; window editors get their wait switch (`code --wait`).
  Without it: `$VISUAL`, `$EDITOR`, then nano / notepad.

- **Profiles** (`profile`, `docs/profiles.md`): named setups for bug bounty, OSINT or SOC work,
  switched in the running shell. A profile layers on the default setup and keeps only what it changes:
  theme, settings, prompt, modules on / off, environment variables, aliases, an rc file and its own
  command history (API keys, the MCP token and the base aliases and rc are shared). `profile use NAME`
  / `profile off` switch; `profile new NAME` is a wizard (or `--template bugbounty|osint|soc`,
  `--from P`); `profile save NAME` keeps the session's setup; `show`, `edit`, `set`, `env`, `diff`,
  `copy`, `rename`, `delete`, `reload`, `export`, `import` manage them. While one is active, `theme`,
  `settings`, `prompt`, `alias` and `modules` change it (`alias -g` the shared aliases), the prompt
  shows `◆ NAME`, and processes ShellCraft starts run in it. `shellcraft --profile NAME` starts in one.

- **`env` manages ShellCraft's environment variables:** `env set NAME=VALUE` (or `NAME VALUE`,
  several at once) sets one for the session, in ShellCraft's own environment, so modules, OS
  programs, `sub` programs and new subshells see it; `env unset NAME` drops it and brings back a
  value your shell had. `env set -s NAME` is a secret: hidden typing, masked everywhere, kept out of
  the history and in the OS keyring, so an rc line `env set -s NAME` asks once. Put `env set`
  lines in `~/.shellcraft/rc` to set them at every start. `settings` lists them under
  "Environment (this session)" (values in full, never cut), and `settings reset NAME` removes one.

- **`settings prefix_key`:** choose the subshell prefix key. Ctrl-] stays the default; Ctrl-B
  (tmux's), Ctrl-A (GNU screen's), Ctrl-T, Ctrl-G and Ctrl-S are offered, each listed with what it
  shadows in bash and nano. The prompt, every subshell, the footer hint and the messages follow the
  choice at once; it is saved in config.json. An unchosen key keeps its usual meaning at the prompt.

- **An AI co-pilot in the shell.** The interactive shell publishes its private link in
  `~/.shellcraft/mainlink.json` (readable by you only, removed at exit), and every ShellCraft MCP
  server — the `mcp start` one and a client's own `--mcp` one — reaches it through that file.
  New tools: `get_recent_output` (your screen), `get_jobs` / `get_job_output`, `get_history`
  (keys redacted), `get_clip` / `set_clip` (what Alt-v pastes), `open_subshell` (a lite ShellCraft
  named `✦ AI · finding` in the footer, started in the background, that prints the AI's brief
  first; an alert points to it), `run_in_shell` and `request_session_grant`. `man mcp`.

- **`run_in_shell` in three tiers.** A read-only command (grep, cat, jq, sort, the query modules;
  no redirection, no file changes, no `&`, no `sub`) runs at once as a `✦ AI: command` background
  job with its trace above the prompt. With the session grant, `settings ai_run on` (never saved;
  `✦ run` in the prompt while on) or a `request_session_grant` the user approves once, everything
  runs at once. Anything else waits in the review queue as `AI wants to run: command`;
  `review approve ID` runs it in the user's shell and sends the output back, `review reject` never
  runs it. `man review`.

- **`tell MESSAGE…`:** send the AI a message (a question, an instruction), stored in the review
  spool as a flag of type `message`; `COMMAND | tell` sends output with it.

- **`ai/`:** agents (`shellcraft-copilot`, `soc-triage`, `monitor`) and skills (`shellcraft-copilot`,
  `soc-triage`, `review-loop`) for Claude Code and other clients, with a README that explains
  the tools, the `claude mcp add` lines and how to install them.

- **A `sub` request from a subshell, or an AI-opened subshell, now appears at once** when the
  prompt is up, instead of after the next Enter.

- **Completion that follows the usage line:** Tab offered file names wherever it knew nothing
  better, even where a command can't take one (`cut <Tab>`, which needs `-f` or `-c`). It now
  reads each command's and module's usage line (`[optional]`, `(a | b)`, `WORD...`, `-f VALUE`,
  `a|b|c`) and offers what may come next: the switches a command can't run without, its words
  (`mcp <Tab>` → start, stop …), a file only where one goes, a command after `time` / `xargs`.
  Where only a free value fits (a PATTERN, an IP) Tab on an empty word offers nothing. A usage
  line it can't read, or words that don't fit it, keep the old completion.

- **`diag keys --raw`:** for a key that `diag keys` does not show at all (Alt-v on some Windows
  setups). On Windows it prints every key record the console delivers, key down and key up, with
  the virtual key, scan code, character and modifier flags, before prompt_toolkit reads them;
  `--vt` turns the console's VT input on first. On Linux it prints the raw bytes of each key.
  `--mouse` shows only the mouse instead: each click and wheel step, with mouse reporting
  turned on (Windows: the raw mouse record, with the wheel delta; elsewhere the SGR report).

- **A saved MCP token:** `mcp start` used to make a new bearer token every time, so clients had to
  be added again. The token is now made once and saved (in the OS keyring when there is one,
  else in `config.json`, like an API key), and so is the address of the last `mcp start`.
  `mcp show` prints the token and the `claude mcp add` line; `mcp token rotate` makes a new token,
  restarts a running server with it, and prints what to run in the client:
  `claude mcp remove shellcraft`, then the new `claude mcp add` line. The `claude mcp add` line is
  no longer broken in two by the shell's line wrapping, so it copies as one line.

- **A review loop with an AI** (`mark`, `review`, and four MCP tools). You flag output for the AI
  with `mark` (`pytest | mark --type test-failure`, `mark --file F`, `mark --diff A B`); it reads
  them with `get_flagged_items` or the `shellcraft://review/queue` resource and closes them with
  `resolve_review_item`. Before a destructive action the AI calls `request_human_review`: the
  request shows in the footer as an alert (info / warn / crit for a low / medium / high
  priority) until you answer with `review approve ID [NOTES]` or `review reject ID [NOTES]`; the
  AI waits for the answer (`wait_seconds`) or asks again with `get_review_status`. The items are
  JSON files in `~/.shellcraft/review/` (owner-only), written atomically and changed under a
  per-item lock, so the shell, `mcp start`'s server and every client's own `--mcp` server can
  share them; connected clients are notified when the queue changes. Each piece keeps its last
  256 KB; done items are deleted after 7 days. Advisory: the server instructions tell the AI to
  ask, nothing stops its own tools.

- **`hotkeys`:** every keyboard shortcut and mouse action, by where it works (the prompt, the
  clip and alerts, the footer and subshells, scrolling and selecting, the viewer), with what
  differs between Linux and Windows. `man hotkeys` opens it in the viewer, `help` lists it among
  ShellCraft's own commands and its footer points to it, and the README has the same tables
  under "Keyboard and mouse".

- **Clip history:** the clip register keeps the last ten copies, newest first: a right-click on
  a second alert no longer loses the first. `$CLIP` is the newest, `$CLIP1` the one before, up to
  `$CLIP9`; **Alt-v** pastes the newest and, pressed again right away, swaps in the next older one
  (like Emacs's yank-pop). `clip -l` lists them, `clip N` prints one, `clip -i N` starts the next
  prompt with it, `clip -c` clears them all. Shared with `sub shellcraft` over the main link, in
  memory only.

- **The scroll view** (`settings scroll_view on`): the wheel at the prompt opens the session's
  recent output (the last 5,000 lines, colors kept, with the commands that produced them) in the
  viewer, at its end; wheel down past the end, or q, closes it. It lives in memory only, and
  `clear` empties it. Off by default: the first turn of the wheel hands the mouse back to the
  terminal, which scrolls its own history as any terminal does, and the next key press takes the
  mouse again for the footer.

- **`diag`** also shows each job's recording flag and the `record` signals received from
  subshells, for a recording bubble that does not turn red.

- **`diag keys`:** a key test that shows what each key press reaches the shell as (`escape v`
  is Alt-v), and on Windows the console's raw flags (left-alt, right-alt, left-ctrl, shift).

- **`theme --sub NAME`:** a theme for every subshell (`sub shellcraft`), saved in `config.json`
  as `sub_theme`. A subshell starts with it; without one it starts with the main theme, as
  before. `theme --sub` shows it and `theme --sub --clear` (or `off`) removes it. It works from
  the main shell and from a subshell.

### Changed

- **`~/.shellcraft/startup/` is now ShellCraft's `~/.bashrc.d`**: a folder of `.sc` files, each
  written like the rc file (ShellCraft commands, one per line) and doing one job. They run right
  after `~/.shellcraft/rc`, in name order, inside your shell, so what one sets (`env set`, `cd`,
  an `input()` answer) the next one and your prompt see. `_name.sc` files are helpers you `source`.
  A profile can have its own `profiles/NAME/startup/`, run after its rc at start and on
  `profile use`, and carried by `profile export / import / copy / save`. `startup` lists the files
  with their last result; `startup run [NAME]` runs them again.

- **The footer's close button is on the right, and bigger.** An alert's row no longer starts with
  an icon (its color says how serious it is); like a finished subshell's row, it ends with a heavy
  `✖` that closes it. The finished subshell's `×` is now the same, more visible `✖`.

- **Every way of running ingest puts new modules in `~/.shellcraft/modules`**: the `ingest` command,
  `shellcraft ingest` and `python -m tools.ingest`. `shellcraft ingest` used to write into the bundled
  folder (inside the installed package for an installed copy) and `python -m tools.ingest` into the
  checkout. `--modules DIR` still picks another folder. `ingest check --all` checks every module the
  shell loads, the bundled ones and yours; `--modules DIR` narrows it to one folder.

- **ingest's AI code is one file**, `tools/ingest/ingest_AI.py`: the providers and models, the HTTP
  calls, both prompts (they were `prompts/*.md`) and the wizard's AI steps (choosing the provider and
  key, asking before sending, converting, finishing the docs). Upgrading the AI side means editing
  that file only. A typed-in key is now asked on the wizard's own console (hidden).

- `modkit.error_stream()` text, from a module or a builtin, follows the command's own `2> FILE` and
  `2>&1`.
- The module checker accepts a module that shadows an OS program on purpose (its page names
  `command NAME`).

- **A faster start.** The banner is printed before the prompt library loads, so it appears in about
  60 ms instead of 160 ms, and the prompt follows sooner (145 ms instead of 167 ms on Linux; `sub
  shellcraft` 141 ms instead of 161 ms). About 90 fewer modules load before the prompt (the Markdown
  renderer now loads on the first `man`), the module registry no longer resolves the path of every
  loaded Python module (15 ms → 2 ms, much more on Windows), and API keys kept in the OS keyring are
  read in the background (the first command waits for them only if it must). `python main.py
  --timing` prints how long each startup phase took, starting from when the process was created;
  `diag` keeps the last one.

- **`env` alone lists only the variables set in this session** (`env set`, `export`, the rc
  file), with their full values and where they came from. `env -a` (`--all`) prints the whole
  environment, as `env` did: `env -a | grep -i proxy`.

- **A new demo recording** (`docs/demo.gif`, `docs/demo.cast`) of the shell as it is now: the
  usage-guided completion, a subshell in the footer, an alert whose data Alt-v pastes, the man viewer.
  `python tools/demo.py` records it again from a script, in a throwaway home with sample files.

- **The README is an overview now** (features, install, quick start, a short tour, the command
  and module lists); the full guide moved, unchanged in substance, to `docs/`: scripting, subshells,
  keyboard and mouse, settings and themes, MCP and the AI co-pilot. The command list names every
  builtin again, and the count is 80+.

- **`diag` is hidden** from `help`, Tab completion and the command line until the
  `diagnostics` setting is on. `shellcraft --diag` (`python main.py --diag`) turns the setting on,
  saves it and prints the report at startup; `settings diagnostics off` hides `diag` again. The
  setting no longer prints the report at every start.

- CI lints and type-checks `shellcraft` (it was still pointed at the old `core` folder), and
  DEVELOPMENT.md lists every module and test file again.

- **`theme NAME` in a subshell changes that subshell only:** it used to save the theme, so the
  main shell opened with it next time. The message says so and points at `theme --sub NAME`.
  `theme reset` in a subshell is not saved either. In the main shell nothing changes.

- **`alert` is quicker to type:** the levels are `info`, `warn` and `crit` (the long `warning`
  and `critical` still work, from scripts, older spool files and AI clients), the data switch is
  `-d DATA` / `--data DATA` instead of `-C`, which was easy to mix up with `-c` (clear), and the
  usage line the toolbar shows while you type names the levels:
  `alert [-l LEVEL (info,warn,crit)] [-t SECONDS | -s] [-d DATA] MESSAGE... | --list | -c [N]`.
  Alert rows end with `crit` and `warn · 57s`; `alert --list` shows `[warn]` and `[crit]`. The
  `send_tui_alert` MCP tool takes `warn` and `crit` too.

- **The footer is clickable at the prompt again by default** (`footer_mouse` now on): while it
  lists a subshell or an alert, its lines, an alert's ✖ and the badge answer the mouse, and text
  is selected with Shift and a drag. `settings footer_mouse off` leaves the mouse to the terminal.

### Fixed

- `env set NAME=~/path` and `export NAME=~/path` store your home folder's path, as bash does, instead
  of a literal `~`.

- The SOC starter profile's `top10` alias held a pipe, which aliases can't, so it was skipped. It is
  now a function in the profile's rc.

- **ingest: a variable your script fills in now starts fresh on every run.** A top-level
  `counts = {}` (or `total = 0`) that the moved code changes (`counts[w] += 1`, `items.append(x)`,
  `total += 1`) stayed at module level, so a second run kept the first run's data (or, for
  `total += 1`, failed). It now moves into `run()` with that code, unless a function reads it.

- **`ingest` inside ShellCraft no longer writes over the last line** near the bottom of the screen
  (seen on Windows). The wizard printed through its own console and asked through Rich's default
  one, neither of them the shell's; it now prints and asks through the shell's console, like every
  other command. Its closing hints name `ingest check` there, not `python -m tools.ingest check`.

- **The startup banner is as wide as the window again.** The early banner (printed before the
  shell loads) let a stale `COLUMNS` win, and on Windows fell back to 80 columns when stdout couldn't
  be measured, so it came out narrow with the small title. It now measures the window the way the
  shell does (`shellcraft/termsize.py`), so it matches the `banner` command.

- **Ctrl-C could close ShellCraft.** A Ctrl-C landing at the wrong moment (a second one while the
  first was being handled, or while the next prompt was starting — likelier on Windows) escaped the
  REPL loop. The loop now only ends on Ctrl-D or `exit`: Ctrl-C anywhere is `^C interrupted`, a
  Ctrl-C between commands is dropped (nothing is running for it to stop), a prompt that fails falls
  back to a plain one after three tries, and any unexpected error is shown and logged to
  `~/.shellcraft/crash.log` (`diag` shows the last one) instead of ending the session. Ctrl-C during
  startup skips the rest of startup; in an rc file it stops that file; in a func, that func.
  Ctrl-Break on Windows works like Ctrl-C instead of killing the process.

- **A module calling `sys.exit()` hung the shell** (plain `argparse` does on `--help` or a bad
  argument): the spinner ran forever. `sys.exit(0)` now ends the module normally and any other exit
  is an error naming the status, in the shell and over MCP; a module exiting at import time is a
  warning. A func that runs itself stops after 16 levels.

- The prefix key and the next key in one read (a fast double press) lost the second key; the
  prefix twice now sends one to the program, and the prefix then another key returns to the prompt,
  as when the two keys arrive apart.

- **Windows: the key reader is chosen by what the console accepts, not by `WT_SESSION`.**
  Since 13 October Windows Terminal got the classic key reader whenever `WT_SESSION` was set.
  A shell opened through Windows 11's default-terminal handoff runs in Windows Terminal with no
  `WT_SESSION`, so the same window could run either reader, and bugs looked random. ShellCraft
  now does what prompt_toolkit's own `create_input()` does: it asks the console for VT input and
  uses the VT reader when it takes it, the classic one otherwise. `SHELLCRAFT_CONSOLE=legacy-keys`
  still forces the classic reader, to compare.

- **Windows: the mouse inside an attached subshell.** The console only hands clicks and the
  wheel to a program with mouse input on and Quick Edit off; the prompt set both, the subshell
  attach set neither, so the wheel and the footer's lines could do nothing there.

- **`diag` lists the optional packages** (`pywinpty`, `pyte`, `mcp`, `keyring`, `requests`) with
  their versions or `missing`, and repeats the banner's warnings for the missing ones, so a
  report shows at once when subshells or the footer are off because a package is absent.

- **cmd.exe's own commands (`dir`, `copy`…) refuse an argument with `"`, `%`, `!` or a line
  break:** cmd.exe parses its command line again after Python quotes it, so such an argument
  could run a second command or expand a variable. Everything else, including `&`, `|` and
  spaces, is passed as one quoted argument as before.

- **The MCP server's parent watch on Windows** declared no types for `OpenProcess` /
  `WaitForSingleObject`, so a 64-bit process handle could be truncated and the wait miss.

- **A subshell left with `exit` stayed in the footer** with a × to click: the pty closes a
  moment before the job is marked finished (on Windows up to half a second later), so the
  prompt's autoclear saw it still running. It now leaves the footer as soon as it ends
  (`jobs_autoclear` off keeps it listed as done, as before).

- **The recording bubble was a dim red on Windows:** it is a bright red there now; Linux keeps
  the theme's red.

- **The completion menu took no clicks and no wheel on Windows:** with the classic key reader the
  mouse arrives as Windows mouse events, and prompt_toolkit maps those onto the screen only for
  its own Windows outputs, so with ShellCraft's console output it dropped every one. The shell now
  maps them itself: a click picks a completion and the wheel scrolls the menu, as on Linux.

- **The wheel did nothing in the viewer on Windows 11** (the scroll view, `man`, `less`), with the
  legacy and the direct key reader alike: the Windows console reports wheel events at screen
  coordinates, which the viewer's layout did not match. The viewer now takes every wheel event
  itself, wherever the mouse is. The scroll view is also bottom-aligned, like the terminal: output
  shorter than the screen sits at its bottom, so opening it looks like scrolling up from the
  prompt.

- **A footer bubble stayed highlighted:** the line under the mouse lights up (its bubble filled,
  in the accent color) to show what a click picks, but it stayed lit after the click and after
  the mouse left the footer. It now goes back to its normal color when the mouse moves off the
  footer, after a click that acts, and after one second without the mouse moving over it.

- **Alt-v with the right Alt or AltGr on Windows:** prompt_toolkit's classic Windows key reader
  turns only the left Alt into the Escape prefix that means Alt, so right-Alt+v typed a plain `v`,
  and AltGr+v (right Alt + Ctrl to Windows) became Ctrl-V. ShellCraft's reader now counts the
  right Alt and AltGr with a letter or a digit as Alt, unless AltGr types a real character of the
  keyboard layout (`@`, `€`), which still types. Esc then Ctrl-V (Ctrl+Alt+v, how AltGr+v arrives
  through the VT key reader and Linux terminals) pastes too.

### Removed

- **The OS keyring** (the optional `keyring` package, the `keyring` setting, `settings
  migrate-keys`, `env unset -k`, the `[keyring]` extra). API keys you set with `settings NAME VALUE`
  and the `mcp start` token are kept in `~/.shellcraft/config.json` (mode 600); a variable exported
  in your own shell is never stored and always wins. `env set -s` still masks a secret and keeps it
  out of the history; in an rc file it asks at each start. The keyring cost startup time and could
  stall when the Linux Secret Service was locked or absent, and whether it was used depended on
  which Python ran ShellCraft. A key an older version kept there shows as "set it again".

- **The `core` compatibility shim** (the package's name before 0.3.0, promised to go in 0.4):
  write `from shellcraft.modkit import …`; `python -m core.cli` is `python -m shellcraft.cli`.

- `tools/winconsole_probe.py`, the Windows console probe: `diag keys` and `diag keys --raw` cover
  it, and Windows 11 support is verified end to end.

- Two functions nothing called (`arith.assignment_value`, `ask.parse_input_line`).

- The copy additions of 2.0.0: the drag highlight and right-click copy of text inside a subshell,
  copying to the host's clipboard (OSC 52 and the Win32 clipboard, `clip -H`), and Ctrl-] v at the
  prompt. Select text with Shift and use the terminal's copy; Alt-v pastes the clip (Ctrl-] v
  still types it into another program inside a subshell), and `clip -i` stays.

## [2.0.0] — 2026-10-16

### Added

- **`diag`** prints the console diagnostics (`--diag`'s report) at any time, with a new **footer
  colors** section: the escape sequence the footer writes for each state (running, done, failed,
  the recording bubble, the alert marks), whether running / done / failed look different in the
  theme, the footer's rows as last drawn, and the live state of every job (running, status, and
  for a Windows pseudo-terminal whether pywinpty says the program is alive). For a footer whose
  colors do not change: run it with a subshell running and again after it ended, and paste it.
- **Copying to the host's clipboard:** a right-click on an alert, `clip -a N`, Alt-c and a text
  selection in a subshell now also put the text on the host's clipboard, with the terminal's
  OSC 52 sequence (Windows Terminal, kitty, WezTerm, foot, iTerm2, Alacritty and tmux honor it;
  it works over ssh) and, on Windows, the Win32 clipboard; no external program. `clip -H [TEXT]`
  copies text, piped input or the register there on demand.
- **More ways to paste the clip:** **Ctrl-] v** at the prompt (it does not depend on how a
  terminal sends Alt; Alt-v on a Windows console needs the left Alt key) and `clip -i`, which
  starts the next prompt with the text on the line, no key chord at all.
- **`jobs -r N NAME`** renames a subshell: the footer, the `jobs` table and the `[N] done` note
  show the name instead of the command (`jobs -r N` alone puts the command back).
- **`record` / `script`:** a transcript of the session, like Linux `script` without the escape
  codes: every command with its time and what the shell printed, into
  `~/.shellcraft/records/session-DATE.txt` or a file you name (`record notes.txt`); `record
  status`, `record stop` (leaving the shell stops it too). The footer shows which shell records:
  its bubble (● or ○) turns red, on main's line or on the line of a `sub shellcraft` that runs
  `record`. The footer and what happens inside an attached subshell are not recorded.
- **Core warnings in the banner:** like a module's missing import, the banner (and the start
  without one) now says what the shell itself is missing: pywinpty on Windows (no subshells on
  a pseudo-terminal, so no footer), pyte, the `mcp` package, keyring, each with the pip command.
- **`sub` inside a subshell:** instead of being refused, it hands the request to the main shell
  (through the alert spool), which opens the subshell in the background under `main`: it
  appears in the footer, you stay where you are, Ctrl-] N switches to it. `cmd &` inside a
  subshell goes the same way. Every subshell belongs to main; there is no subshell inside a
  subshell. `sub sc` is short for `sub shellcraft`, and `sc &` starts one in the background.
- **The footer at the prompt too:** the shell stack (`● main`, then one line per subshell,
  newest at the bottom) now sits on the last rows of the terminal at the main prompt as well as
  inside every subshell, growing as subshells open (up to half the screen, then "… and N
  more"), with `main` there even when nothing runs. The prompt and its menus stay above it.
  **Ctrl-] c**, or a click on the `▾` after `main`, folds the subshell lines into
  `main  3 subshells ▸` and unfolds them. `settings footer off` goes back to a panel under
  the prompt only while something runs.
- **`shellcraft --lite`**, what `sub shellcraft` now runs: the builtins, modules, settings,
  aliases and history, without the banner, the clear on start, the startup scripts and rc, hot
  reload, the alert spool watcher (a nested shell used to take alerts meant for the main one)
  or the MCP server.
- **Alerts with a payload, and the clip register:** `alert -C CONTENT MESSAGE` (or `-C -` for
  what was piped in; `content` in the `send_tui_alert` MCP tool, `shellcraft-alert -C`) keeps up
  to 64 KB with the alert: a path, a command, a URL, JSON. A **right-click** on the alert's row
  copies it (or the message) into the shell's own clip register, and the row says `copied`.
  **Alt-v** (or Esc then v) pastes it at the prompt, **Ctrl-] v** into an attached subshell, the
  new `clip` builtin prints or sets it (`clip | jq`, `echo x | clip`, `clip -c`) and `$CLIP`
  holds it. Nothing touches the system clipboard, so it works in every terminal and over ssh.
- **`jq`:** a builtin that looks inside JSON with Python's `json.loads`: `cat f.json | jq`
  pretty-prints, `jq f.json -k` lists the keys, `-f ['name']` / `-f [5]['name']` / `-f .items[0].id`
  picks a value, `-i N` starts inside element N of a list and `-i *` goes through every element
  (`jq -i * -f ['name']`, `jq -i * -k`). `-r FILE` reads a file, `-c` prints compact JSON, `-n`
  counts. `jq` looks at what it got first: on screen a bare `jq` starts with a line saying the
  shape and what fits it (`# list of 3 objects (keys: name, age) · jq [0] · jq -t · jq -i '*'
  -f name`), `-s` prints just that line, and a path on its own (`jq [3]`, `jq .items[0]`) works
  like `-f`, so a list is indexed the way Python does it. JSON Lines, Python-style
  dictionaries and XML (`-x`, or by itself when the input starts with a tag: converted to
  JSON-style data first, attributes as `@name`, repeated tags as lists) are accepted. Words on a `jq` line are not
  globbed, so `*` and `[name]` arrive as typed. It is not the jq language, and it shadows a jq
  program on the PATH (run that one by its full path, such as `/usr/bin/jq`).
- **`jq -w`, `-t`, `-p`:** `-w COND` keeps the list elements that match (`-w 'age>30'`,
  `-w name~ad` for contains, `-w active=true`, `-w email` for "has the key"; more than one must all
  match; status 1 when none do). `-t` shows a list of objects as a table, tab-separated in a pipe.
  `-p` prints every value with its path (`.items[0].id = 7`), and each path works as a `-f` filter.
- **`jq` for deeply nested data** (dict -> list -> dict -> list, any number of levels):
  `-s` (and the header on screen) describes data nested four levels or more as an outline of its
  deepest branch (`object{company} -> company: object{name,departments} -> departments: list of
  2 objects{name,teams} -> …`) and a chain (`dict -> dict -> list[2] -> dict …   (11 levels)`);
  the header then suggests the switches that fit. `-R KEY` / `-f ..KEY` finds a key at any depth,
  `..` is every value, `[*].items[*].tags[*]` goes through many levels in one filter, `-W COND`
  finds the objects (or, with no path, the scalar values) that match at any depth (`-W
  'status=error'`, `-W '~timeout'`), `-F` flattens to `a.b[0].c = value` lines (with `-t`, one flat
  row per record, a column per nested key), `-d N` stops at N levels (`{…3 keys}`, and `-k -d N`
  is the key outline `items[*].tags[*].label`), `-P` prints the path of each result. A filter
  that does not fit the shape says what is there and what to type (`'name' is inside each
  element; try .company.departments[*].name`, `'level' is deeper: try -R level`, did you mean).
  Everything walks the data without recursion, so thousands of levels and hundreds of thousands
  of values are fine. A number in a condition (`-w 'age>30'`, `-W '>3'`) now compares with
  numbers only (text that reads as a number counts), instead of by text order.
- **Background startup scripts:** `# shellcraft: background` at the top of a script in
  `~/.shellcraft/startup/` starts it as a subshell when the prompt appears, so it never slows the
  start; `# shellcraft: restart` restarts it after a crash (2 s, 4 s … 60 s; gives up with a red
  alert after 5 crashes in 10 minutes); `# shellcraft: timeout=N` gives a normal script more time.
  `startup stop [NAME]`, `startup start NAME`, `startup restart NAME`. Example in
  `templates/startup/30-watcher.example.py`.
- **Ending a subshell:** Ctrl-] x inside an attached subshell ends it and returns to the prompt;
  `x` or Delete on the chosen line of the subshell panel stops that one.

- **Following a background command:** `jobs -f N` prints what a command job has written so far and
  then each new line as it comes, like `tail -f`, until the job ends (its status line) or Ctrl-C
  (the job keeps running). Selecting a still-running command job (`jobs N`, Ctrl-] N, a click)
  follows it the same way; a finished one shows its whole output. Background commands record
  their output as it streams, so `tail -f app.log | grep ERROR &` can be followed.
- **`cmd &`:** a trailing `&` runs the line in the background, like `sub cmd`: a builtin, module,
  pipeline or loop as a command job (the line as typed, quotes kept), a single OS program as a
  program job that is not attached (Ctrl-] N switches to it). Interactive shell only; an `&`
  anywhere else is still a parse error, which now says only a trailing one is supported.
- **Streaming pipelines:** every command of a pipeline runs at the same time, and lines flow to
  the next one as they are made. `yes | head -3` returns at once (the producer is stopped when the
  reader has enough), `tail -f app.log | grep ERROR` prints each match the moment it is written,
  and output that keeps coming is printed live (Ctrl-C stops it, status 130). A pipeline that
  finishes quickly looks exactly as before (colors, the pager). `cat`, `grep`, `head`, `tail -f`,
  `wc`, `tee` and the new `yes` read their input as it arrives; OS programs in a pipeline stream
  through their pipes; a module whose `run()` yields lines streams too. Builtins opt in with
  `@builtin(..., streams=True)` and get `core.streams.Lines` as stdin.

- **Shell functions:** `name() { …; }` and `function name { …; }`, on one line or several (at the
  prompt and in rc files), with `$1`…`$9`, `$#`, `$@`, `$*`, `local NAME[=VALUE]` (put back when the
  call returns), `return [N]`, recursion (100 calls deep at most), and use as a command in pipes,
  redirects and `&&` / `||` (a function's commands read its input). A function takes precedence
  over a builtin or module of the same name, as in bash, except `exit`, `export`, `unset`, `source`,
  `.`, `local`, `declare`, `alias` and `unalias`; `\name` skips it. `declare -f` / `declare -F` list
  them, `unset -f` removes one, `type` and `which` report them; never available over MCP. `$1` and
  `$#` also work inside `$(( ))`, as do `$x` and `${x}`.
- **`select` menus:** `select NAME in WORD...; do …; done` shows a numbered menu, asks with `$PS3`
  (default `#? `), sets NAME to the chosen word (empty for an invalid answer) and `$REPLY` to what
  was typed, and repeats until `break`, Ctrl-D or Ctrl-C (status 130). An empty answer shows the
  menu again. Interactive shell only (`man select`).
- **`&&`, `||`, `!` and `( … )` inside `[[ ]]`**, with bash precedence (`&&` binds tighter):
  `[[ -f $f && ( $env == prod || $env == staging ) ]]`. The script splitter no longer cuts a
  statement at an `&&` / `||` that is inside the brackets.
- **`>&2` and `1>&2`:** a command's output goes to the error stream: `echo "error: bad input" >&2`
  shows in the error style on screen and leaves `$?` alone; with `2> file` it goes to the file,
  with `2>&1` it stays output. Inside a function or a loop it follows the call's or the block's
  own redirection (`myfn 2> err.txt`, `done 2>&1 | grep …`), as do the body's failing commands. A
  background `sub` job keeps it in the job's output. Without a screen (MCP) it goes to the
  process's stderr. Other `>&` forms are still rejected, with a message saying which work.

- **`"$@"`, `shift` and default values in functions:** a quoted `"$@"` gives one word per argument
  (prefix and suffix join the first and last; no arguments, no word), in commands and `for` lists;
  `shift [N]` drops arguments (status 1 past the end); `${NAME:-word}` and `${NAME-word}` give a
  default, everywhere variables expand.

- **Comparisons inside `[[ ]]`:** `<`, `>`, `<=` and `>=` compare text order (bash's two, plus
  Python's `<=` / `>=`), and Python's `in` / `not in` (contains) and `is` / `is not` (plain text
  equality) work there too: `[[ err in $line && $a < $b ]]`. Outside `[[ ]]`, `<` and `>` are
  still redirections.

### Changed

- **No Shift for the wheel and for selecting text:** while a program has the mouse, a terminal
  keeps its own wheel scrolling and text selection for Shift. ShellCraft no longer takes the mouse
  at the prompt when an alert shows (that put the wheel and selection behind Shift again, and
  made the wheel step through the history). At the prompt the terminal keeps it, so its wheel and
  its selection work as usual; `settings footer_mouse on` gives the footer's lines, an alert's ✖
  and the badge their clicks back at that price. Inside an attached subshell, where the footer
  needs the mouse, ShellCraft now supplies both itself: the wheel scrolls the subshell's page
  (and sends arrow keys to a full-screen program such as `less` or `man`), a left-button drag
  highlights text and a right-click copies it to the clipboard. A mouse report cut in two reads
  is held for the rest instead of being typed into the program.
- **The wheel scrolls the output again:** at the prompt the mouse is no longer reported to the
  shell just because a subshell or an alert is listed (that took the terminal's wheel and text
  selection away). The footer's lines, an alert's ✖ and the badge are clickable at the prompt
  with the new `footer_mouse` setting (off by default); inside a subshell they always are, and
  the keys (Ctrl-] N, ↓, Ctrl-] c, alert -c) work everywhere.
- **A new `help` screen:** grouped by purpose, at most 100 columns wide, no table. ShellCraft's
  own features (`sub`, `jobs`, `alert`, `clip`, `record`, `mcp`…) come first with a short
  description; Shell, Files, Text, Scripting and System are grids of names; the modules come
  last, in their own color, with the first clause of their summary (◆ marks your own).
  `help -a` describes every command; `help -k WORD` searches; `help NAME` and `man NAME` have
  the usage. In a pipe it is still one `name  summary` line per command.
  `local`, `shift` and `declare`, which only make sense inside a function, are not listed;
  `man function` has their pages now (each still has its own `man` page and completes).
- **The pager only when asked:** output taller than the window no longer opens the viewer by
  itself; it prints, so you can scroll back. `man` still opens it, and the new **`less`** (and
  `more`) page anything on demand: `ls -l | less`, `cat big.log | less`, `less FILE`. The
  `pager` setting now means: open man pages and `less` in the viewer (off: print them).
- **Footer lines:** a running subshell's line is plain (it was amber); the main line's color
  runs to the right edge like every other; a finished line has an × after `done` / `exit N`
  that removes it with a click.
- **Finished jobs clear themselves:** `sub ls` or `make &` used to stay in the stack as `done`
  until `jobs -c`. A job that ended is now reported at the next prompt (`[N] done: …`, plus a
  command job's output) and then leaves the stack, like a finished `ls &` in bash. `settings
  jobs_autoclear off` keeps the old behavior.
- **The stack is no longer cut at 8 subshells:** it may take up to half the screen. On
  Windows, clicks on the footer at the prompt are mapped from screen-buffer rows to window
  rows, so they land on the right line when there is scrollback.
- **`mcp start` waits up to 45 s** (was 15 s) for the server to start listening: a cold start
  that imports mcp and uvicorn on a slow disk or a busy machine sometimes needed more.
- **Alerts fold into the panel:** an alert sent from a subshell shows inline on that subshell's
  line, after its command, in its color; a badge at the right end of the `main` line counts every
  alert by level (`● 1  ● 1  ● 1 ▸`, a filled bubble in each level's color) and a click on it unfolds one row per alert (another folds
  them back). A critical alert always keeps its full row. Every level has the same ✖ mark, in
  its color, and a click on it dismisses the alert. Alerts without a subshell to fold into (no
  `sub` running) still show as rows. The `⤷` glyph is gone from the footer, the prompt (`[1]`
  inside `sub shellcraft`) and the job notes: many console fonts have no such character.
- **Shorter subshell lines:** the elapsed time and the word `running` are gone from the right
  end of a subshell's line (its color is the state; `jobs` has the times); a finished one still
  says `done`, `exit 2` or `killed`.
- **Shorter subshell panel:** the header line with the keys (`⤷ 1 subshell · click or Ctrl-] N
  switches · …`) is hidden, so the panel and its alerts take one row less. `settings panel_hint on`
  brings it back.
- **`jobs -k` stops a background command for real:** each background command job has a stop flag
  that every pipeline it runs inherits, so endless producers, `tail -f`, `sleep`, loops, modules
  checking `modkit.cancelled()` and quiet OS programs (killed by a watcher) all stop, and its
  threads end; before, it was only marked killed and kept running until the shell exited.
- One cancellation mechanism: a pipeline segment's stop flag is also `modkit`'s cancel event, so a
  module checking `modkit.cancelled()` stops when `head` has its lines or on Ctrl-C.

### Fixed

- **Footer lines never changed color on Windows:** a program in a subshell (`sub pwsh`, `sub
  shellcraft`) stayed `running` for good once it exited. pywinpty's `read()` is a blocking
  recv that gets no EOF when a ConPTY program exits (the pipe stays open until the pseudo
  console is closed), so a program that ends without printing anything more, such as `exit` in
  pwsh, never ended its job: the line stayed plain, never green or red, the `[N] done` note never
  came, and attaching waited on it. The reads now run in a helper thread while the job asks the
  process itself whether it is alive (`isalive()`, every 0.1 s), then closes the pipe to release
  the reader. Also on Windows: an exit status pywinpty reports late (`None`) is waited for
  instead of becoming 0, so a failing program is red `exit N` and not green `done`; a job end is
  reported once however it is noticed; typing into a program that has just ended no longer
  raises an `EOFError` in the attach loop; and a killed job is always red, whatever status the
  terminate left. Verified on Linux against a fake pywinpty process that behaves as its source
  says; the ConPTY itself could not be run here (`diag` shows what it reports).

- **Footer clicks inside a subshell stopped now and then:** a program in the subshell that turns
  its own mouse reporting on and off (the lite ShellCraft's completion menu and pager, `less`,
  `vim`, `htop`) passed those switches straight to the terminal, so the footer could lose the
  mouse until the next one. While a subshell is attached the footer keeps the mouse: the
  program's mode switches are replaced by the footer's (clicks in SGR form, plus the motion the
  program asked for), and its mouse events are handed to it in the form it asked for.
- **The clip register inside `sub shellcraft`:** a right-click on an alert is handled by the main
  shell (it draws the footer), so the copy went to the main shell's register and `clip`,
  `$CLIP` and Alt-v in a lite subshell found nothing. A lite subshell now asks the main shell
  for its register over a private channel (a local socket, a named pipe on Windows) locked with
  a random key that only `sub shellcraft` is given; the register stays in the main shell's
  memory, never on disk.
- **Right-click on an alert at the prompt:** since the wheel fix the prompt never asked for the
  mouse, so the terminal kept right-clicks and the ✖ and the badge did nothing. (Superseded: the
  prompt now leaves the mouse to the terminal again, see Changed; `settings footer_mouse on`
  brings those clicks back.) New keyboard ways: **Alt-c** copies the top alert, `clip -a N`
  copies alert N.
- **A blank toolbar row:** the usage toolbar stayed on into the next prompt (the input buffer
  still held the last command when the toolbar was decided) and drew as an empty colored row;
  with the footer at the prompt it sat right above it. The buffer is cleared first now. A
  footer that gets shorter (a fold, a subshell gone) clears the rows it gave up.
- **Alert clicks:** with alerts showing and no subshell, the prompt left the mouse to the
  terminal, so the × (and its hover) and the right-click did nothing; the mouse is now on
  whenever the panel shows. On Windows a release is reported without its button
  (`RIGHT;MOUSE_DOWN`, then `NONE;MOUSE_UP`), so the right-click copy and the badge click never
  fired; both now go by the button that was pressed, at the prompt and in a subshell's footer.
- **`clear` on Windows 11:** `clear` (and `banner -c`) now clear the screen and the scrollback
  the way the startup clear does, by writing the terminal codes directly. Before, they used Rich's
  clear, which Windows Terminal turned into scrolling the old text out of view, or skipped.

## [0.3.0] — 2026-10-13

Subshells and the subshell panel, alerts, the rc file and startup scripts, the aliases file,
mouse support, a user modules folder, the `shellcraft` package name, API keys in the OS keyring,
and Ctrl-C that reaches running modules.

### Changed (robustness and packaging)

- **The package is `shellcraft` now, not `core`** (a generic name that could collide with other
  packages in site-packages). Module authors: write `from shellcraft.modkit import …`. A `core`
  compatibility shim stays for the 0.3 releases: `import core.X` returns the very same module as
  `shellcraft.X` (with a DeprecationWarning), and `python -m core.cli` / `python -m core.alerts`
  still run, so modules and alert shims from 0.2 keep working. It is removed in 0.4.

### Added (robustness and packaging)

- **Ctrl-C reaches running modules:** `modkit.check_cancel()` (raises `Cancelled`, a
  `ModuleError`), `modkit.cancelled()` and `modkit.sleep()` (wakes up on cancel) let a module
  stop when the user presses Ctrl-C or an MCP call times out; the shell sets the worker thread's
  cancel event in both cases. The bundled `fetch`, `ip2geo`, `queryDns` and `queryCert` check it
  between targets and retries. With the spinner off, Ctrl-C now interrupts at once instead of
  after the module finished.

- **API keys in the OS keyring:** with the optional `keyring` package (the `keyring` extra) and a
  working backend, `settings NAME VALUE` stores the key in Windows Credential Manager / macOS
  Keychain / Secret Service and `config.json` keeps only `@keyring`; startup reads it back, `reset`
  deletes it. The `keyring` setting (on) chooses; `settings migrate-keys` moves keys stored in plain
  text, and the `settings` table warns while any are left. Without keyring nothing changes, and the
  README now says plainly that `config.json` is plain text and mode 600 does nothing on Windows.

- **Your own modules folder, `~/.shellcraft/modules`,** loaded next to the bundled one (or
  `--modules DIR`). A module there shadows a bundled one of the same name; `modules` has a `from`
  column and `help` marks them `(user module)`; hot reload watches both folders; `_helpers` import
  across both (yours first). `ingest` inside the shell now writes there instead of into a checkout.

### Added

- **Subshells:** `sub` opens your own shell, `sub PROGRAM` hands the terminal to any program
  (`vim`, `htop`, `ssh`, `python`…), `sub shellcraft` nests a ShellCraft (its prompt shows the
  depth), and `sub COMMAND` runs a builtin or module in the background with its output collected.
  Each is a numbered job. At the prompt a **panel** under it lists them one per line (the line
  under the mouse lights up, a click switches, ↓ moves into the panel and Enter picks); inside a
  subshell the same panel sits at the bottom of the terminal (a scroll region keeps it in
  place), clickable too, with the ● on the shell you are in. **Ctrl-] N** switches from anywhere, **Ctrl-]**
  alone comes back. `jobs` lists, selects, stops (`-k N`) and clears (`-c`) them, finished jobs
  are announced at the next prompt, and the `footer` setting turns the strip off. Each program
  has its own terminal emulator (`pyte`), so switching to it redraws its screen exactly,
  scrollback included; subshells are shown on the alternate screen, so the prompt comes back
  untouched; the wheel scrolls a subshell's scrollback; on Windows programs run on ConPTY
  through `pywinpty`. Programs run on a pseudo-terminal (POSIX); on Windows
  `sub PROGRAM` runs in the foreground. A `jobs` prompt segment shows how many are running.
- **The completion menu follows the mouse:** the entry under the pointer is highlighted (and put
  on the line, as ↑ / ↓ do), so a click picks what you see.
- **`ingest` inside the shell and `shellcraft ingest` from a terminal.** The module wizard and
  its checker were only reachable as `python tools/ingest`. The `ingest` builtin runs them in
  place with the shell's own modules folder, and a new module works at the next prompt;
  `shellcraft ingest` does the same from a terminal (the wheel now ships `tools.ingest`).
- **Mouse everywhere it helps, in small steps.** `settings mouse_always on` keeps the mouse on
  at the prompt: click to place the cursor, click a prompt segment to act on it (`⚡` → `help`,
  path → `ls`, `⎇ branch` → `git status`, `● mcp` → `mcp log`, `[✗ N]` → the last error), click
  the toolbar for the man page of the command being typed, wheel through the history or through
  Ctrl-R matches. In the pager the status bar's keys are clickable, a scrollbar on the right can
  be clicked or dragged, and a click on a line marks it for `n` / `N`.
- **Clickable paths** (the `links` setting, on): `ls`, `tree`, `find`, `du` and `grep` print file
  names as terminal hyperlinks, so Ctrl-click opens them. Pipes and MCP still see plain names.
- **Mouse in the completion menu:** click an entry to pick it, wheel to scroll the list. The
  terminal is asked for the mouse only while a menu is open (and in the pager, as before), so
  text selection and wheel scrollback at the prompt are unchanged. The `mouse` setting turns it
  off. On Windows the console's mouse input is switched on with the menu and Quick Edit mode is
  restored when it closes.

### Added

- **Asking for a value:** `name = input("What is your name? ")`, `n = int(input("How many? "))` and
  `float(input(…))` (which ask again until the answer is a number) on a line of its own (at the prompt,
  in an rc file, in a sourced file) and `read [-p PROMPT] [-s] NAME` store the answer in a shell
  variable for later lines (`$name`). Interactive shell only; Ctrl-C cancels with status 130.
- **`~/.shellcraft/rc` and `source FILE`:** a startup file of ShellCraft command lines, run as if
  typed when the interactive shell starts (never in MCP mode): themes, `export`, `cd`, aliases,
  modules, `sub`, `alert`, multi-line `for` / `if`. A failing line is reported with its line number
  and the rest still run; `source` (or bash's `. FILE`) runs any such file by hand, so an rc file
  can pull in others (`if [ -f ~/.shellcraft/rc.work ]; then . ~/.shellcraft/rc.work; fi`).
- **Startup scripts (first version):** every `.py` in `~/.shellcraft/startup/` runs at startup in
  name order, before the first prompt. Scripts hand environment variables back through
  `$SHELLCRAFT_ENV_FILE` (`NAME=value` lines), which the shell, subshells and modules then inherit.
  Failures and timeouts (30 s) are reported without stopping the shell. `startup`, `startup run`,
  `settings startup`, `--no-startup`; an example in `templates/startup/`.
- **An aliases file:** aliases live in `~/.shellcraft/aliases`, one `alias NAME='COMMAND'` per line
  like `~/.bash_aliases`, with comments. Hand edits apply to the next command; `alias` and
  `unalias` rewrite only their own lines; `alias --file` shows the path; unusable lines are listed
  by `alias` with their line number. Aliases saved in `config.json` by older versions move into the
  file once, at startup.
- **Alerts in the status panel:** `alert [-l info|warning|critical] [-t SECONDS | -s] MESSAGE`
  shows a green, amber or red line at the top of the panel (three at most, the most severe
  first, timed out or dismissed with `alert -c`). Subshells get an `alert` command on their
  PATH, a nested ShellCraft forwards to the main one, `shellcraft-alert` works from any script,
  and AI clients call the new **`send_tui_alert`** MCP tool. Every alert names its sender
  (`sub 2 (bash)`, `mcp · claude-code`…). Senders drop a file into the owner-only spool folder
  `~/.shellcraft/alerts/`, which the shell watches: no port, no token.
- **What each file in `modules/` does:** every `name.py` is a command shown by `help`; one
  without `run()` is run as a plain Python script (`python name.py ARGS`, stdin piped, never
  imported). A `.skill` makes it an MCP tool (modules without one are local to the shell), a
  `.md` gives it a man page, and `_name.py` is a helper that modules and scripts import
  (`import _name`, `from . import _name`); editing a helper reloads the modules that use it.
- **Windows:** the prompt session is bound to `CONIN$` / `CONOUT$` explicitly, and Windows
  Terminal (`WT_SESSION`) now defaults to `SHELLCRAFT_CONSOLE=legacy-keys`, the setup that
  works there; the classic console keeps `direct`.

### Fixed

- The pager's ← / → had nothing to scroll for text output: Rich wrapped long lines at the
  window width before the pager saw them. Text now reaches the pager unwrapped (`w` wraps it).
- Inside a subshell on Windows, the arrow, Home / End, PgUp / PgDn, Insert and Delete keys were
  dropped in a console without VT input; they are now sent to the program as VT sequences.

### Changed

- The `links` setting (clickable file names in `ls`, `tree`, `find`, `du`, `grep`) is now off by
  default: accidental clicks in a listing opened files. `settings links on` brings it back.
- `clear_on_start` clears the scrollback as well (`ESC[3J`), so the shell starts on a fresh
  screen like a new program, instead of leaving the old lines above.
- The default `--mcp-http` / `mcp start` address is `127.0.0.1:38765` (was `:8765`), to stay
  clear of the ports other local tools commonly take.

### Fixed

- `help` printed `Paged(value=...)` instead of the command table.

## [0.2.0] — 2026-10-12

The fourth assessment (`UPGRADE-ASSESSMENT.md`, now in the git history) in one release: every crash
it found is fixed, the TUI got the polish it asked for, and the bash habits it listed now work.

### Fixed

- `main.py` cleared the screen before parsing arguments, in every mode: the MCP stdio stream
  started with an escape sequence and `--version` wiped the terminal. The interactive shell
  clears the screen now (the `clear_on_start` setting), nothing else does.
- Redirecting to `~nosuchuser` killed the shell with a traceback; a deleted working directory
  crashed the prompt. Both are clean errors, the prompt shows `(deleted)`, and `Shell.loop()`
  catches any unexpected exception as a last resort.
- A lone `&` was passed on as an argument (`rm -f a & ls` deleted both). It is rejected.
- `cp -r DIR DIR/sub` recursed until `RecursionError`; it refuses to copy a directory into itself.
- A malformed `config.json` section (`"env": [1]`) crashed startup in every mode; bad sections are
  dropped with a warning. Two open shells overwrote each other's config; saves now merge only the
  keys the session changed and write atomically through a temporary file.
- `grep -m 0` printed one line; a quoted `~` in a redirect was expanded; a redirect created
  missing directories; `sort -u -k N` deduped whole lines; `wc -c` counted characters (`-m` does
  now); history redaction missed `\settings`, `a ; settings KEY v` and aliases of `settings`;
  `mcp restart` truncated the previous log; slow builtins (`du /`, `find /`) showed no spinner.
- The version was defined twice; `pyproject.toml` reads it from `core.__version__`.

### Added

- **Pager:** `w` wraps long lines, `←`/`→` scroll sideways, `d`/`u` half pages, `?` lists the
  keys, the mouse wheel scrolls, the status bar names what is being paged, and search matches use
  the theme's accent color.
- **help:** `help NAME` (summary and usage), `help -k WORD`, section headers instead of a kind
  column, the usage column on wide terminals, a footer with the module count and warnings, and a
  plain layout under 80 columns. `for`/`if` are listed as keywords, not commands.
- **Prompt:** `⎇ branch` (read from `.git/HEAD`, no git needed), `⏱ 4.2s` after a slow command,
  a clock, and the `prompt` builtin to choose the segments (`prompt name path git status`,
  `prompt -r clock`). The `transient_prompt` setting collapses the prompt after Enter.
- **Input line:** syntax highlighting (known commands, operators, quotes, switches, variables;
  the `highlight` setting), a bottom toolbar with the usage of the command being typed (the
  `toolbar` setting), a styled Ctrl-R search, completion menus in the theme's colors, quote-aware
  path completion (names with spaces are quoted), positional values from `.skill` files
  (`queryCensys <Tab>` → `host cert search`), and completion for `help`, `theme preview` and `prompt`.
- **Themes:** `theme preview NAME`, `theme reset`, the light-terminal presets `solarized-light`
  and `catppuccin-latte`, `surface` / `surface_text` / `light` keys for custom themes, and theme
  warnings shown in the banner.
- **Feedback:** the spinner shows the elapsed time after 3 s and `Ctrl-C cancels` after 10 s;
  an unknown switch error shows the usage line and a "did you mean" (`grep -ignore` → `-i,
  --ignore-case`); `ls` colors archives, media, source and build leftovers by extension.
- **Commands:** `grep -r -A -B -C -w -x -o -e -L -h -H -E`, `ls -t -S -r -R -d`, `head -c`,
  `tail -c`, `tail -f`, `find -size -mtime -newer -empty -path -ipath -not/!`, `sort -h -V -s -k N,M`,
  `cat -s -A -E -T`, `tree -I --du`, `wc -m`, and the new builtins `printf`, `seq`, `xargs`,
  `basename`, `dirname`, `realpath`, `stat`, `whoami`, `hostname`, `uname`, `sleep`, `time`,
  `type`, `export`, `unset`.
- **Scripting:** shell variables (`x=1`, `$x`), `export` / `unset`, `$HOME` and other environment
  variables in the interactive shell (never over MCP), `$((…))` arithmetic, `$(…)` and globs on
  plain command lines, `while` / `until` (bash and `-py`), `case … esac`, piping or redirecting a
  whole loop (`done | sort`, `} > out`), and `!N` / `!-N` / `!prefix` / `!*` history expansion.
- **Redirections:** `< file`, `2> file`, `2>> file`, `2>&1` (a failure's message becomes the
  command's output, so `cmd 2>&1 | grep` works) and `&> file`.
- **MCP:** a per-call timeout (`SHELLCRAFT_MCP_TIMEOUT`, default 120 s) and caps on stdin and
  output; `expose = false` in a `.skill` keeps a module local (not a tool, and refused inside
  `shellcraft_pipeline`); `--expose NAME,...` narrows the tool list; `--mcp-http` requires a bearer
  token (`--token`, `$SHELLCRAFT_MCP_TOKEN`, or one generated and printed; `--no-token` opts out);
  `mcp start` generates a token per server and prints the `claude mcp add` line; the child server
  exits when the shell that started it dies, even after `kill -9`.
- **Startup:** modules are imported on first use (the `lazy_modules` setting), so a module's
  slow imports don't delay the prompt; `.md`-only edits are re-read without re-importing, and hot
  reload reloads only the changed modules; `to_text()` wraps at the terminal's width; `show()`
  renders once and sends very large plain text straight to the pager; a module's `run()` may
  return an iterator of lines.
- **Tooling:** `ruff` and `mypy` configuration in `pyproject.toml` (the `dev` extra installs
  them), CI for GitLab and GitHub (pytest on Linux and Windows for Python 3.11–3.14, lint, the
  module checker, and a packaging smoke test), and this changelog.

### Changed

- `wc -c` counts bytes, as GNU `wc` does; `wc -m` counts characters.
- `help` prints `name — summary` lines on terminals narrower than 80 columns.
- `grep` with `-r` prefixes every line with the file name, even for one directory.

## [0.1.0] — 2026-09-27

The first release: pipelines and redirection, the ported builtins, modules with `.md` manuals
and `.skill` descriptions, bash-style and `-py` loops and conditions, aliases, history, themes,
the pager, settings with API keys, the MCP server (stdio and HTTP), and the module authoring kit.
