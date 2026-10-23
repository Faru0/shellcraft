import sys, os, time, random, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(__file__))
from harness import Term, scan, ROOT

BIG = os.path.join(tempfile.gettempdir(), "shellcraft-stress-big.txt")
if not os.path.exists(BIG):
    with open(BIG, "w") as f:
        for i in range(300000): f.write(f"line {i} some text here with ERROR {i%7} and words\n")

# (command, max seconds before Ctrl-C, what to type after Ctrl-C to be back at the prompt)
CTRLC = [
    ("seq 1 400000 | grep 1", 1.5, b""),
    ("yes | head -300000", 1.5, b""),
    ("sleep 5", 1.0, b""),
    (f"cat {BIG} | sort | uniq -c | sort -rn | head", 2.0, b""),
    (f"grep ERROR {BIG} | wc -l", 1.0, b""),
    ("find / -name '*.conf' 2>/dev/null", 1.5, b""),
    ("for i in 1 2 3 4 5; do sleep 1; echo $i; done", 2.0, b""),
    ("while true; do echo loop; done", 1.0, b""),
    ("read name", 1.0, b""),
    ("man grep", 1.0, b"q"),
    ("sub bash -c 'seq 1 300000; sleep 3'", 1.5, b"\x1d"),   # Ctrl-] back to main
    ("sub sc", 1.5, b"exit\n"),
    ("/bin/sleep 5", 1.0, b""),
    ("echo $(sleep 3; echo late)", 1.0, b""),
    ("tree /usr/share", 1.0, b""),
    ("ls -R /usr/lib | wc -l", 1.0, b""),
    ("jq -r .x <<< '{\"x\": 1}'", 0.3, b""),
    ("theme nord", 0.2, b""),
    ("history | tail -3", 0.2, b""),
    ("help", 0.5, b""),
    ("sleep 4 &", 0.5, b""),
    ("jobs -f 1", 1.0, b""),
    ("curl https://10.255.255.1 --max-time 8", 1.5, b""),
    (f"cat {BIG}", 1.0, b""),
    (f"cat {BIG} | less", 1.0, b"q"),
    ("seq 1 100000 | sort -rn | head -3", 0.5, b""),
    ("", 0.1, b""),  # Ctrl-C at the idle prompt
]

QUICK = ["echo x", "pwd", "true", "ls", "history | tail -1", "theme nord", "seq 1 20"]

MISC = [  # (what to send, seconds to wait, keys to recover with)
    (b"cat /bin/ls | head -c 3000\n", 2, b""),
    (b"printf '\\xff\\xfe bad \\xc3 utf\\n'\n", 1, b""),
    (b"seq 1 30000 | tr '\\n' ' '\n", 2, b""),
    (b"echo \x1b[31mred\x1b[0m \x1b]0;title\x07 \x1b[2J\n", 1, b""),
    (b"echo -e '\\x1b[6n'\n", 1, b""),               # a command printing a CPR request itself
    (b"printf '\\e[?1000h'; sleep 1; printf '\\e[?1000l'\n", 2.5, b""),
    (b"echo one\necho two\necho three\n", 1, b""),      # a pasted block
    (b"\x1b[200~echo pasted\nsleep 1\x1b[201~\n", 2.5, b""),  # bracketed paste
    (b"\x04", 1, b""),                                   # Ctrl-D at the prompt
    (b"\x1a", 1, b""),                                   # Ctrl-Z
    (b"\x1c", 1, b""),                                   # Ctrl-\\
    (b"\x1b\x1b\x1b[A\x1b[B\x1bOA\x1b[Z\x1b[?", 1, b"\x15\n"),  # raw escape junk
    (b"\x1b[<0;10;5M\x1b[<0;10;5m", 1, b"\x15\n"),   # a mouse click at the prompt
    (b"sleep 2 &\n\x1b[<0;10;5M\x1b[<0;10;5m", 1, b"\x15\n"),
    (b"seq 1 200000\n", 0.2, b"\x03"),                  # type while output streams, then interrupt
    (b"echo '\n", 1, b"\x03"),                          # an unfinished quote
    (b"for i in 1 2 3; do\n", 1, b"\x03"),
    (b"x" * 5000 + b"\n", 2, b""),
    (b"echo " + b"a" * 20000 + b" | wc -c\n", 2, b""),
    (b"alias l='ls'; l; unalias l\n", 1, b""),
    (b"cd /nonexistent; cd /tmp; cd -\n", 1, b""),
    (b"settings footer off; settings footer on\n", 1, b""),
    (b"modules; modules disable myip; modules enable myip\n", 2, b""),
    (b"RESIZE", 1.5, b""),
    (b"sleep 3\nRESIZE", 1.5, b"\x03"),
    (b"!!\n", 1, b""),
    (b"echo $?; echo $NOPE; echo ${x:-dflt}\n", 1, b""),
    (b"f() { echo in f; }; f\n", 1, b""),
    (b"sub bash -c 'printf \"\\e[?1003h\"; sleep 2'\n", 3.5, b"\x1d"),
]

