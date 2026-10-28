# Module reference

← [Manual](../README.md)

Every rule, file and option for writing ShellCraft modules, in one place. Come here when you're
writing or fixing a module and need the exact name of a key, the order of a section, or what the
checker looks for. To learn by doing, start with [Your first module](first-module.md). To convert
a script you already have, see [Ingest a script](ingest.md).

## The files

A module is up to three files with the same name, in `~/.shellcraft/modules/` (or a bundled
modules folder). Use this table to see what each file adds, and what happens when one is missing:

| File | Effect |
| --- | --- |
| `name.py` | Always a command: listed by `help`, Tab-completed, usable in pipelines. |
| `name.py` with `def run(args, stdin)` | Imported, and `run()` is called (the rules below). |
| `name.py` without `run()` | A plain Python script: run as `python name.py ARGS` with stdin piped in and stdout as the output, never imported. Its docstring's first line is the `help` summary, followed by "⚠ needs run() to work properly! ingest it, or see docs/modules/first-module.md" as a reminder to convert it (`ingest name.py` does it); a nonzero exit is the error. |
| `name.skill` | Makes it an **MCP tool** for AI clients. Without it the command is local to the shell. |
| `name.md` | Gives it a **man page**. Without it `man name` says there is none. |
| `_name.py` | A **helper**: never a command. Modules and scripts import it with `import _name` (or `from . import _name` inside a module). |

Editing a helper reloads the modules that use it (hot reload). `modules` lists the helpers in their own table, with the modules that import them and a description: the first line of prose in `_name.md`, else the first line of the docstring.

`templates/example.py`, `.md` and `.skill` in the ShellCraft folder are a complete, commented
reference module (`example`, a word counter) to copy from.

