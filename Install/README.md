# Offline install (air-gapped lab)

Everything needed to run shellcraft on a machine with **no internet**.
Copy the whole repo (including this `Install/` folder) to the lab machine.

**Quick start:** install Python (below), then run the setup script from anywhere. The first run
creates `.venv/` in the repo root and installs the wheels offline, then starts shellcraft (`main.py`).
Later runs reuse the venv and skip the install (it runs again only when `requirements.txt` changes
or a package is missing), so they start straight away. Arguments pass through to `main.py`, e.g. `--no-banner` or `--mcp`.

| OS      | Command |
|---------|---------|
| Windows | `powershell -ExecutionPolicy Bypass -File .\Install\setup.ps1` |
| Linux   | `./Install/setup.sh` |

The manual steps are below if you'd rather do it by hand.

| Folder     | Contents |
|------------|----------|
| `windows/` | `python-3.14.7-amd64.exe`, the official python.org installer (newest stable) |
| `linux/`   | Python 3.14 `.deb` packages + dependencies for **Ubuntu 26.04 LTS (amd64)** |
| `wheels/`  | Every runtime dependency, plus `censys-platform` and `pytest`, for Windows x64 and Linux x86_64, CPython 3.11 – 3.14, including `pyte` (the subshell emulator) and `pywinpty` (ConPTY for `sub` on Windows, one wheel per CPython version). Also `pycurl` for Windows x64 (one wheel per CPython version); setup doesn't install it, so add it yourself with `pip install --no-index --find-links Install\wheels pycurl` |

## Windows

1. Run `Install\windows\python-3.14.7-amd64.exe`. Tick **"Add python.exe to PATH"**.
2. From the repo root:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links Install\wheels -r requirements.txt "censys-platform>=0.16" "pytest>=8"
python main.py
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
pip install --no-index --find-links Install/wheels -r requirements.txt "censys-platform>=0.16" "pytest>=8"
python main.py
```

Other distros / Python versions: any CPython 3.11–3.14 on x86_64 (glibc ≥ 2.28) can use `wheels/`
as-is; only the `.deb` files are Ubuntu-26.04-specific.

## Notes

- python.org does not publish `.deb` files. Ubuntu packages Python 3.14.7 only in its development
  release (26.10), and those packages need a newer libc, so the LTS build (3.14.4) is bundled here.
- Verified in an Ubuntu 26.04 container with networking disabled (September 2026): installing the
  debs, then the wheels with `--no-index`, then running the tests. The wheel install was checked again
  offline in October 2026 with ShellCraft 2.0.0.
- Not bundled: the lint and packaging tools of the `dev` extra (`ruff`, `mypy`, `build`), so
  `pip install -e ".[dev]"` needs the internet.
- To refresh the wheels (on a machine with internet), re-run `pip download` with `--only-binary=:all:`
  for each `--platform` / `--python-version` combination and add `pywin32` explicitly for Windows
  (pip does not evaluate `sys_platform` markers for the target platform).
