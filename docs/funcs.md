# Funcs

← [README](../README.md) · [All docs](README.md)

A func is a named script of ShellCraft lines that runs when you call it: like your rc file, on
demand. `func run recon example.com` runs its steps one after another in your shell, as if you
typed them (a `cd` or `env set` stays), with `$1`, `$@` and `$#` set to the arguments. It stops at
the first step that fails and says which. A func is not a pipe stage: it runs on its own line.

```
func new recon --save                  # guided: a few questions, then the steps one per line
func                                   # the funcs you can run
func show recon                        # its header and steps, numbered and highlighted
func run recon example.com             # run it
recon example.com                      # the same, when its header says `command: yes`
```

## The file

`~/.shellcraft/funcs/recon.func`:

```
# func: recon
# description: Quick domain recon: certificates, DNS, owners
# system: no
# output: show
# command: yes
# usage: recon DOMAIN
queryCert -s $1 > subs-$1.txt
queryDns $1
@ cat subs-$1.txt | wc -l
echo "recon of $1 done"
```

| Header | Meaning |
| --- | --- |
| `description` | One line, shown by `func list` |
| `system` | `yes`: it runs OS programs, so it is refused while OS commands are off (`settings system_commands on`) |
| `output` | `show`: each step's output prints. `quiet`: only `echo` / `printf` steps and steps starting with `@` print. Errors always do |
| `command` | `yes`: typing `recon example.com` runs it, besides `func run recon example.com`. A builtin or module with the same name wins |
| `usage` | How to call it, shown by `func list` and `func show` |

The command lines themselves are never printed, as in a bash script; a muted line ends the run:
`✓ recon: 4 steps, 2.3 s`, or `✗ recon: step 2 failed (queryDns example.com): …`. A step is a
whole command, so a `for … done` written over several lines is one step. Other `#` lines are
comments.

## Running

| Command | What it does |
| --- | --- |
| `func run NAME [ARG...]` | Run it in your shell, as if typed |
| `func run -i NAME …` | Isolated: afterwards the directory, shell variables, environment variables, aliases, theme and settings are put back, and nothing is saved while it runs |
| `func run -b NAME …` | In the background, as a job in the footer (`jobs N` shows its output). It runs in the job's own context, so steps that change the shell (`cd`, `env set`) are refused there |
| `func run -q` / `-v NAME …` | Quiet or show, this time, whatever the header says |

## Making and changing funcs

| Command | What it does |
| --- | --- |
| `func new NAME` | Guided: the description, whether it runs OS commands, show or quiet, whether its name is a command, a usage line, then the steps (`step 1 ❯`, an empty line ends) |
| `func new NAME -e` | Write it in your editor instead ([the editor](#the-editor)) |
| `func new NAME 'CMD' 'CMD'…` | All at once, with `-d DESC`, `--system`, `--quiet`, `--command` |
| `--save` / `-g` | Keep it on disk: the active profile's folder, or with `-g` the default setup's. Without `--save` it lasts until you leave ShellCraft |
| `func save NAME [-g]` | Put a session func on disk |
| `func edit NAME` | Open it in your editor |
| `func add NAME 'CMD'` · `insert NAME N 'CMD'` · `rm NAME N` · `move NAME FROM TO` | Change the steps |
| `func set NAME FIELD VALUE` | `description`, `system` (yes/no), `output` (show/quiet), `command` (yes/no), `usage` |
| `func rename OLD NEW` · `copy SRC DST [-g]` · `delete NAME [-y]` | Manage them |
| `func export NAME\|--all [FILE]` · `import FILE [-f] [-g]` | Share them as one JSON file |
| `func path [NAME]` | Where it lives |

## Where funcs live

- `~/.shellcraft/funcs/` holds the default setup's funcs, which work in every profile.
- `~/.shellcraft/profiles/NAME/funcs/` holds a profile's own; one with the same name as a
  default func wins while the profile is active, and `func list` says `(hides default)`.
- A func made without `--save` lives in the session, in every profile, until you leave.

`profile export` / `import` carry a profile's funcs, and `profile save` copies the active
profile's funcs and the session's. Funcs are never available to AI clients over MCP.

## The editor

`func edit`, `func new -e` and `profile edit` open files in the editor set with
`settings editor`:

```
settings editor                 # the one in use, and the editors found on this machine
settings editor code            # VS Code; also codium, subl, vim, nvim, nano, micro… or any command line
settings reset editor           # back to $VISUAL, then $EDITOR, then nano (notepad on Windows)
```

A terminal editor (vim, nano…) opens in a subshell with the whole terminal. A window editor
(VS Code, Sublime…) opens its own window; ShellCraft adds its "wait" switch (`code --wait`) and
reads the file back when you close the tab. The setting is shared by every profile.
