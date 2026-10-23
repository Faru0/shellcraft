# Ingest a script

← [Manual](../README.md)

Already have a Python script you run with `python myscript.py`? The ingest wizard turns it into a
ShellCraft command without rewriting it. It shows you exactly what has to change, makes only
those changes, writes a manual page and an AI description, and checks the result.

Your original script is never changed, and never moved.

## Run it

Inside ShellCraft:

```
ingest ~/scripts/wordcount.py
```

From your normal terminal, the same wizard:

```bash
shellcraft ingest ~/scripts/wordcount.py          # after pip install
python tools/ingest ~/scripts/wordcount.py        # from a checkout, without installing
```

All three write the new command to `~/.shellcraft/modules/`, and a running ShellCraft picks it up
at the next prompt.

## A walk through

Here's the script we'll convert, `wordcount.py`. It counts the most common words in a file:

```python
import argparse
import sys

parser = argparse.ArgumentParser(description="Count the words in a text file.")
parser.add_argument("file")
parser.add_argument("-t", "--top", type=int, default=5, help="how many words to show")
args = parser.parse_args()

counts = {}
with open(args.file) as fh:
    for word in fh.read().lower().split():
        counts[word] = counts.get(word, 0) + 1

for word, n in sorted(counts.items(), key=lambda kv: -kv[1])[: args.top]:
    print(f"{n:5}  {word}")
```

The wizard goes through six steps. Wherever it asks something, the answer in brackets is the
default, and Enter accepts it.

### 1. Your script

It reads the script and analyzes it. It never runs it at this point.

### 2. Name

```
What should the command be called? (wordcount):
```

The name you'll type to run it. It can't be the name of a builtin like `ls` or `grep`. If a
program on your system has the same name, the wizard warns you, because the module will win
inside ShellCraft.

### 3. Analysis

What it found in the script:

```
  top-level statements  9 (7 move into run())
  options found         file, --top
  reads stdin           no
  network               no
  writes files          no
  API keys              none
```

