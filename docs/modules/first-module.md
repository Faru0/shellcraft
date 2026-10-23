# Your first module

← [Manual](../README.md)

A module is a Python file that becomes a ShellCraft command. This page starts with the smallest
module that works, three lines long, and builds up step by step to a command that calls a web API.
Every block of code is a complete file you can copy.

You need ShellCraft running and a text editor. You don't need to restart anything: ShellCraft
notices a new or changed module and loads it at the next prompt.

## Where modules go

Your modules live in `~/.shellcraft/modules/` (`C:\Users\you\.shellcraft\modules\` on Windows).
Create the folder if it isn't there yet:

```bash
mkdir -p ~/.shellcraft/modules
```

The file name is the command name. `hello.py` becomes the command `hello`.

## Step 1: the bare minimum

Create `~/.shellcraft/modules/hello.py` with this in it:

<!-- file: hello.py step 1 -->
```python
def run(args, stdin):
    return "hello\n"
```

Now type `hello` at the ShellCraft prompt:

```
❯ hello
hello
```

That's a working command. Here is everything that matters about it:

- ShellCraft calls a function named **`run`**, with exactly two parameters.
- **`args`** is a list of the words typed after the command name, already split and unquoted.
  For `hello Ada "Grace Hopper"` it's `["Ada", "Grace Hopper"]`.
- **`stdin`** is the text piped into the command, as one string. It's `""` when nothing is piped in.
- **Return your output** as a string, ending with a newline. Don't `print()` it: ShellCraft needs
  the returned text to send it down a pipe, into a file, or to an AI client.

Because it returns text, it already works with pipes and redirects:

```
❯ hello | wc -c
6
❯ hello > greeting.txt
```

## Step 2: use the arguments

<!-- file: hello.py step 2 -->
```python
def run(args, stdin):
    name = args[0] if args else "world"
    return f"hello {name}\n"
```

```
❯ hello
hello world
❯ hello Ada
hello Ada
```

## Step 3: switches, piped input and errors

Real commands take switches like `-s`. Use `ArgParser` from `shellcraft.modkit` for that. It's
Python's `argparse` with one difference: a bad argument ends the command with a clean error
message instead of exiting Python. You also get Tab completion and usage messages for free.

<!-- file: hello.py -->
```python
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Say hello to someone, or to every name piped in"


def run(args, stdin):
    parser = ArgParser("hello")
    parser.add_argument("names", nargs="*", metavar="NAME")
    parser.add_argument("-s", "--shout", action="store_true")
    opts = parser.parse_args(args)

    names = opts.names or stdin.split()
    if not names:
        raise ModuleError("hello: who should I greet? Try: hello Ada")

    lines = []
    for name in names:
        text = f"hello {name}"
        lines.append(text.upper() if opts.shout else text)
    return "\n".join(lines) + "\n"
```

```
❯ hello -s Ada Linus
HELLO ADA
HELLO LINUS
❯ echo Ada Grace | hello
hello Ada
hello Grace
❯ hello
╭─ ✗ command failed ──────────────────────────╮
│ hello  (command)                            │
│ hello: who should I greet? Try: hello Ada   │
╰─────────────────────────────────────────────╯
```

Three new things:

- **`SUMMARY`** is the one line `help` shows next to your command.
- **`stdin.split()`** reads names from the pipe when none are typed, like most Unix tools do.
- **`raise ModuleError("…")`** is how a module fails. The user sees your message, and the pipeline
  stops there. Start the message with the command name.

## Step 4: call a web API

Now a more useful command. `ghrepo` asks GitHub how many stars and forks a repository has. Create
`~/.shellcraft/modules/ghrepo.py`:

<!-- file: ghrepo.py -->
```python
import os

import requests

from shellcraft.modkit import ArgParser, EnvSetting, ModuleError, check_cancel

SUMMARY = "Show the stars, forks and language of GitHub repositories"
SPINNER_TEXT = "asking GitHub…"
ENV_SETTINGS = [
    EnvSetting("GITHUB_TOKEN", "GitHub token",
               "Optional. Raises the limit from 60 to 5000 requests an hour: "
               "github.com/settings/tokens, no scopes needed."),
]


def run(args, stdin):
    parser = ArgParser("ghrepo")
    parser.add_argument("repos", nargs="*", metavar="OWNER/REPO")
    opts = parser.parse_args(args)

    repos = opts.repos or stdin.split()
    if not repos:
        raise ModuleError("ghrepo: which repository? Try: ghrepo python/cpython")

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    lines = []
    for repo in repos:
        check_cancel()  # Ctrl-C stops here
        try:
            reply = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=10)
        except requests.RequestException as exc:
            raise ModuleError(f"ghrepo: can't reach GitHub: {exc}") from None
        if reply.status_code == 404:
            raise ModuleError(f"ghrepo: no repository called {repo}")
        if reply.status_code != 200:
            raise ModuleError(f"ghrepo: GitHub answered {reply.status_code} for {repo}")
        data = reply.json()
        language = data.get("language") or "-"
        lines.append(f"{repo}\t{data['stargazers_count']}\t{data['forks_count']}\t{language}")
    return "\n".join(lines) + "\n"
