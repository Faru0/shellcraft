"""Discover and load tools from ./modules/<name>.{py,md,skill}."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Callable, Iterable

from core import params as params_mod
from core.modkit import EnvSetting
from core.options import OptionSpec, from_markdown, from_skill, merge
from core.settings import ENV_NAME

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
    # [[params]]: named, typed parameters that become the MCP input schema (see core/params.py).
    params: list[dict[str, Any]] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    # [[tests]] cases for `tools/ingest check`; never part of the MCP description.
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
                values = item.get("values")
                args.append({"name": str(item.get("name", "")), "description": str(item.get("description", "")),
                             "values": [str(v) for v in values] if isinstance(values, list) else []})
            else:
                args.append({"name": str(item), "description": "", "values": []})
        params = [p for p in data.get("params", []) if isinstance(p, dict)]
        if params and not args:
            args = params_mod.as_args(params)  # Tab completion and docs read [[args]]
        known = {"summary", "when_to_use", "usage", "args", "params", "examples", "notes", "tests"}
        return cls(
            raw=text,
            summary=str(data.get("summary", "")).strip(),
            when_to_use=str(data.get("when_to_use", "")).strip(),
            usage=str(data.get("usage", "")).strip(),
            args=args,
            params=params,
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
        if self.args and not self.params:  # with params, the input schema describes each one
            lines = []
            for a in self.args:
                line = f"  {a['name']}: {a['description']}" if a["description"] else f"  {a['name']}"
                if a.get("values"):
                    line += f" (values: {', '.join(a['values'])})"
                lines.append(line)
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
    env_settings: tuple[EnvSetting, ...] = ()  # API keys etc. set with `settings NAME`

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

    @cached_property
    def options(self) -> list[OptionSpec]:
        """Switches for Tab completion: .skill [[args]] merged with the .md Options table."""
        return merge(from_skill(self.skill), from_markdown(self.doc_md))

    @property
    def input_schema(self) -> dict[str, Any] | None:
        """The MCP schema built from .skill [[params]], or None for the raw `args` array."""
        if self.skill and self.skill.params:
            return params_mod.input_schema(self.skill.params)
        return None

    def argv(self, values: dict[str, Any]) -> list[str]:
        """CLI args for a call with named values (raises params.ParamError)."""
        return params_mod.to_argv(self.skill.params if self.skill else [], values)

    @property
    def description(self) -> str:
        if self.skill:
            return self.skill.to_description()
        return self.summary or f"ShellCraft module `{self.name}`."


class ModuleRegistry:
    """Loads every valid, enabled module in a directory; broken modules become warnings, not crashes.

    A disabled module (`modules disable NAME`, saved in config.json) is never imported: it isn't
    a command, an MCP tool or a man page until it is enabled again. `files` still lists it.
    """

    def __init__(self, directory: Path, disabled: Iterable[str] = ()):
        self.directory = Path(directory)
        self.modules: dict[str, ModuleSpec] = {}  # loaded and enabled
        self.files: dict[str, Path] = {}  # every module file with a valid name, enabled or not
        self.disabled: set[str] = set(disabled)
        self.warnings: list[str] = []

    def load(self) -> None:
        for stale in [k for k in sys.modules if k.startswith("shellcraft_modules.")]:
            del sys.modules[stale]  # a deleted or renamed module must not linger after reload
        # Built aside and swapped in at the end, so a reload never shows a half-empty registry.
        modules: dict[str, ModuleSpec] = {}
        files: dict[str, Path] = {}
        warnings: list[str] = []
        if not self.directory.is_dir():
            warnings.append(f"modules directory not found: {self.directory}")
        else:
            for py in sorted(self.directory.glob("*.py")):
                name = py.stem
                if name.startswith("_"):
                    continue
                if not VALID_NAME.match(name) or name in RESERVED_NAMES:
                    warnings.append(f"{py.name}: invalid module name, skipped")
                    continue
                files[name] = py
                if name in self.disabled:
                    continue
                try:
                    modules[name] = self.load_file(name, py)
                except Exception as exc:  # noqa: BLE001 — one bad module must not stop the shell
                    warnings.append(f"{py.name}: {type(exc).__name__}: {exc}")
        self.modules, self.files, self.warnings = modules, files, warnings

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
        skill = SkillInfo.parse(text) if (text := _read(py.with_suffix(".skill"))) is not None else None
        if skill and (errors := params_mod.problems(skill.params)):
            raise ValueError(f"{name}.skill [[params]]: {'; '.join(errors)}")
        return ModuleSpec(
            name=name,
            path=py,
            run=run,
            doc_md=_read(md),
            skill=skill,
            spinner_text=getattr(module, "SPINNER_TEXT", None),
            module_summary=getattr(module, "SUMMARY", None) or _first_doc_line(module),
            env_settings=_env_settings(module),
        )

    def enable(self, name: str) -> None:
        """Enable a module and import it now; raises KeyError (no such module) or the import error
        (the module is then enabled but not loaded, with a warning, as at startup)."""
        if name not in self.files:
            raise KeyError(name)
        self.disabled.discard(name)
        if name in self.modules:
            return
        self.warnings = [w for w in self.warnings if not w.startswith(f"{self.files[name].name}:")]
        try:
            self.modules[name] = self.load_file(name, self.files[name])
        except Exception as exc:
            self.warnings.append(f"{self.files[name].name}: {type(exc).__name__}: {exc}")
            raise

    def disable(self, name: str) -> None:
        """Disable a module: it stops being a command right away. Raises KeyError for no such module."""
        if name not in self.files:
            raise KeyError(name)
        self.disabled.add(name)
        self.modules.pop(name, None)
        sys.modules.pop(f"shellcraft_modules.{name}", None)
        self.warnings = [w for w in self.warnings if not w.startswith(f"{self.files[name].name}:")]

    def is_disabled(self, name: str) -> bool:
        return name in self.disabled and name in self.files

    def disabled_names(self) -> list[str]:
        return sorted(n for n in self.files if n in self.disabled)

    def all_names(self) -> list[str]:
        return sorted(self.files)

    def describe(self, name: str) -> str:
        """A module's summary without importing it (for disabled modules): .skill, then .md."""
        if (spec := self.modules.get(name)) is not None:
            return spec.summary
        py = self.files.get(name)
        if py is None:
            return ""
        if (text := _read(py.with_suffix(".skill"))) is not None and (summary := SkillInfo.parse(text).summary):
            return summary
        for line in (_read(py.with_suffix(".md")) or "").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                return line.strip()
        return ""

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


