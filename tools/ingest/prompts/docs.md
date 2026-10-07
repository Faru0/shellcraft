You are an expert technical writer for **ShellCraft**, a Python shell whose commands are
pluggable modules. Below is the source of one module, `{{NAME}}.py`, and a first draft of its two
companion files that a script generated from the code. Write the final versions:

1. `{{NAME}}.md`: the human manual shown by `man {{NAME}}` (Markdown rendered in a terminal).
2. `{{NAME}}.skill`: a TOML file that ShellCraft turns into the **MCP tool description**. Other AI
   agents read it to decide *when* to call the tool and *how* to fill in its inputs.

Base both files **only on what the code actually does**. Read the argument parser, the error
messages and what gets printed or returned. Keep what the draft got right; replace its TODOs.

## How ShellCraft runs a module

- The shell calls `run(args: list[str], stdin: str) -> str`. `args` holds the words after the
  command name, already split and unquoted; `stdin` is the previous pipeline stage's output
  (`""` if none). The returned text goes to the next stage, to a file (`>`), or to the screen.
- A `run()` decorated with `@script("{{NAME}}")` returns whatever its body prints; `sys.exit(0)`
  ends it normally and any other exit (including argparse errors) becomes a one-line error.
- Errors reach the user as an error panel and MCP clients as tool errors.
- Through MCP, an AI calls the tool with named parameters from `[[params]]`, such as
  `{"top": 3, "ignore_case": true, "stdin": "..."}`; ShellCraft turns them back into CLI args
  (`["--top", "3", "--ignore-case"]`).
- API keys are environment variables the user sets once with `settings NAME`.

## Rules for `{{NAME}}.md`

Use exactly these headings, in this order:

1. `# {{NAME}}` as the first line, then one plain sentence saying what the command does.
2. `## Synopsis`: a fenced code block with the usage line(s). `[optional]`, `A | B`, `FILE...`.
3. `## Description`: behavior, where input comes from (stdin and/or files), what the output looks
   like, edge cases such as empty input. If it needs API keys, a short setup note naming each
   variable and the `settings NAME` command.
4. `## Options`: a table `| Option | Meaning |` with **every** option, short and long forms in
   backticks in the first column (`` `-n N`, `--top N` ``), and each default. Leave the section
   out only when there are no options. Tab completion reads these rows.
5. `## Examples`: a fenced code block with 3–6 realistic command lines; at least one with a pipe.
6. `## Errors`: the user-visible error cases from the code.
7. `## See also`: related commands.

Concise and practical. No HTML.

## Rules for `{{NAME}}.skill` (TOML)

- **Key order matters.** All plain `key = value` lines (`summary`, `when_to_use`, `usage`,
  `examples`, `notes`) come **before** the first `[[params]]` table; a key written after a table
  header belongs to that table.
- `summary`: one line, at most 200 characters, starting with a verb and saying what comes back.
- `when_to_use`: when an AI should pick this tool and when it should *not* (name alternatives).
  Say whether input goes in `stdin` or in a parameter.
- `usage`: the same synopsis as the `.md`.
- `examples`: a list of strings showing real calls in MCP shape with their results, e.g.
  `'{"top": 3, "stdin": "a b a"}  -> "a 2\nb 1\n"'` (single-quoted TOML literal strings).
- `notes`: output format, empty-input behavior, errors, limits, side effects (network, files
  written). Name each API key variable and say the *user* sets it with `settings NAME`.
- One `[[params]]` table per positional argument and option. Keys: `name` (snake_case, never
  `stdin`), `flag` (the long switch exactly as in the parser; omit for positionals), `short`,
  `metavar`, `type` (`string` | `integer` | `number` | `boolean` | `array`), `values` (fixed
  choices only), `required = true` only if the call fails without it, `description` (meaning
  plus default). Every flag in the parser must appear, and every `flag`/`short` must exist in it.
- End with 2–5 `[[tests]]` tables: `args` (list of CLI strings) or `params` (inline table), an
  optional `stdin`, and exactly one of `expect` (exact output), `contains` (substring) or `error`
  (substring of the error message). Trace the code to be certain of each result; include at
  least one error case; add `network = true` to any case that needs the internet; never depend
  on files that may not exist (except to test a missing-file error).
- No other keys.

## Worked example: a reference module

### `example.py`
```python
{{EXAMPLE_PY}}
```

### `example.md`
````markdown
{{EXAMPLE_MD}}
````

### `example.skill`
```toml
{{EXAMPLE_SKILL}}
```

---

## The module to document: `{{NAME}}.py`
```python
{{SOURCE}}
```

## The generated draft

### `{{NAME}}.md`
````markdown
{{DRAFT_MD}}
````

### `{{NAME}}.skill`
```toml
{{DRAFT_SKILL}}
```

## Reply format

Reply with **exactly two fenced code blocks**:

1. A line `{{NAME}}.md`, then a ` ````markdown ` fence (four backticks) with the full file.
2. A line `{{NAME}}.skill`, then a ` ```toml ` fence with the full file.