```

```
❯ ghrepo python/cpython BurntSushi/ripgrep
python/cpython        77745  38310  Python
BurntSushi/ripgrep    69364  5488   Rust
❯ cat repos.txt | ghrepo | sort -k2 -n > by-stars.tsv
```

What's new here, and why:

- **`timeout=10`** on the request. Always give network calls a timeout, so a slow server can't
  hang your command. Ctrl-C can't interrupt a request that's waiting.
- **`check_cancel()`** inside the loop. When you press Ctrl-C, ShellCraft asks the command to stop,
  and this is where it does.
- **Errors become `ModuleError`s** with a clear message, instead of a long Python traceback.
- **`SPINNER_TEXT`** is shown next to a spinner while the command is slow.
- **One line per result, separated by tabs.** That's what makes `sort`, `cut`, `grep` and `> file`
  work on your output.
- `requests` already comes with ShellCraft. For any other package, install it into the same Python
  that runs ShellCraft.

### An API key

`ENV_SETTINGS` declares the environment variables your module reads. ShellCraft then lists the
key in `settings`, lets the user store it with hidden typing, and passes it to your module as an
environment variable:

```
❯ settings GITHUB_TOKEN
GitHub token (GITHUB_TOKEN): ••••••••
```

Your code just reads `os.environ`. Never ask for the key with `input()`: a module can't ask
questions, because it may be running in a pipe or for an AI client.

## Step 5: give it a manual page

Create `~/.shellcraft/modules/ghrepo.md`. This is what `man ghrepo` shows:

<!-- file: ghrepo.md -->
````markdown
# ghrepo

Show how many stars and forks GitHub repositories have, and their main language.

## Synopsis

```
ghrepo OWNER/REPO...
... | ghrepo
```

## Description

`ghrepo` asks the GitHub API about each repository and prints one line per repository:
the name, stars, forks and language, separated by tabs. With no names it reads them from the
pipe, one or more per line.

## Options

| Option | Meaning |
| --- | --- |
| `OWNER/REPO` | A repository, such as `python/cpython`. Give as many as you like. |

## API key

It works without a key, up to 60 requests an hour. A GitHub token raises that to 5000:
create one at github.com/settings/tokens (no scopes needed), then run `settings GITHUB_TOKEN`.

## Examples

```
ghrepo python/cpython
ghrepo python/cpython BurntSushi/ripgrep | sort -t$'\t' -k2 -n
cat repos.txt | ghrepo > stars.tsv
```

## See also

`man curl`
````

Keep these headings in this order: the title, a one line description, Synopsis, Options and
Examples are the ones ShellCraft checks for. List every switch in the Options table, inside
backticks, because Tab completion reads its help text from there.

## Step 6: let AI clients use it

Create `~/.shellcraft/modules/ghrepo.skill`. It describes the command to an AI client connected
over MCP, and lists its parameters with their types:

<!-- file: ghrepo.skill -->
```toml
summary = "Look up GitHub repositories and return one line each: name, stars, forks and main language, tab separated."

when_to_use = """
Use when you need to know how popular or active a GitHub repository is, or to compare several.
Do not use it for anything but public repositories on github.com."""

usage = "ghrepo OWNER/REPO..."

examples = [
  '{"repos": ["python/cpython"]}  -> "python/cpython\t77745\t38310\tPython\n"',
  '{"stdin": "psf/requests\nBurntSushi/ripgrep"}  -> one line per repository',
]

notes = """
Calls api.github.com. Without GITHUB_TOKEN GitHub allows 60 requests an hour; the user sets a
token with `settings GITHUB_TOKEN`. A repository that doesn't exist is a tool error."""

[[params]]
name = "repos"
type = "array"
metavar = "OWNER/REPO"
description = "Repositories such as python/cpython. Leave out to read them from stdin."

[[tests]]
args = []
error = "which repository?"

[[tests]]
args = ["python/cpython"]
contains = "python/cpython\t"
network = true
```

Write it for an AI reader: say what the command returns, when to use it, and when not to. Put
every plain `key = value` line before the first `[[params]]`, or TOML hides it inside that table.
The `[[tests]]` at the end are run by the checker and are never shown to the AI.

Without a `.skill` file, a module is still a command in your shell. It just isn't offered to AI
clients.

## Step 7: check it

The checker loads your module the way ShellCraft does and tests everything this page talked about:

```
❯ ingest check ~/.shellcraft/modules/ghrepo.py
╭─ ghrepo PASSED  18 pass · 0 warn · 0 fail · 2 skipped ─────────────╮
…
```

A `FAIL` line says what to fix. `--network` also runs the tests that need the internet.

## The rules, in short

| Do | Don't |
| --- | --- |
| Name the function `run(args, stdin)` | Read `sys.argv` or `sys.stdin` |
| Return your output as text | `print()` it |
| Raise `ModuleError("name: message")` | Call `sys.exit()` or `exit()` |
| Use `ArgParser` for switches | Use plain `argparse`, which exits Python on a bad switch |
| Give network calls a `timeout` | Ask questions with `input()` |
| Read keys from `os.environ` (`ENV_SETTINGS`) | Write your own config file |

The [module reference](reference.md) has every rule and option.

## Next steps

- Already have a script that works on its own? [Ingest it](ingest.md) instead of rewriting it.
- Have module files from someone else? See [Add modules you already have](adding-modules.md).
