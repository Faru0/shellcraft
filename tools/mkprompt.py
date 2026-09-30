#!/usr/bin/env python3
"""mkprompt — build a ready-to-paste AI prompt that writes a module's .md and .skill files.

    python tools/mkprompt.py modules/mymod.py                 # print the prompt
    python tools/mkprompt.py modules/mymod.py -o prompt.md    # save it to a file
    python tools/mkprompt.py modules/mymod.py --existing      # ask the AI to revise existing files

Paste the prompt into any AI assistant, save the two files it returns next to your .py, then run
    python tools/modtest.py modules/mymod.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.stdio import utf8_stdio  # noqa: E402

TEMPLATES = ROOT / "templates"
PROMPT = TEMPLATES / "AI_MODULE_PROMPT.md"
EXAMPLE = TEMPLATES / "module" / "template"


def build_prompt(py: Path, include_existing: bool = False) -> str:
    name = py.stem
    existing = ""
    if include_existing:
        parts = []
        for suffix, lang in ((".md", "markdown"), (".skill", "toml")):
            path = py.with_suffix(suffix)
            if path.is_file():
                fence = "````" if suffix == ".md" else "```"
                parts.append(f"\n### Current `{name}{suffix}` (revise it; keep what is correct)\n"
                             f"{fence}{lang}\n{path.read_text(encoding='utf-8').rstrip()}\n{fence}\n")
        existing = "".join(parts)

    replacements = {
        "{{MODULE_NAME}}": name,
        "{{EXAMPLE_PY}}": EXAMPLE.with_suffix(".py").read_text(encoding="utf-8").rstrip(),
        "{{EXAMPLE_MD}}": EXAMPLE.with_suffix(".md").read_text(encoding="utf-8").rstrip(),
        "{{EXAMPLE_SKILL}}": EXAMPLE.with_suffix(".skill").read_text(encoding="utf-8").rstrip(),
        "{{MODULE_SOURCE}}": py.read_text(encoding="utf-8").rstrip(),
        "{{EXISTING_FILES}}": existing,
    }
    text = PROMPT.read_text(encoding="utf-8")
    # The module name last: the other values may legitimately contain the text "{{MODULE_NAME}}".
    for key in [k for k in replacements if k != "{{MODULE_NAME}}"] + ["{{MODULE_NAME}}"]:
        text = text.replace(key, replacements[key])
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mkprompt", description=__doc__.splitlines()[0])
    parser.add_argument("module", type=Path, help="path to the module's .py file")
    parser.add_argument("-o", "--output", type=Path, help="write the prompt here instead of stdout")
    parser.add_argument("--existing", action="store_true",
                        help="include the current .md/.skill so the AI revises them")
    opts = parser.parse_args(argv)

    if opts.module.suffix != ".py" or not opts.module.is_file():
        parser.error(f"{opts.module} is not an existing .py file")
    prompt = build_prompt(opts.module, opts.existing)
    utf8_stdio()  # `mkprompt x.py > prompt.txt` on Windows would otherwise fail on non-cp1252 text
    if opts.output:
        opts.output.write_text(prompt, encoding="utf-8")
        name = opts.module.stem
        print(f"Prompt written to {opts.output} ({len(prompt):,} characters).\n"
              f"Paste it into an AI assistant and save its two answers as {name}.md and {name}.skill\n"
              f"next to {opts.module}, then run:  python tools/modtest.py {opts.module}", file=sys.stderr)
    else:
        sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