Anything your module can do, a connected AI client can do too, once it has a `.skill` file. Be
careful with modules that delete, overwrite or send data, and consider [`expose = false`](#expose--false-local-only).

## Rules for the .py file

The rules every module's code follows. Most of them exist because a module's output may go to a
pipe, a file or an AI client rather than your screen.

1. **Name:** the file name is the command name. Use letters, digits, `_` or `-`, and start with a letter. Don't reuse a builtin name (`ls`, `cat`, `grep`, …): builtins win, so your module would never run. The checker catches this.
2. **Entry point:** `def run(args: list[str], stdin: str) -> str`. (A converted script uses `@script` instead of rules 3–5.)
   - `args` are the words after the command name, already split and unquoted.
   - `stdin` is the previous pipeline stage's output (`""` if none).
3. **Return your output; never `print()` it.** Printing bypasses pipes and redirects, and it corrupts the MCP stdio transport. To stream, make `run()` a generator that `yield`s lines: they flow to the next command (and to the screen, if it keeps coming) as you yield them, and your generator is closed when the reader has had enough (`mymod | head -1`).
4. **Never call `sys.exit()`.** Parse arguments with `shellcraft.modkit.ArgParser`, which is argparse but raises `ModuleError` instead of exiting.
5. **Fail with `ModuleError("prog: clear one-line message")`.** Other exceptions still work, but they show up as `TypeError: …` noise.
6. **Accept stdin and FILE arguments,** like Unix tools. Relative paths resolve against the shell's current directory.
7. **Output plain text,** one record per line, ending in `\n`, with a deterministic order. Then `| head`, `| grep` and `> file` work on it.
8. **Optional metadata:**
   - `SUMMARY = "..."`: one line for `help` and Tab completion, used when the `.skill` has no `summary`.
   - `SPINNER_TEXT = "..."`: shown while slow calls run.
   - `ENV_SETTINGS = [...]`: API keys the module needs (see [API keys](#api-keys)).
   - a module docstring.
9. **Keep `run()` free of side effects when it runs with no arguments,** or make it cheap. The checker calls `run([], "")` as a smoke test.
10. **Let Ctrl-C stop you.** A module runs in a worker thread, which Ctrl-C at the prompt (or an MCP call's timeout) can't kill, so the shell asks it to stop instead. Pass a `timeout=` to every network call, call `check_cancel()` between the steps of a loop (it raises `Cancelled`, a `ModuleError`), and use `modkit.sleep()` instead of `time.sleep()`, which wakes up when the command is cancelled. `cancelled()` returns the flag without raising.

   ```python
   from shellcraft.modkit import check_cancel, sleep

   for host in hosts:
       check_cancel()          # stops here after Ctrl-C
       results.append(lookup(host, timeout=10))
       sleep(1)                # polite delay that Ctrl-C cuts short
   ```

### A converted script: `@script`

A plain script that prints its output can keep doing that. Decorate `run` with `@script`, and
ShellCraft turns what the body prints into the output, `sys.exit(0)` into a normal end, and any
other exit, argparse errors included, into a one line error. This is what [ingest](ingest.md)
uses:

```python
from shellcraft.modkit import script

@script("mytool")
def run(args, stdin):
    ...  # the original script, print() and all
```

## API keys

Copy this when your module needs a key. If your module calls a service that needs a key, don't invent your own config file. Declare the
environment variable, and read it from `os.environ` when `run()` is called:

```python
import os
from shellcraft.modkit import EnvSetting, ModuleError

ENV_SETTINGS = [
    EnvSetting("MYSERVICE_API_KEY", "MyService API key",
               "Free key from https://myservice.example/account."),
]

def run(args, stdin):
    ...                                   # parse and validate arguments first
    key = os.environ.get("MYSERVICE_API_KEY", "").strip()
    if not key:
        raise ModuleError("mymod: no MyService API key; run: settings MYSERVICE_API_KEY")
```

What you get:
- `settings` lists the key as `mymod · MyService API key`, with a masked value.
- `settings MYSERVICE_API_KEY` asks for it without echoing, stores it in `config.json` (mode 600), and exports it.
- It's exported at every startup, MCP mode included.
- Tab completion knows the name, and the value never reaches the history file.

Rules:
- **Names:** UPPER_CASE environment-variable names. Use the name the service's own tools use, if there is one.
- **Order of checks:** check the key *after* validating arguments. Then the checker's smoke calls and your `[[tests]]` error cases pass whether or not a key is set.
- **Clear errors:** say how to get the key, and to run `settings NAME`. Turn the service's 401/403 into a "key rejected" message.
- **Documentation:** name every variable in the `.md` (for example in a *Setup* or *API key* section) and in the `.skill` `notes`. The checker warns otherwise.
- **Tests:** offline tests can set the variable with `monkeypatch.setenv`. See `tests/test_querydns.py`.

## The .md file

The manual page that `man NAME` shows. Use this section order: `# <name>`, a one-sentence description, `## Synopsis` (a code block),
`## Description`, `## Options` (a table with every flag, both forms, and defaults),
`## Examples` (a code block, with at least one pipe), `## Errors`, `## See also`.
See `example.md`.

Write each Options row as ``| `-n N`, `--top N` | Meaning (default 10). |``. Put every switch in
backticks in the first column, because Tab completion reads its switches and help text from these
rows. A value list can be written as ``| `--mode a\|b\|c` | … |``.

## The .skill file

The `.skill` file (TOML) turns the module into a tool for AI clients connected over MCP. Without
it, the module is a command in your shell only.

- **Keys:** `summary` (one line, ≤ 200 chars), `when_to_use` (when to use it, and when *not* to), `usage`, `examples` (MCP-shaped calls with results), `notes`, then one `[[params]]` table per argument/option, then `[[tests]]`. The checker requires `summary`, `when_to_use`, `usage` and `examples`.
- ⚠️ **Order matters.** Put every `key = value` line *before* the first `[[params]]`. In TOML, a key written after a table header belongs to that table, so it silently disappears from the description. The checker catches this.
- Write for an AI reader: be concrete, give defaults, say what the output looks like, and name side effects such as network access or files written.

### `expose = false`: local only

A module that must never be reachable from an AI client (it spends money, deletes things, or
reads something private) sets `expose = false` at the top of its `.skill`. It stays a normal
shell command, but it is not an MCP tool, has no man-page resource, and `shellcraft_pipeline`
refuses to run it. `--expose NAME,...` on the server command line narrows the list further.

### `[[params]]`: typed parameters

Copy this block once per option or argument. Each `[[params]]` table becomes one typed property of the tool's MCP input schema. An AI calls
`{"top": 3, "ignore_case": true, "stdin": "…"}`, and ShellCraft turns that back into the CLI args
your `run()` already parses: `["--top", "3", "--ignore-case"]`. Your module code doesn't change.

```toml
[[params]]
name = "output"               # the property name, snake_case (required)
flag = "--output"             # the long switch; leave it out for a positional argument
short = "-o"                  # the short switch (Tab completion and docs)
metavar = "FORMAT"            # the value placeholder (Tab completion and docs)
type = "string"               # string (default) | integer | number | boolean | array
values = ["table", "json"]    # fixed choices: checked on MCP calls, completed by Tab
required = false
description = "table (default) or json."   # required
```

- A `boolean` is a bare switch, sent only when true, and it needs a `flag`.
- An `array` repeats its `flag` once per item. As a positional, it adds each item, and it must be the last positional.
- Switches come first in the generated args, then the positionals in the order they're declared.
- `stdin` is added to the schema for you. Don't declare it.
- The checker fails when a `flag` or `short` isn't defined in the code, since MCP calls using it would break.

A module with no `[[params]]` still works. AI clients then get a raw `args` array of CLI strings,
documented from optional `[[args]]` tables (`name = "-s / --long METAVAR"`, `description`,
optional `values`).

### Tab completion comes free

The shell completes your module's switches from these files, with no completer code needed:

- `mymod -<Tab>` lists the switches, with help text from the `.md` Options table.
- `mymod --fo<Tab>` completes long switches.
- `mymod --format <Tab>` offers the `values` from the `.skill`.
- Switches already on the line aren't offered again.

The `.skill` `[[params]]` (or `[[args]]`) tables are the main source; the `.md` Options table fills in the short help
text, and it also works on its own. The checker reports how many switches it found ("Tab completion").

### `[[tests]]`: examples that are checked

Tests the checker runs against your module. Use `expect` for the exact output, `contains` for part
of it, or `error` for a failure you expect.

```toml
[[tests]]
args = ["-n", "2"]
stdin = "b a b c a b\n"
expect = "b 3\na 2\n"      # or contains = "…", or error = "…" (a ModuleError substring)
network = false            # true = only run with check --network

[[tests]]
params = {top = 1, ignore_case = true}   # instead of args: named values, converted like an MCP call
stdin = "The the THE cat\n"
expect = "the 3\n"
```

Tests are run by the checker and are **never** shown to AI clients.

If any test has `network = true`, the checker treats the whole module as network-using. It then also
skips its plain smoke calls (`run([], "")`), unless you pass `--network`.

## The checker

The checker loads a module the way the shell does and tests it against the rules on this page. The
commands first, then what each part of its report covers:

```bash
ingest check ~/.shellcraft/modules/wordfreq.py     # one module (inside ShellCraft)
ingest check --all                                 # every module ShellCraft loads
ingest check modules/x.py --network                # include network tests
shellcraft ingest check --all --strict             # from a terminal; warnings fail too (good for CI)
python tools/ingest check --all --strict           # from a checkout, without installing
```

| Area | What it checks |
| --- | --- |
| Files & name | The `.md`/`.skill` exist, the name is valid, and it doesn't clash with a builtin (FAIL) or an OS program (WARN). |
| Import | The module loads exactly as the shell loads it. |
| Contract | `run(args, stdin)` signature, and `SUMMARY`/docstring. |
| Behavior | `run([], "")` and an unknown option each finish within the timeout, don't print past `@script`, don't `sys.exit()`, return `str` or raise `ModuleError`. |
| `[[tests]]` | Every case passes, with a diff on mismatch. |
| `.md` | The title is `# <name>`; Synopsis, Options and Examples sections exist. |
| `.skill` | Valid TOML, required keys present, no misplaced or unknown keys, summary length, description size. |
| Docs coverage | Every `add_argument("-x", "--long")` flag in the code appears in both the `.md` and the `.skill`. |
| Tab completion | How many switches (and value lists) completion found. A WARN if the code has options but the docs yield none. |
| API keys | Lists `ENV_SETTINGS` (an invalid declaration already fails the import), and WARNs if a variable isn't named in the `.md` or `.skill`. |

The exit code is `0` when there are no FAILs, and `1` otherwise.
