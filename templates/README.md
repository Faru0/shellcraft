# Writing a ShellCraft module

A module is **three files with the same base name** in `modules/`:

| File | For | Required? |
| --- | --- | --- |
| `<name>.py` | The code: `run(args, stdin) -> str` | yes |
| `<name>.md` | Humans: the page `man <name>` shows | strongly recommended |
| `<name>.skill` | AI agents: becomes the MCP tool description (TOML) | strongly recommended |

`templates/module/` holds a complete, working, commented reference module (`template`, a word
counter). Copy it, rename it, and change it.

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
   - `SUMMARY = "..."`: one line for `help` and Tab completion.
   - `SPINNER_TEXT = "..."`: shown while slow calls run.
   - a module docstring.
9. **Keep `run()` free of side effects when it runs with no arguments,** or make it cheap. `modtest` calls `run([], "")` as a smoke test.

## Rules for the `.md`

Use this section order: `# <name>`, a one-sentence description, `## Synopsis` (a code block),
`## Description`, `## Options` (a table with every flag, both forms, and defaults),
`## Examples` (a code block, with at least one pipe), `## Errors`, `## See also`.
See `module/template.md`.

## Rules for the `.skill` (TOML)

- **Keys:** `summary` (one line, ≤ 200 chars), `when_to_use` (when to use it, and when *not* to), `usage`, `examples` (MCP-shaped calls with results), `notes`, then one `[[args]]` table per argument/option (`name`, `description`), then `[[tests]]`.
- ⚠️ **Order matters.** Put every `key = value` line *before* the first `[[args]]`. In TOML, a key written after a table header belongs to that table, so it silently disappears from the description. `modtest` catches this.
- Write for an AI reader: be concrete, give defaults, say what the output looks like, and name side effects such as network access or files written.

### `[[tests]]`: executable examples

```toml
[[tests]]
args = ["-n", "2"]
stdin = "b a b c a b\n"
expect = "b 3\na 2\n"      # or contains = "…", or error = "…" (a ModuleError substring)
network = false            # true = only run with modtest --network
```

Tests are run by `modtest` and are **never** shown to AI clients.

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

The exit code is `0` when there are no FAILs, and `1` otherwise.
