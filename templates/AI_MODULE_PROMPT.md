You are an expert technical writer for **ShellCraft**, a Python shell whose commands are
pluggable modules. I will give you the source code of one module, `{{MODULE_NAME}}.py`. Write
its two companion files:

1. `{{MODULE_NAME}}.md`: the human manual shown by `man {{MODULE_NAME}}` (rendered as Markdown in a terminal).
2. `{{MODULE_NAME}}.skill`: a TOML file that ShellCraft turns into the **MCP tool description**.
   Other AI agents read it to decide *when* to call the tool and *how* to fill in its inputs.

Base both files **only on what the code actually does**. Read the argument parser, the error
messages and the return values carefully.

---

## How ShellCraft runs a module

- The shell calls `run(args: list[str], stdin: str) -> str`.
- `args` holds the command-line words after the module name. They are already split and unquoted: `{{MODULE_NAME}} -n 3 "a b"` becomes `["-n", "3", "a b"]`.
- `stdin` is the previous pipeline stage's output (`""` if there is none).
- The returned text goes to the next pipeline stage, to a file (`>` / `>>`), or to the screen.
- Errors are raised as `ModuleError("one-line message")`. The shell shows them in an error panel, and MCP clients receive them as tool errors.
- The module name is the file name. Relative file paths resolve against the shell's current directory.
- Through MCP, an AI calls the tool with `{"args": [...], "stdin": "..."}` and gets the returned text back.

## Rules for `{{MODULE_NAME}}.md`

Use exactly this section order and these headings:

1. `# {{MODULE_NAME}}`: the first line, exactly this.
2. One plain sentence saying what the command does.
3. `## Synopsis`: a fenced code block with the usage line(s), built from the parser. Use `[optional]`, `A | B` for choices, and `FILE...` for repeats.
4. `## Description`: how it behaves, including where input comes from (stdin and/or files), what the output looks like, and edge cases such as empty input.
5. `## Options`: a Markdown table `| Option | Meaning |` with **every** option from the code, using both short and long forms (`` `-n N`, `--top N` ``). Include each default. Leave this section out only if the module has no options.
6. `## Examples`: a fenced code block with 3–6 realistic command lines. At least one should use a pipe (`|`), and one may use `>` redirection. Short `# comments` are allowed.
7. `## Errors`: the user-visible error cases, taken from the `ModuleError` messages in the code.
8. `## See also`: related commands, such as `man grep`.

Keep it concise and practical. Don't use HTML.

## Rules for `{{MODULE_NAME}}.skill` (TOML)

- **Key order matters in TOML.** Write all plain `key = value` lines (`summary`, `when_to_use`, `usage`, `examples`, `notes`) **before** the first `[[args]]` table. A key written after a table header belongs to that table and would vanish from the description.
- `summary`: one line of at most 200 characters. Start with a verb and say what comes back.
- `when_to_use`: a multi-line string. Say when an AI should pick this tool, and when it should *not* (name better alternatives, such as `grep`/`filter` or `wc`). Mention whether input goes in `stdin` or in `args`.
- `usage`: the same synopsis as the `.md`.
- `examples`: a list of strings showing real calls in MCP shape, with their results. For example, `'args=["-n", "3"], stdin="a b a"  -> "a 2\nb 1\n"'`. Use single-quoted TOML literal strings.
- `notes`: the output format, empty-input behavior, error behavior, limits, side effects (network access, files written), and anything an agent could get wrong.
- One `[[args]]` table per positional argument and per option, with `name` (e.g. `"-n N / --top N"`) and `description` (what it means plus its default). Every flag in the parser must appear.
- Add 2–5 `[[tests]]` tables **at the end**. Each has `args` (a list of strings), an optional `stdin`, and **exactly one** of:
  - `expect` (exact output)
  - `contains` (a substring)
  - `error` (a substring of the `ModuleError` message)

  Work out the expected output precisely by tracing the code. Only use cases you are certain of, and include at least one error case. Add `network = true` to any case that needs the internet. Tests must not depend on files that may not exist, except for testing a missing-file error.
- Don't use any other keys.

## Worked example: the reference module

### `template.py`
```python
{{EXAMPLE_PY}}
```

### `template.md`
````markdown
{{EXAMPLE_MD}}
````

### `template.skill`
```toml
{{EXAMPLE_SKILL}}
```

---

## The module to document: `{{MODULE_NAME}}.py`
```python
{{MODULE_SOURCE}}
```
{{EXISTING_FILES}}
## Output format

Reply with **exactly two fenced code blocks** and nothing else in between:

1. A line `{{MODULE_NAME}}.md`, then a ` ````markdown ` fence (four backticks, because the file contains code blocks) with the full file.
2. A line `{{MODULE_NAME}}.skill`, then a ` ```toml ` fence with the full file.

After that, add at most three bullet points listing anything in the code that looked like a bug
or was ambiguous, or write "No issues found."