If something can't be converted safely, it stops here and lists the lines to fix by hand. See
[What it can't convert](#what-it-cant-convert).

### 4. Code changes

The exact changes, as a numbered list and a diff:

```
 1. After line 2, add:
    from shellcraft.modkit import script
 2. Put this above line 4:
    @script("wordcount")
    def run(args: list[str], stdin: str) -> str:
  and indent lines 4-15 by 4 spaces, so they become its body.
 3. Line 7: change
    args = parser.parse_args()
  to
    args = parser.parse_args(args)

How do you want to apply them?
  auto    make exactly these changes now (recommended)
  manual  I'll edit the script myself (saves a .patch file too)
  ai      send the script to Claude or OpenAI to make the changes
Choose [auto/manual/ai] (auto):
```

- **auto** makes exactly those changes. This is almost always what you want.
- **manual** writes the changes as `wordcount.patch` in the folder you're in, and stops. Apply them
  yourself, then run the wizard again. It sees a ready module and skips to the next step.
- **ai** sends your script to Claude or OpenAI and asks for the same changes. If the answer changes
  anything else at all, the wizard refuses it. See [The AI option](#the-ai-option).

Then:

```
Write the module to ~/.shellcraft/modules/wordcount.py? [y/n] (y):
```

### 5. Documentation

```
wrote ~/.shellcraft/modules/wordcount.md
wrote ~/.shellcraft/modules/wordcount.skill
Have an AI turn the drafts into finished docs (better wording, examples and tests)? [y/n] (n):
```

It drafts the manual page (`.md`) and the AI description (`.skill`) from your code: the options,
their defaults, the error messages, any API keys. Lines marked `TODO` need a human touch. You can
let an AI finish them, or edit them yourself later.

### 6. Check

```
Run a quick test call, run([], ""), as part of the check? It executes your code. [y/n] (y):
```

The [checker](reference.md#the-checker) runs on the new module and reports PASS, WARN or FAIL
for each thing it checks. Then try your new command:

```
❯ wordcount notes.txt --top 3
    12  the
     7  and
     5  shellcraft
```

## What it changes in your code

As little as possible. The script's code moves into a function called `run`, indented one level.
Imports, functions, classes and real constants stay where they are. Then:

- `parser.parse_args()` gets the `args` list. `sys.argv[1:]` becomes `args`, and
  `sys.stdin.read()` becomes `stdin`.
- An `if __name__ == "__main__":` block becomes `run()` itself, so its body doesn't even move.
- `@script("name")` takes care of the rest. Whatever your code prints becomes the command's
  output, `sys.exit(0)` ends it normally, and any other exit, argparse errors included, becomes
  a clean one line error.

For a script with a `main()` function, the change looks like this:

```diff
 import argparse
+from shellcraft.modkit import script
 ...
-def main():
+def main(argv=None):
     parser = argparse.ArgumentParser()
     ...
-    args = parser.parse_args()
+    args = parser.parse_args(argv)
     print(result)

-if __name__ == "__main__":
+@script("mytool")
+def run(args: list[str], stdin: str) -> str:
-    sys.exit(main())
+    sys.exit(main(args))
```

The converted file no longer runs as `python wordcount.py` on its own. Your original still does.

## What it can't convert

The wizard stops, writes nothing, and points at the lines when the script:

- asks questions with `input()` or `getpass`. A command can run in a pipe or for an AI client,
  where nobody can answer.
- uses `sys.argv` or `sys.stdin` inside a function, apart from the `main()` and `parse_args()`
  pattern above.
- has a function that reads a variable as a global, when that variable is set by code that moves
  into `run()`.
- already uses the name `run`, for example `from subprocess import run`.
- uses `multiprocessing`, a GUI toolkit (`tkinter`, Qt, `curses`), `os._exit`, `os.fork` or
  `signal.signal`.
- loops forever, or never runs anything at the top level.

Fix those lines in your script and run the wizard again.

## The AI option

The `ai` choice in step 4, and the offer to finish the docs in step 5, use Claude or OpenAI. The
wizard says what it will send and to whom, and asks before sending anything.

It uses `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from your environment, or a key stored with
`settings ANTHROPIC_API_KEY`. Without one, it asks for a key with hidden typing and uses it for
that run only. Pick a model with `--model`, or `SHELLCRAFT_AI_MODEL`.

All of the AI code is one file, `tools/ingest/ingest_AI.py`: the providers, the models, the
prompts and the requests. To use a newer model or change the prompts, edit that file.

## Options

| Option | What it does |
| --- | --- |
| `SCRIPT.py` | The script to convert. Left out, the wizard asks for it |
| `--name NAME` | The command name, instead of asking |
| `--modules DIR` | Write the module to DIR instead of `~/.shellcraft/modules` |
| `--model MODEL` | The AI model for the `ai` option |

## Converting several scripts without questions

The wizard reads its answers from the input, so you can give them all at once. This converts a
whole folder with the default answers: auto, write it, no AI docs, run the test call:

```bash
for f in ~/scripts/*.py; do
  printf 'auto\ny\nn\ny\n' | shellcraft ingest "$f" --name "$(basename "$f" .py)"
done
```

The answers have to match the questions, so check the first one by hand. An extra question comes
up when a name is also a program on your system, or when a module of that name already exists.

## Is it a copy?

Yes. The module in `~/.shellcraft/modules` is a converted copy of your script. ShellCraft runs the
copy, so changing your original later doesn't change the command. Either edit the module
directly, or run the wizard on the original again: it asks before replacing the module.

Most people convert once, then keep working on the module file itself. Your original stays as
it was, for running outside ShellCraft.

## Check modules any time

```
ingest check ~/.shellcraft/modules/wordcount.py     # one module
ingest check --all                                   # every module ShellCraft loads
ingest check --all --strict                          # warnings count as failures too
ingest check NAME.py --network                       # also run tests that need the internet
```

From a terminal, use `shellcraft ingest check …` the same way.
