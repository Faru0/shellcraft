---
name: ebpf-audit
description: Auditing eBPF, kprobes and uprobes on a Linux host through sshrun and hostscan - what is loaded, who loaded it, what it hooks, and which combinations are red flags. Load it for "what is running in eBPF", "check kprobes and uprobes", "rootkit".
---

# eBPF, kprobes and uprobes

eBPF is where legitimate observability and modern rootkits look alike. Your job is to list what is there,
find who owns it, and compare it with what the host should be running. Root is needed: the host must be
marked `sudo = true` or `root = true`, or the checks refuse.

## Collect

```
sshrun HOST bpf-progs   | hostscan bpf       # loaded programs: type, name, tag, owner PIDs
sshrun HOST bpf-links   | hostscan bpf       # what each program is attached to
sshrun HOST bpf-maps    | hostscan bpf       # maps (names like ports, pids, hidden are noteworthy)
sshrun HOST probes      | hostscan probes    # kprobe_events and uprobe_events through tracefs
sshrun HOST modules     | baseline diff HOST modules
```

Every answer through `baseline diff HOST CHECK` keeps the first look as the normal and shows only changes
after it. A new program is a stronger signal than an old one.

## Read the result

- **Owner.** `pids` on a program names the process holding it. Known tools (systemd, cilium, tetragon, falco,
  bpftrace, datadog, otelcol) are expected where the user runs them; ask `ask_user` once if you are unsure
  what the host should run. A program with **no owner pid** is held only by a pinned file or a link: look.
- **Type.** `tracing`, `lsm`, `cgroup_*` for monitoring are common. `kprobe`, `kretprobe`, `raw_tracepoint`
  with no name or an odd one, `xdp`/`sched_cls` (packet path) and `sk_msg`/`sk_skb` (socket data) are worth
  knowing the reason for.
- **Hooks.** Rootkit style: a kprobe or tracepoint on `getdents64`/`filldir` (hides files), `tcp4_seq_show` and
  friends (hides connections), `kill` (signals), `read`/`write` of auth files, `bpf` itself. Observability style:
  `execve`, `tcp_connect`, `inet_sock_set_state`, `sched_process_exec`.
- **Uprobes** on `libc`, `libssl`, `libpam`, `sshd` or `sudo` symbols read credentials as they pass. An
  uprobe on a binary under `/tmp` or `/dev/shm` is high.
- **Maps** named after what they hide (`hidden_pids`, `ports`) next to a program that hooks directory reads.

## Rate and record

Two signs make it `high`: a program with no owner **and** a hook on a hiding syscall, or an uprobe on `pam`/`ssh`
**and** a writable-path binary. Anything else you cannot explain is `medium` with `confidence: low`, and
you ask the user ("Is `tetragon` supposed to run on web01?"). Record each as a finding (`kind: process` or
`host`, the program id and name as the value) with the raw `bpftool` lines.

You cannot unload anything and must not suggest a command that does without `request_human_review`.
Removing the wrong program can blind the user's own monitoring.
