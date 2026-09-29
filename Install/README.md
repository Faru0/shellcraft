# Offline install (air-gapped lab)

Everything needed to run shellcraft on a machine with **no internet**.
Copy the whole repo (including this `Install/` folder) to the lab machine.

| Folder     | Contents |
|------------|----------|
| `windows/` | `python-3.14.7-amd64.exe`, the official python.org installer (newest stable) |
| `linux/`   | Python 3.14 `.deb` packages + dependencies for **Ubuntu 26.04 LTS (amd64)** |
| `wheels/`  | Every Python dependency (runtime, `dev`, `censys`) for Windows x64 and Linux x86_64, CPython 3.11 – 3.14 |

## Windows

1. Run `Install\windows\python-3.14.7-amd64.exe`. Tick **"Add python.exe to PATH"**.
2. From the repo root:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links Install\wheels -e ".[dev,censys]"
shellcraft
```

## Linux (Ubuntu 26.04 LTS)

1. Install Python. Ubuntu 26.04 already ships 3.14, so skip this if `python3 --version` shows 3.14:

```bash
sudo apt install ./Install/linux/*.deb     # use apt, not dpkg -i (apt orders the packages; no network needed)
```

2. From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --no-index --find-links Install/wheels -e ".[dev,censys]"
shellcraft
```

Other distros / Python versions: any CPython 3.11–3.14 on x86_64 (glibc ≥ 2.28) can use `wheels/`
as-is; only the `.deb` files are Ubuntu-26.04-specific.

## Notes

- python.org does not publish `.deb` files. Ubuntu packages Python 3.14.7 only in its development
  release (26.10), and those packages need a newer libc, so the LTS build (3.14.4) is bundled here.
- Verified in an Ubuntu 26.04 container with networking disabled: installing the debs, then the wheels
  with `--no-index`, then running the tests gave 328 passed.
- To refresh the wheels (on a machine with internet), re-run `pip download` with `--only-binary=:all:`
  for each `--platform` / `--python-version` combination and add `pywin32` explicitly for Windows
  (pip does not evaluate `sys_platform` markers for the target platform).
