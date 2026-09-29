# Writing a ShellCraft module

This is the module author's guide. For installing and using ShellCraft, see the main
[README](../README.md). For working on the shell itself, see [DEVELOPMENT.md](../DEVELOPMENT.md).

A module is **three files with the same base name** in `modules/`:

| File | For | Required? |
| --- | --- | --- |
| `<name>.py` | The code: `run(args, stdin) -> str` | yes |
| `<name>.md` | Humans: the page `man <name>` shows | strongly recommended |
| `<name>.skill` | AI agents: becomes the MCP tool description (TOML) | strongly recommended |

`templates/module/` holds a complete, working, commented reference module (`template`, a word
counter). Copy it, rename it, and change it.

What your module gets for free:
- **A command:** it runs in pipelines and can be redirected.
- **A manual:** `man <name>`.
- **A `help` listing.**
- **Tab completion** of its switches and their values.
- **An MCP tool** that AI clients can call.

Because of that last point, **anything your module can do, a connected AI can do too.** Be careful
with modules that delete, overwrite or send data.

## Quick start

```bash
cp templates/module/template.py    modules/wordfreq.py
cp templates/module/template.md    modules/wordfreq.md
cp templates/module/template.skill modules/wordfreq.skill
# rename "template" → "wordfreq" in all three files, then write your logic
python tools/modtest.py modules/wordfreq.py          # check it
python main.py -c "echo a b a | wordfreq"            # try it
```

In a running shell, type `reload` to pick up a new module without restarting.

## Already have the `.py`? Let an AI write the `.md` and `.skill`

```bash
python tools/mkprompt.py modules/wordfreq.py -o prompt.md
```

1. Paste `prompt.md` into any AI assistant (Claude, ChatGPT, …).
2. Save the two blocks it returns as `modules/wordfreq.md` and `modules/wordfreq.skill`.
3. Run `python tools/modtest.py modules/wordfreq.py` and fix anything it flags.

The prompt includes the rules below, the reference module as a worked example, and your source.
Add `--existing` to have the AI revise the files you already have instead of starting over.
Without the script, you can open `templates/AI_MODULE_PROMPT.md` and replace the `{{…}}`
placeholders by hand.

## Rules for the `.py`

1. **Name:** the file name is the command name. Use letters, digits, `_` or `-`, and start with a letter. Don't reuse a builtin name (`ls`, `cat`, `grep`, …): builtins win, so your module would never run. `modtest` catches this.
2. **Entry point:** `def run(args: list[str], stdin: str) -> str`.
   - `args` are the words after the command name, already split and unquoted.
   - `stdin` is the previous pipeline stage's output (`""` if none).
