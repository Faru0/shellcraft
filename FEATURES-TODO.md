# Features checklist: ask, enrichment, unattended rounds, fleet, arrays

Five features, built one after another. Whoever finishes an item (a person or an agent) ticks its box and
fills in its Notes: what changed, which tests and docs cover it, and anything left out. An item is done only
when the full test suite, `ruff check` and `mypy shellcraft` (199 or fewer) pass.

## Before the features

- [x] **0. `--install-ai` installs an agent and a skill of the same name**
  - Done when: `shellcraft-copilot` and `soc-triage` are installed as an agent and as a skill; a bare name selects both
  - Notes: `ai_install.catalog()` returns a list (it was a dict by name, so the skill replaced the agent); `select` and `needs` work on it. Test: `test_an_agent_and_a_skill_of_the_same_name_are_both_installed` in tests/test_ai_install.py.

## 1. `ask`: the co-pilot inside ShellCraft

- [x] **1.1 `shellcraft/claude_run.py`**: find the client, write the MCP config for this shell, run it headless, stream the text, report cost and session, stop it on cancel
  - Done when: a stand-in client in the tests gets the right arguments and config, its text streams, a cancel kills it, a missing client is one clear line
  - Notes: `Request`, `Result`, `run()` (a generator of text, tool, note and result events), `shell_server()`, `write_config()` (0600, removed after the run). The prompt goes in on stdin, not on the command line (`ps` would show it). The client runs in its own process group; a cancel or closing the generator stops the group. `own_tools` limits the client's built-in tools (`--tools`). Tests: the first part of tests/test_ask_ai.py with a stand-in `claude`.
- [x] **1.2 The `ask` builtin**: `ask QUESTION`, `-b`, `-c`, `--budget`, `--model`, `-n`
  - Done when: the prompt holds the question, the folder and the last command with its redacted output; the answer streams inline; `-b` is a `✦` job that `jobs -k` stops; `-c` continues the last question; free text with `'`, `?` and `#` needs no quotes; it is refused over MCP; `ask_budget` and `ask_model` settings work
  - Notes: `shellcraft/commands/ask_ai.py`; free text is read in `pipeline.run_line` (`ask_ai.free_text`), and the shell skips history expansion and line continuation for it. Different from the plan: (a) `ask` has its own short system text instead of the co-pilot agent's, which tells the AI to open subshells and send alerts; (b) it runs with `--tools ToolSearch`, so the client's own shell and file tools are off and everything goes through the shell (also 3 to 5 times cheaper: about $0.03 a question instead of $0.10); (c) the prompt carries the last five commands, the output of the newest; (d) it works wherever a main shell is reachable, and `-n` without one. String settings `ask_budget` and `ask_model` (`settings.TEXT_SETTINGS`). Tests: tests/test_ask_ai.py (49).
- [x] **1.3 Docs for `ask`**: `docs/ask.md`, `man ask`, help group, walkthrough, getting started, changelog
  - Done when: the docs tests pass and the live check (`ls /nope`, `ask why did that fail`, `ask fix it`, Alt-v) works in a real shell
  - Notes: docs/ask.md (in mkdocs.yml and docs/README.md), `man ask`, the walkthrough's "short way in", getting started, settings page, mcp-and-ai pointer, changelog. `ask` is in the "ShellCraft" group of `help` (there is no "AI and MCP" group there). Live check against the real client in a scratch home: `ls /nope` + `ask why did that fail` streamed an answer (4s, $0.04); `mkdir one/two/three` + `ask fix it` staged `mkdir -p one/two/three` and Alt-v pasted it; a command it ran showed as a `✦ AI` job; `ask -c`, `ask -b` with `jobs N`, piped input and Ctrl-C (status 130, no client left behind) worked.

## 2. The enrichment pack

- [x] **2.1 `modules/_intel.py`**: one HTTP helper and error style, indicator kinds, defang and refang, the 24 hour answer store
  - Done when: tests cover each kind, both fang directions, the store and every error message
  - Notes: `modules/_intel.py`: `get_json`, `kind_of`/`clean`, `refang`/`defang`, `is_public`, `find` (used by `ioc`), the answer store (`answer`), the record every lookup returns (`record`: source, indicator, kind, verdict, summary, known_good, cached, data, raw) and the shared command line (`parser`, `targets`, `each`, `show`). Added to the plan: (a) `Patience`, the one rate-limit rule for a list: a limit of 65 s or less is waited out, five minutes in all, and a wait that did not help ends the waiting; a limit with no Retry-After and a single lookup never wait; (b) "not known" is stored too, so asking again costs nothing; (c) the store keeps the case of a URL. The module checker (`tools/ingest/check.py`, `module_flags`) now counts options a helper adds as the module's own. Tests: tests/test_intel.py (110).
