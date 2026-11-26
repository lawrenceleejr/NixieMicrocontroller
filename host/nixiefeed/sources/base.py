"""Source base class and registry.

A source is anything that produces a number: a model ("births today at the
national average rate"), a clock, or a live web feed. Sources are dataclasses;
their fields are their parameters, so they can be configured from the command
line (`-p latitude=51.5`) or from a TOML file without extra glue code.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, ClassVar

USER_AGENT = "nixiefeed/1.0 (frequency-counter display)"


class SourceError(RuntimeError):
    pass


@dataclasses.dataclass
class Source:
    """Base class. Subclasses set `refresh_s` and implement `read`."""

    name: ClassVar[str] = ""
    summary: ClassVar[str] = ""
    refresh_s: ClassVar[float] = 1.0  # how often read() is worth calling
    # Display defaults, used unless the user overrides them.
    default_scale: ClassVar[float] = 1.0
    default_offset: ClassVar[float] = 0.0
    default_overflow: ClassVar[str] = "clip"

    def read(self, now: datetime) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def label(self) -> str:
        return getattr(self, "preset_summary", self.summary)


_REGISTRY: dict[str, Callable[..., Source]] = {}
_SUMMARIES: dict[str, str] = {}
_PARAMS: dict[str, list[dataclasses.Field]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator: make a Source available by its `name`."""
    _REGISTRY[cls.name] = cls
    _SUMMARIES[cls.name] = cls.summary
    _PARAMS[cls.name] = [f for f in dataclasses.fields(cls) if f.init]
    return cls


def register_preset(name: str, summary: str, cls: type[Source], **defaults: Any) -> None:
    """Register `name` as `cls` with some parameters already filled in."""

    def factory(**params: Any) -> Source:
        merged = {**defaults, **params}
        src = cls(**_coerce(cls, merged))
        src.preset_name = name  # type: ignore[attr-defined]
        src.preset_summary = summary  # type: ignore[attr-defined]
        return src

    _REGISTRY[name] = factory
    _SUMMARIES[name] = summary
    _PARAMS[name] = [f for f in dataclasses.fields(cls) if f.init and f.name not in defaults]


def available() -> dict[str, str]:
    return dict(sorted(_SUMMARIES.items()))


def params_of(name: str) -> list[dataclasses.Field]:
    return _PARAMS[name]


def _coerce(cls: Callable[..., Source], params: dict[str, Any]) -> dict[str, Any]:
    """Convert string parameters (from the command line) to the field types."""
    if not dataclasses.is_dataclass(cls):
        return params
    types = {f.name: f.type for f in dataclasses.fields(cls)}
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key not in types:
            raise SourceError(f"unknown parameter {key!r}; known: {', '.join(sorted(types))}")
        t = types[key]
        t = t if isinstance(t, str) else getattr(t, "__name__", str(t))
        if isinstance(value, str):
            if "bool" in t:
                value = value.strip().lower() in ("1", "true", "yes", "on")
            elif "float" in t:
                value = float(value)
            elif "int" in t:
                value = int(value)
        out[key] = value
    return out


def make(name: str, **params: Any) -> Source:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise SourceError(f"no source called {name!r}; try `nixiefeed list`") from None
    if isinstance(factory, type):
        return factory(**_coerce(factory, params))
    return factory(**params)


def fetch_json(url: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    """GET a URL and decode JSON. Raises SourceError on any failure."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network, HTTP and JSON errors all mean "no reading"
        raise SourceError(f"{url}: {e}") from e


def dig(data: Any, path: str) -> Any:
    """Follow a dotted path like 'features.0.properties.mag' into JSON data.

    Negative list indices count from the end ('values.-1').
    """
    cur = data
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError) as e:
                raise SourceError(f"bad list index {part!r} in {path!r}") from e
        elif isinstance(cur, dict):
            if part not in cur:
                raise SourceError(f"no key {part!r} in {path!r}")
            cur = cur[part]
        else:
            raise SourceError(f"cannot descend into {type(cur).__name__} at {part!r}")
    return cur