3. **Return your output; never `print()` it.** Printing bypasses pipes and redirects, and it corrupts the MCP stdio transport.
4. **Never call `sys.exit()`.** Parse arguments with `core.modkit.ArgParser`, which is argparse but raises `ModuleError` instead of exiting.
5. **Fail with `ModuleError("prog: clear one-line message")`.** Other exceptions still work, but they show up as `TypeError: …` noise.
6. **Accept stdin and FILE arguments,** like Unix tools. Relative paths resolve against the shell's current directory.
7. **Output plain text,** one record per line, ending in `\n`, with a deterministic order. Then `| head`, `| grep` and `> file` just work.
8. **Optional metadata:**
   - `SUMMARY = "..."`: one line for `help` and Tab completion, used when the `.skill` has no `summary`.
   - `SPINNER_TEXT = "..."`: shown while slow calls run.
   - `ENV_SETTINGS = [...]`: API keys the module needs (see [API keys](#api-keys-env_settings) below).
   - a module docstring.
9. **Keep `run()` free of side effects when it runs with no arguments,** or make it cheap. `modtest` calls `run([], "")` as a smoke test.

## API keys (`ENV_SETTINGS`)

If your module calls a service that needs a key, don't invent your own config file. Declare the
environment variable, and read it from `os.environ` when `run()` is called:

```python
import os
from core.modkit import EnvSetting, ModuleError

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
- **Order of checks:** check the key *after* validating arguments. Then `modtest`'s smoke calls and your `[[tests]]` error cases pass whether or not a key is set.
- **Clear errors:** say how to get the key, and to run `settings NAME`. Turn the service's 401/403 into a "key rejected" message.
- **Documentation:** name every variable in the `.md` (for example in a *Setup* or *API key* section) and in the `.skill` `notes`. `modtest` warns otherwise.
- **Tests:** offline tests can set the variable with `monkeypatch.setenv`. See `tests/test_querydns.py`.

## Rules for the `.md`

Use this section order: `# <name>`, a one-sentence description, `## Synopsis` (a code block),
`## Description`, `## Options` (a table with every flag, both forms, and defaults),
`## Examples` (a code block, with at least one pipe), `## Errors`, `## See also`.
See `module/template.md`.

Write each Options row as ``| `-n N`, `--top N` | Meaning (default 10). |``. Put every switch in
backticks in the first column, because Tab completion reads its switches and help text from these
rows. A value list can be written as ``| `--mode a\|b\|c` | … |``.

## Rules for the `.skill` (TOML)

- **Keys:** `summary` (one line, ≤ 200 chars), `when_to_use` (when to use it, and when *not* to), `usage`, `examples` (MCP-shaped calls with results), `notes`, then one `[[params]]` table per argument/option, then `[[tests]]`. `modtest` requires `summary`, `when_to_use`, `usage` and `examples`.
- ⚠️ **Order matters.** Put every `key = value` line *before* the first `[[params]]`. In TOML, a key written after a table header belongs to that table, so it silently disappears from the description. `modtest` catches this.
- Write for an AI reader: be concrete, give defaults, say what the output looks like, and name side effects such as network access or files written.

### `[[params]]`: typed parameters for AI clients

Each `[[params]]` table becomes one typed property of the tool's MCP input schema. An AI calls
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
- `modtest` fails when a `flag` or `short` isn't defined in the code, since MCP calls using it would break.

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
text, and it also works on its own. `modtest` reports how many switches it found ("Tab completion").

### `[[tests]]`: executable examples

```toml
[[tests]]
args = ["-n", "2"]
stdin = "b a b c a b\n"
expect = "b 3\na 2\n"      # or contains = "…", or error = "…" (a ModuleError substring)
network = false            # true = only run with modtest --network

[[tests]]
params = {top = 1, ignore_case = true}   # instead of args: named values, converted like an MCP call
stdin = "The the THE cat\n"
expect = "the 3\n"
```

Tests are run by `modtest` and are **never** shown to AI clients.

If any test has `network = true`, `modtest` treats the whole module as network-using. It then also
skips its plain smoke calls (`run([], "")`), unless you pass `--network`.

## The tester: `tools/modtest.py`

```bash
python tools/modtest.py modules/wordfreq.py      # one module
python tools/modtest.py --all                    # every module in modules/
python tools/modtest.py modules/x.py --network   # include network tests
python tools/modtest.py modules/x.py --preview   # show the MCP description + man page
python tools/modtest.py --all --strict           # warnings fail too (good for CI)
```

| Area | What it checks |
| --- | --- |
| Files & name | The `.md`/`.skill` exist, the name is valid, and it doesn't clash with a builtin (FAIL) or an OS program (WARN). |
| Import | The module loads exactly as the shell loads it. |
| Contract | `run(args, stdin)` signature, and `SUMMARY`/docstring. |
| Behavior | `run([], "")` and an unknown option each finish within the timeout, print nothing, don't `sys.exit()`, return `str` or raise `ModuleError`. |
| `[[tests]]` | Every case passes, with a diff on mismatch. |
| `.md` | The title is `# <name>`; Synopsis, Options and Examples sections exist; it renders. |
| `.skill` | Valid TOML, required keys present, no misplaced or unknown keys, summary length, description size. |
| Docs coverage | Every `add_argument("-x", "--long")` flag in the code appears in both the `.md` and the `.skill`. |
| Tab completion | How many switches (and value lists) completion found. A WARN if the code has options but the docs yield none. |
| API keys | Lists `ENV_SETTINGS` (an invalid declaration already fails the import), and WARNs if a variable isn't named in the `.md` or `.skill`. |

The exit code is `0` when there are no FAILs, and `1` otherwise.
