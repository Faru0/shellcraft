# Stress harness

A pseudo-terminal driver that runs ShellCraft the way a person would, badly: commands fired
and interrupted with Ctrl-C at random moments, keys and mouse reports at the wrong time,
resizes mid-command, bad bytes. It renders the screen with `pyte` and answers cursor-position
queries with a realistic delay, so timing races show up. After every step it checks for a
traceback, a dead process, a hung prompt, escape fragments on the prompt line and leftover
input.

```
python tools/stress/runner.py SEED ROUNDS ctrlc    # commands + Ctrl-C at random moments
python tools/stress/runner.py SEED ROUNDS fast     # quick commands, Ctrl-C as the prompt redraws
python tools/stress/runner.py SEED ROUNDS mouse    # clicks and wheel while a command runs
python tools/stress/runner.py SEED ROUNDS misc     # bad UTF-8, binary, pastes, resizes, Ctrl-D/Z/\
python tools/stress/probes.py                       # the fixed bugs, as one-line yes/no checks
```

Each run writes `log-GROUP-SEED.jsonl` in the current folder, one line per step, with the
screen and the raw output tail for any step that had findings. Linux and macOS only (it needs
`pty`); a few steps take a while because the harness renders every line of a 300,000-line
output. See `STRESS-TEST.md` at the root for what the first runs found and what was done.
