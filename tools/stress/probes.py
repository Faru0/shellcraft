import sys, os, time, random, tempfile, re, subprocess; sys.path.insert(0, os.path.dirname(__file__))
from harness import Term, scan, MODES, ROOT, PY
from runner import CTRLC, BIG
random.seed(7)
# 1. ^C glued to the prompt
t = Term(tempfile.mkdtemp()); t.wait_prompt(25); glued = 0; interrupted = 0
for n in range(30):
    since = len(t.raw); t.send(b"echo x\n"); time.sleep(random.uniform(0, 0.12)); t.send(b"\x03"); t.wait_prompt(10)
    tail = bytes(t.raw[since:]).decode(errors="replace")
    if re.search(r"\^C\x1b\[[0-9;]*m?╭|\^C╭", tail) or re.search(r"\^C[^\r\n]{0,12}╭", tail): glued += 1
    if "interrupted" in tail: interrupted += 1
print(f"1. ^C after a quick command: {glued}/30 times the prompt header followed ^C on the same line; 'interrupted' printed {interrupted}/30")
t.send(b"exit\n"); time.sleep(0.4); t.kill()

# 2. Ctrl-C inside sub sc / sub bash, then the state
t = Term(tempfile.mkdtemp()); t.wait_prompt(25)
for label, keys in (("sub sc + Ctrl-C + exit", [(b"sub sc\n", 2.5), (b"\x03", 0.6), (b"exit\n", 1.0)]),
                    ("sub bash + Ctrl-C + exit", [(b"sub bash\n", 1.5), (b"\x03", 0.6), (b"exit\n", 1.0)]),
                    ("sub bash sleep + Ctrl-C", [(b"sub bash -c 'sleep 5'\n", 1.0), (b"\x03", 1.0)])):
    since = len(t.raw)
    for k, w in keys: t.send(k); time.sleep(w)
    st = t.wait_prompt(10); found, plain = scan(t, since)
    seq = [f"{m.group(1).decode()}{'+' if m.group(2)==b'h' else '-'}" for m in MODES.finditer(bytes(t.raw[since:])) if m.group(1) in (b"1000", b"1002", b"1003")]
    t.send(b"sleep 0.5\n"); time.sleep(0.25); during = t.mouse_on(); t.wait_prompt(10)
    print(f"2. {label:28} prompt {st}; mouse at prompt after: {'ON' if t.mouse_on() else 'off'}; during next command: {'ON' if during else 'off'}; findings {found}")
t.send(b"exit\n"); time.sleep(0.4); t.kill()

# 3. visibility after Ctrl-C steps
t = Term(tempfile.mkdtemp()); t.wait_prompt(25); invisible = []
for n in range(30):
    cmd, maxwait, after = random.choice(CTRLC); since = len(t.raw)
    t.send(cmd.encode() + b"\n"); time.sleep(random.uniform(0, maxwait)); t.send(b"\x03")
    if after and t.wait_prompt(0.8) != "ok": t.send(after)
    if t.wait_prompt(15) != "ok": t.send(b"\x03q\x1d\x03"); t.wait_prompt(5)
    if t.prompt_text()[4:].strip(): t.send(b"\x15"); time.sleep(0.2)
    t.send(f"echo marker-{n}-ok\n".encode()); st = t.wait_prompt(10)
    lines, _ = t.lines()
    if not any(f"marker-{n}-ok" in l and not l.startswith("╰─❯") for l in lines):
        invisible.append((n, cmd[:40], st, [l for l in lines if l.strip()][-5:]))
print(f"3. visibility: {len(invisible)}/30 echo outputs missing after a Ctrl-C step")
for i in invisible[:4]: print("   ", i)
t.send(b"exit\n"); time.sleep(0.4); t.kill()

# 4. Ctrl-C during startup
crashes = 0; outs = []
for d in (0.03, 0.08, 0.12, 0.16, 0.2, 0.3, 0.45, 0.6):
    t = Term(tempfile.mkdtemp()); time.sleep(d); t.send(b"\x03"); time.sleep(2.5)
    raw = bytes(t.raw).decode(errors="replace"); alive = t.alive and t.exited() is None
    if "Traceback" in raw: crashes += 1; outs.append((d, raw[-400:]))
    outs.append((d, "alive" if alive else f"exited {t.exited()}", "Traceback" in raw))
    t.send(b"exit\n"); time.sleep(0.4); t.kill()
print(f"4. Ctrl-C during startup: {crashes}/8 tracebacks;", [(o[0], o[1]) for o in outs if len(o) == 3])
for o in outs:
    if len(o) == 2: print("   TRACE at", o[0], repr(o[1][-300:]))