def ensure_prompt(t, note):
    """Back to a clean prompt, recording what it took."""
    status = t.wait_prompt(15)
    if status == "hang":
        for keys in (b"\x03", b"q", b"\x1d", b"\x03\x15"):
            t.send(keys); status = t.wait_prompt(4)
            if status == "ok": note.append("RECOVERED-WITH:" + repr(keys)); break
    if status == "ok" and t.prompt_text()[4:].strip():
        note.append("LEFTOVER-INPUT:" + repr(t.prompt_text()[4:].strip())); t.send(b"\x15"); time.sleep(0.2)
    return status

def run(seed, rounds, log, group):
    random.seed(seed)
    home = tempfile.mkdtemp(prefix="sc-stress-")
    t = Term(home)
    status = t.wait_prompt(25)
    results = []
    if status != "ok":
        results.append({"step": "start", "status": status}); return results
    for n in range(rounds):
        note = []; since = len(t.raw)
        if group == "ctrlc":
            cmd, maxwait, after = random.choice(CTRLC); delay = random.uniform(0, maxwait)
            t.send(cmd.encode() + b"\n"); time.sleep(delay)
            mouse = t.mouse_on()
            t.send(b"\x03")
            if after and t.wait_prompt(0.8) != "ok":
                t.send(after)
        elif group == "fast":
            cmd = random.choice(QUICK); delay = random.uniform(0, 0.25); after = b""
            t.send(cmd.encode() + b"\n"); time.sleep(delay); mouse = t.mouse_on()
            for _ in range(random.choice((1, 1, 3, 6))):
                t.send(b"\x03"); time.sleep(random.uniform(0.01, 0.08))
        elif group == "mouse":
            cmd, maxwait, after = random.choice(CTRLC[:8]); delay = random.uniform(0, maxwait)
            t.send(cmd.encode() + b"\n"); time.sleep(delay); mouse = t.mouse_on()
            t.send(b"\x1b[<0;10;5M\x1b[<0;10;5m\x1b[<64;10;5M")  # click and wheel while it runs
            time.sleep(0.3); t.send(b"\x03")
        else:
            data, wait, after = MISC[n % len(MISC)]; cmd = repr(data)[:60]; delay = wait
            if data == b"RESIZE":
                t.resize(30, 80); time.sleep(0.5); t.resize(40, 120)
            elif data.endswith(b"RESIZE"):
                t.send(data[:-6]); time.sleep(0.5); t.resize(30, 80); time.sleep(0.5); t.resize(40, 120)
            else:
                t.send(data)
            time.sleep(wait); mouse = t.mouse_on()
            if after and t.wait_prompt(0.8) != "ok": t.send(after)
        status = ensure_prompt(t, note)
        found, plain = scan(t, since)
        found += note
        if mouse: found.append("MOUSE-MODE-ON-DURING-COMMAND")
        entry = {"step": n, "cmd": cmd, "delay": round(delay, 2), "status": status, "found": found}
        if status != "ok" or found:
            lines, _ = t.lines()
            entry["screen"] = [l for l in lines if l.strip()][-14:]
            entry["tail"] = plain[-800:]
        results.append(entry)
        log.write(json.dumps(entry, ensure_ascii=False) + "\n"); log.flush()
        if status == "dead" or not t.alive:
            entry["exit"] = t.exited()
            t = Term(home)
            if t.wait_prompt(25) != "ok": break
        elif status == "hang":
            t.kill(); t = Term(home)
            if t.wait_prompt(25) != "ok": break
    t.send(b"exit\n"); time.sleep(1); t.kill()
    shutil.rmtree(home, ignore_errors=True)
    return results

if __name__ == "__main__":
    seed = int(sys.argv[1]); rounds = int(sys.argv[2]); group = sys.argv[3]
    with open(f"log-{group}-{seed}.jsonl", "w") as log:
        res = run(seed, rounds, log, group)
    bad = [r for r in res if r.get("status") != "ok" or r.get("found")]
    print(f"[{group} seed {seed}] {len(res)} steps, {len(bad)} with findings")
    for r in bad: print("  ", json.dumps({k: r[k] for k in r if k not in ("tail", "screen")}, ensure_ascii=False)[:300])
