# Features checklist: ask, enrichment, unattended rounds, fleet, arrays

Five features, built one after another. Whoever finishes an item (a person or an agent) ticks its box and
fills in its Notes: what changed, which tests and docs cover it, and anything left out. An item is done only
when the full test suite, `ruff check` and `mypy shellcraft` (199 or fewer) pass.

## Before the features

- [x] **0. `--install-ai` installs an agent and a skill of the same name**
  - Done when: `shellcraft-copilot` and `soc-triage` are installed as an agent and as a skill; a bare name selects both
  - Notes: `ai_install.catalog()` returns a list (it was a dict by name, so the skill replaced the agent); `select` and `needs` work on it. Test: `test_an_agent_and_a_skill_of_the_same_name_are_both_installed` in tests/test_ai_install.py.

## 1. `ask`: the co-pilot inside ShellCraft

- [ ] **1.1 `shellcraft/claude_run.py`**: find the client, write the MCP config for this shell, run it headless, stream the text, report cost and session, stop it on cancel
  - Done when: a stand-in client in the tests gets the right arguments and config, its text streams, a cancel kills it, a missing client is one clear line
  - Notes:
- [ ] **1.2 The `ask` builtin**: `ask QUESTION`, `-b`, `-c`, `--budget`, `--model`, `-n`
  - Done when: the prompt holds the question, the folder and the last command with its redacted output; the answer streams inline; `-b` is a `✦` job that `jobs -k` stops; `-c` continues the last question; free text with `'`, `?` and `#` needs no quotes; it is refused over MCP; `ask_budget` and `ask_model` settings work
  - Notes:
- [ ] **1.3 Docs for `ask`**: `docs/ask.md`, `man ask`, help group, walkthrough, getting started, changelog
  - Done when: the docs tests pass and the live check (`ls /nope`, `ask why did that fail`, `ask fix it`, Alt-v) works in a real shell
  - Notes:

## 2. The enrichment pack

- [ ] **2.1 `modules/_intel.py`**: one HTTP helper and error style, indicator kinds, defang and refang, the 24 hour answer store
  - Done when: tests cover each kind, both fang directions, the store and every error message
  - Notes:
- [ ] **2.2 Lookup modules**: `queryRdap`, `queryShodan`, `queryVirusTotal`, `queryAbuseIPDB`, `queryGreyNoise`
  - Done when: each has `.py`, `.skill`, `.md`, tests with the fake HTTP, a clear line when its key is missing, and `tools/ingest check --all --strict` is clean
  - Notes:
- [ ] **2.3 `ioc`**: pull IPs, domains, URLs, hashes and emails out of any text
  - Done when: `--type`, `--public`, `--defang`, `--refang`, `--count` and `-o json` work, defanged input is read, duplicates go
  - Notes:
- [ ] **2.4 `enrich`**: every lookup that fits, one verdict
  - Done when: `enrich IP`, a domain, a hash and piped input give a `VERDICT` line with a `SOURCE:` line per service; services without a key are listed as skipped; `--only`, `--fresh`, `-o json` work
  - Notes:
- [ ] **2.5 AI side and docs**: analyst tools, agent tool lists, triage skills with the escalation ladder, `docs/enrichment.md`, module tables, API key table, changelog
  - Done when: `tests/test_ai_files.py`, `tests/test_analyst_role.py` and the docs tests pass; `enrich 8.8.8.8` answers live without keys
  - Notes:

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
