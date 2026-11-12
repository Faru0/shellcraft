# ask: the AI at your prompt

← [Manual](README.md)

`ask` puts a question to the AI without leaving ShellCraft. You do not open a second terminal and you do
not explain what you were doing: it already has your folder, your last commands and what the last one printed.

```
ls /nope
ask why did that fail
```

```
`ls` failed because `/nope` doesn't exist. Check the spelling, or look at what is there:

    ls /
✦ 4s · $0.04 · ask -c carries on
```

The answer is written out as it arrives. The last line shows how long it took and what it cost.

## What you need

- The Claude Code client installed and logged in (`claude --version`).
- Nothing else. You do not have to run `claude mcp add` or `mcp start` for `ask`: it starts the client
  itself and connects it to the shell you typed the question in.

If the client is somewhere else, set `SHELLCRAFT_CLAUDE` to its path.

## Asking

Type the question as you would say it. A line that starts with `ask` and whose question does not start with
a quote is taken exactly as typed, to the end of the line:

```
ask why didn't that work?
ask what does the # in line 3 mean
ask is `set -e` on here, and what does a | b > c do then
```

No quotes needed for `'`, `?`, `#`, `!`, `|` or `>`. They are part of the question.

When you want shell syntax on the same line, start the question with a quote. Then the line is an ordinary
command line:

```
ask "list the ten biggest files here" > big.txt
ask "which of these look odd" | grep -i warn
```

Pipe something in and it is added to the question:

```
dmesg | tail -50 | ask what is wrong here
cat nginx.conf | ask is the proxy block right
```

## Fix it

```
mkdir one/two/three
ask fix it
```

```
✦ set_clip: mkdir -p one/two/three
mkdir failed because one/ and one/two/ don't exist yet. Adding -p creates them too:

    mkdir -p one/two/three

Press Alt-v to paste it.
```

When the answer is a command, the AI puts it in the clip register. Press **Alt-v** and it is on your prompt,
ready to read, change and run. The grey `✦` lines tell you which tools it used. They show on your screen
only: they never land in a file or a pipe.

## Carrying on

`ask -c` continues the last question instead of starting fresh, so "it" still means what you were talking about:

```
ask how do I see which ports are open
ask -c and only the ones listening on IPv6?
```

## In the background

A question that will take a while can run as a job:

```
ask -b go through today's auth.log and summarise the failed logins
```

It shows in the footer and in `jobs` with a `✦`. `jobs N` shows the answer, `jobs -k N` stops it. You keep
your prompt the whole time.

## What it may do

`ask` adds no rules of its own. It follows the same switch as every other AI client, `ai on` and `ai off`
(see [MCP and the AI co-pilot](mcp-and-ai.md)):

| Setting | What `ask` does |
| --- | --- |
| `ai on` (the default) | It may run commands in your shell. Each one shows as a `✦ AI` job, with its output, and is written to `ai log`. |
| `ai off` | It only reads. A command it would run goes to the clip register instead, for you to paste and run. |

Your rules in `~/.shellcraft/ai-policy.toml` apply as well. The client's own tools (its own shell, its file
editing) are switched off for `ask`, so everything it does goes through your shell, where you see it.

What it is sent with the question: the working folder, your last five commands with their exit status, and
the last 60 lines the newest one printed. Passwords, tokens and keys in that output are replaced by
`REDACTED` first. `ask -n QUESTION` shows you exactly what would be sent, and the client's command line,
without running anything.

Press Ctrl-C to stop an answer. The client is stopped with it.

## Cost

Each question is one run of the client and is billed to your Claude account like any other use of it. A
short question costs a few cents. Two settings keep it in hand:

| Setting | Default | What it is |
| --- | --- | --- |
| `ask_budget` | `0.50` | The most one question may cost, in USD. The client stops when it is spent. `settings ask_budget 1` |
| `ask_model` | not set | The model `ask` uses. Not set: the client's own default. `settings ask_model NAME` |

`ask --budget 2 QUESTION` and `ask --model NAME QUESTION` set them for one question.
`settings reset ask_budget` goes back to the default.

## Options

| Option | Meaning |
| --- | --- |
| `-b`, `--background` | Run as a `✦` background job. |
| `-c`, `--continue` | Carry on from the last `ask` of this session. |
| `-n`, `--dry-run` | Show the command line and what would be sent. Nothing runs. |
| `--budget USD` | The most this question may cost. |
| `--model NAME` | The model for this question. |

## When something is off

| You see | What to do |
| --- | --- |
| `the Claude Code client (claude) is not on PATH` | Install it, or set `SHELLCRAFT_CLAUDE` to its path. |
| `stopped: the budget of $0.5 for one run was reached` | The question needed more steps. Ask again with `--budget 1`, or raise `ask_budget`. |
| `needs a running interactive shell` | `ask` was run with `shellcraft -c` or from a script with no shell open. It needs the shell, because the AI looks at your screen through it. |
| `the shellcraft tools did not start` | The AI answers from the question alone. Run `shellcraft --mcp` in a terminal to see why the server fails. |
| An answer about the wrong thing | It only sees the last 60 lines of the last command. Pipe the output in: `COMMAND | ask ...`. |

`ask` is for you at the prompt. An AI client cannot call it over MCP.
