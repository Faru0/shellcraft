"""Discover and load tools from ./modules/<name>.{py,md,skill}."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

VALID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
RESERVED_NAMES = {"shellcraft_pipeline"}

RunFn = Callable[[list[str], str], str]


@dataclass
class SkillInfo:
    """Structured AI-facing guidance parsed from <name>.skill (TOML)."""

    raw: str
    summary: str = ""
    when_to_use: str = ""
    usage: str = ""
    args: list[dict[str, str]] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    # [[tests]] cases for tools/modtest.py; never part of the MCP description.
    tests: list[dict[str, Any]] = field(default_factory=list)
    parsed: bool = True

    @classmethod
    def parse(cls, text: str) -> SkillInfo:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            # Free-form .skill files are still useful: pass them through verbatim.
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            return cls(raw=text, summary=first, parsed=False)

        args = []
        for item in data.get("args", []):
            if isinstance(item, dict):
                args.append({"name": str(item.get("name", "")), "description": str(item.get("description", ""))})
            else:
                args.append({"name": str(item), "description": ""})
        known = {"summary", "when_to_use", "usage", "args", "examples", "notes", "tests"}
        return cls(
            raw=text,
            summary=str(data.get("summary", "")).strip(),
            when_to_use=str(data.get("when_to_use", "")).strip(),
            usage=str(data.get("usage", "")).strip(),
            args=args,
            examples=[str(e) for e in data.get("examples", [])],
            notes=str(data.get("notes", "")).strip(),
            extra={k: str(v).strip() for k, v in data.items() if k not in known},
            tests=[t for t in data.get("tests", []) if isinstance(t, dict)],
        )

    def to_description(self) -> str:
        """Render as the plain-text tool description handed to MCP clients."""
        if not self.parsed:
            return self.raw.strip()
        parts = [self.summary] if self.summary else []
        if self.when_to_use:
            parts.append(f"When to use:\n{self.when_to_use}")
        if self.usage:
            parts.append(f"Usage: {self.usage}")
        if self.args:
            lines = [f"  {a['name']}: {a['description']}" if a["description"] else f"  {a['name']}" for a in self.args]
            parts.append("Arguments (pass in `args` as CLI-style strings):\n" + "\n".join(lines))
        if self.examples:
            parts.append("Examples:\n" + "\n".join(f"  {e}" for e in self.examples))
        if self.notes:
            parts.append(f"Notes:\n{self.notes}")
        for key, value in self.extra.items():
            parts.append(f"{key.replace('_', ' ').capitalize()}:\n{value}")
        return "\n\n".join(parts)


@dataclass
class ModuleSpec:
    name: str
    path: Path
    run: RunFn
    doc_md: str | None = None
    skill: SkillInfo | None = None
    spinner_text: str | None = None
    module_summary: str | None = None

    @property
    def summary(self) -> str:
        if self.skill and self.skill.summary:
            return self.skill.summary
        if self.module_summary:
            return self.module_summary
        if self.doc_md:
            for line in self.doc_md.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
        return ""

    @property
    def description(self) -> str:
        if self.skill:
            return self.skill.to_description()
        return self.summary or f"ShellCraft module `{self.name}`."


class ModuleRegistry:
    """Loads every valid module in a directory; broken modules become warnings, not crashes."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.modules: dict[str, ModuleSpec] = {}
        self.warnings: list[str] = []

    def load(self) -> None:
        self.modules = {}
        self.warnings = []
        if not self.directory.is_dir():
            self.warnings.append(f"modules directory not found: {self.directory}")
            return
        for py in sorted(self.directory.glob("*.py")):
            name = py.stem
            if name.startswith("_"):
                continue
            if not VALID_NAME.match(name) or name in RESERVED_NAMES:
                self.warnings.append(f"{py.name}: invalid module name, skipped")
                continue
            try:
                self.modules[name] = self.load_file(name, py)
            except Exception as exc:  # noqa: BLE001 — one bad module must not stop the shell
                self.warnings.append(f"{py.name}: {type(exc).__name__}: {exc}")

    def load_file(self, name: str, py: Path) -> ModuleSpec:
        """Import one module file (as the shell does) and return its spec; raises on failure."""
        import_name = f"shellcraft_modules.{name}"
        spec = importlib.util.spec_from_file_location(import_name, py)
        if spec is None or spec.loader is None:
            raise ImportError("cannot create import spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[import_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(import_name, None)
            raise
        run = getattr(module, "run", None)
        if not callable(run):
            raise AttributeError("missing callable run(args, stdin)")

        md = py.with_suffix(".md")
        skill = py.with_suffix(".skill")
        return ModuleSpec(
            name=name,
            path=py,
            run=run,
            doc_md=_read(md),
            skill=SkillInfo.parse(text) if (text := _read(skill)) is not None else None,
            spinner_text=getattr(module, "SPINNER_TEXT", None),
            module_summary=getattr(module, "SUMMARY", None) or _first_doc_line(module),
        )

    def get(self, name: str) -> ModuleSpec | None:
        return self.modules.get(name)

    def names(self) -> list[str]:
        return sorted(self.modules)

    def __len__(self) -> int:
        return len(self.modules)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _first_doc_line(module: Any) -> str | None:
    doc = (module.__doc__ or "").strip()
    return doc.splitlines()[0] if doc else None