def _env_settings(module: Any) -> tuple[EnvSetting, ...]:
    """Validate ENV_SETTINGS; a malformed declaration stops the module from loading."""
    declared = getattr(module, "ENV_SETTINGS", None)
    if declared is None:
        return ()
    if not isinstance(declared, (list, tuple)):
        raise TypeError("ENV_SETTINGS must be a list of core.modkit.EnvSetting")
    seen: set[str] = set()
    for item in declared:
        if not isinstance(item, EnvSetting):
            raise TypeError(f"ENV_SETTINGS entries must be core.modkit.EnvSetting, got {type(item).__name__}")
        if not isinstance(item.name, str) or not ENV_NAME.match(item.name):
            raise ValueError(f"ENV_SETTINGS name {item.name!r} must be an UPPER_CASE environment variable name")
        if not (isinstance(item.label, str) and item.label.strip()
                and isinstance(item.description, str) and item.description.strip()):
            raise ValueError(f"ENV_SETTINGS {item.name}: label and description must be non-empty strings")
        if item.name in seen:
            raise ValueError(f"ENV_SETTINGS {item.name} is declared twice")
        seen.add(item.name)
    return tuple(declared)


def _first_doc_line(module: Any) -> str | None:
    doc = (module.__doc__ or "").strip()
    return doc.splitlines()[0] if doc else None
