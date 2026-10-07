You are converting a Python script into a **ShellCraft module** for `{{NAME}}`. ShellCraft calls
`run(args: list[str], stdin: str) -> str` instead of running the file as a script.

Make **exactly the changes listed below and nothing else**. This is a minimal, mechanical edit:

- Do not rename anything, fix bugs, add or remove comments, change quotes, reformat, add type
  hints, reorder code, or "improve" anything — even if it looks wrong.
- Keep every line that is not mentioned exactly as it is (apart from the indentation the
  changes require).
- `@{{DECORATOR}}("{{NAME}}")` (imported from `core.modkit`) turns what the body `print()`s into the
  output and `sys.exit()` into a clean error, so prints and exits stay as they are.

## Changes to make

{{STEPS}}

## The script (line numbers on the left are for reference only — do not output them)

```
{{NUMBERED_SOURCE}}
```

## Reply format

Reply with the complete converted file in **one** ```python fenced block, and nothing else.