- [x] **2.2 Lookup modules**: `queryRdap`, `queryShodan`, `queryVirusTotal`, `queryAbuseIPDB`, `queryGreyNoise`
  - Done when: each has `.py`, `.skill`, `.md`, tests with the fake HTTP, a clear line when its key is missing, and `tools/ingest check --all --strict` is clean
  - Notes: Each module has `lookup(indicator, timeout=, fresh=)` for `enrich` next to `run`, and `SOURCE`, `KINDS`, `KEY_VAR`. `ip2geo` gained the same `lookup`. Verdict rules are constants at the top of each file and a table in each man page. `queryShodan` uses InternetDB without a key and the host API with one (a 429 there waits one second). `queryVirusTotal` turns its 429 into a 60 s wait (it sends no Retry-After) and only reads reports, it never uploads. A private address is answered `unknown` with no request in VirusTotal, AbuseIPDB and GreyNoise. Output `-o table|json|raw`. Tests: 12 + 46 + 39 + 53 + 51; `tools/ingest check --all --strict` 19/19 (with `--network` all the new modules pass; `queryCert`'s own network test timed out on crt.sh that day).
- [x] **2.3 `ioc`**: pull IPs, domains, URLs, hashes and emails out of any text
  - Done when: `--type`, `--public`, `--defang`, `--refang`, `--count` and `-o json` work, defanged input is read, duplicates go
  - Notes: `modules/ioc.py`, no network. Output is `-o list|json` (the plan said `-o json`; `list` is the default's name). A URL also gives its host and an email address its domain. A name counts as a domain only with a known ending, so `main.py` and `notes.md` are left out. Hex groups that look like code (a MAC, a time) are not read as IPv6. Tests: tests/test_ioc.py (20).
- [x] **2.4 `enrich`**: every lookup that fits, one verdict
  - Done when: `enrich IP`, a domain, a hash and piped input give a `VERDICT` line with a `SOURCE:` line per service; services without a key are listed as skipped; `--only`, `--fresh`, `-o json` work
  - Notes: `modules/enrich.py`: sources asked a few at a time in threads (cancel reaches them), one `Patience` per source per run. Output is `-o text|json`. Different from the plan: `SKIPPED:` lines come once per run, not per indicator; a private address and a URL on one are never sent; a URL goes to VirusTotal and the other sources get its host; an email is looked up by its domain; `malicious` from one source and known-good from another gives `suspicious`. One indicator prints a JSON object, several a list. Tests: tests/test_enrich.py (42).
- [x] **2.5 AI side and docs**: analyst tools, agent tool lists, triage skills with the escalation ladder, `docs/enrichment.md`, module tables, API key table, changelog
  - Done when: `tests/test_ai_files.py`, `tests/test_analyst_role.py` and the docs tests pass; `enrich 8.8.8.8` answers live without keys
  - Notes: Seven tools in `ai_policy.ANALYST_TOOLS`; `enrich` and `ioc` in the tool lists of edr-analyst, host-analyst, feed-analyst, log-reader, soc-triage and worker, the five lookups in all of those but log-reader. `soc-triage`, `remote-triage` and `aggregator-triage` start with `enrich` and share one ladder table (verdict plus what the host shows gives `info`, `warn` or `crit`; one alert per finding, again only when the level went up). Docs: docs/enrichment.md (mkdocs.yml, docs/README.md), module table, API key table, DEVELOPMENT.md, ai/README.md, mcp-and-ai, ai-host-analysis, AI-TODO item 5 (the report file is left for feature 4), changelog. Live without keys: `enrich 8.8.8.8` answered from ip2geo, RDAP, Shodan InternetDB and GreyNoise, listed VirusTotal and AbuseIPDB as skipped, and showed `(stored)` the second time. Not checked: VirusTotal, AbuseIPDB and the Shodan host API against the real services, because no keys are set on this machine; they are tested against recorded answer shapes only.

## 3. Missed alerts are kept, rounds run on their own

- [ ] **3.1 Missed alerts**: an alert raised while no shell is open is shown by the next shell
  - Done when: an old spooled alert shows as missed with its age and stays until dismissed; old `sub` and `recording` requests still expire; 200 files and 7 days are the limits
  - Notes:
- [ ] **3.2 `shellcraft --watch-rounds PLAN`**: one due round, headless, with a cost cap
  - Done when: nothing due means no client start; a lock stops overlap; `rounds.log` gets one line per run; a failure sends a `warn` alert; `--dry-run` shows the command
  - Notes:
- [ ] **3.3 `--install-timer` and `--remove-timer`**: a systemd user timer for a plan
  - Done when: the unit files are right, `systemctl --user` is called (a stand-in in the tests), removal cleans up, no systemd prints a crontab line, `--check-analyst` lists the timers
  - Notes:
- [ ] **3.4 Docs and the live check**: host analysis page, walkthrough, `man alert`, folder page, changelog
  - Done when: a real capped round on `hugo` ran from the command with the shell closed and its alert showed in the next shell; the timer was installed, listed and removed
  - Notes:

## 4. The fleet view, reports, hostscan rules

- [ ] **4.1 `report` module**: `report save UNIT --status ... --summary ...`, `report list`, `report show`
  - Done when: a status record is always written, a Markdown report only when something was found or a body is given, old reports are removed, the body is redacted
  - Notes:
- [ ] **4.2 `fleet` builtin**: every host and device in one table, `fleet UNIT` for the detail, `--json`
  - Done when: hosts, aggregator devices, last round, `LATE` and `never`, changed counts and open findings show from a scratch home in the tests; it pipes as plain text
  - Notes:
- [ ] **4.3 hostscan rules for the eight integrity checks**: `pkg-verify`, `suid`, `capabilities`, `ld-preload`, `units`, `sudoers-files`, `sshd-config`, `shell-rc`
  - Done when: every rule has a case and a clean sample in `tests/test_hostscan.py` and its id in `man hostscan`; `sshrun H suid | hostscan suid` works through the fake ssh
  - Notes:
- [ ] **4.4 AI side and docs**: `report` in the analyst tools, the round skills end with `report save`, the alert carries the report path, the `host:NAME` tag, `docs/fleet.md`, changelog, `AI-TODO.md` item 5
  - Done when: one live round on `hugo` leaves a status in `fleet`, and Alt-v on its alert pastes the report path
  - Notes:

## 5. Arrays, lists and dicts

- [ ] **5.1 The values**: `ShArray` and `ShMap` (text that carries the elements), type-aware `set_variable`
  - Done when: no behaviour changes yet and the suite is green; equality, caps and "no nesting" are tested
  - Notes:
- [ ] **5.2 Bash indexed arrays**: `a=(x y)`, `a+=(z)`, `a[i]=v`, `${a[i]}`, `${a[@]}`, `"${a[@]}"`, `${#a[@]}`, `${!a[@]}`, slices, `unset`, `for x in "${a[@]}"`
  - Done when: each works as in bash, in and out of quotes, across lines; subshells and functions do not leak changes
  - Notes:
- [ ] **5.3 `declare -a` / `-A`, maps, `read -a`, `mapfile`**
  - Done when: `declare -A m; m[k]=v; ${m[k]}; ${!m[@]}`, `declare -p`, `local`, `readonly`, `read -a` and `mapfile -t` work; exporting an array is refused with one line
  - Notes:
- [ ] **5.4 Python lists and dicts, reading**: literals, subscripts, slices, `len`, `in`, `sorted`, `join`, `for h in hosts`, `for k, v in d.items()`
  - Done when: a list set in bash reads in Python and the other way round; `print(hosts)` prints like Python
  - Notes:
- [ ] **5.5 Python lists and dicts, changing**: `hosts.append(x)`, `hosts[0] = x`, `d["k"] = v`, `del`, `pop`, `update`
  - Done when: each statement writes the new value back; a changing method inside a condition is refused with one line
  - Notes:
- [ ] **5.6 Checker, colours, docs**: `source -n`, highlighting, `docs/scripting/arrays-and-lists.md`, the "no arrays" lines, the AI scripting skill, `SCRIPTING-TODO.md` ticks, changelog
  - Done when: `tests/test_docs.py` runs every new example
  - Notes:
