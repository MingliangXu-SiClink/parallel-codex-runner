from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


FALLBACK_REASONING_EFFORTS: tuple[str, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)


@dataclass(frozen=True)
class CodexModelInfo:
    slug: str
    default_effort: str | None
    supported_efforts: tuple[str, ...]
    visible: bool = True


@dataclass(frozen=True)
class CodexModelRegistry:
    models: dict[str, CodexModelInfo]
    configured_model: str | None = None
    configured_effort: str | None = None

    @classmethod
    def load(cls, codex_home: Path) -> "CodexModelRegistry":
        codex_home = codex_home.expanduser()
        models = _load_model_info(codex_home / "models_cache.json")
        configured_model, configured_effort = _load_config_defaults(
            codex_home / "config.toml"
        )
        return cls(
            models=models,
            configured_model=configured_model,
            configured_effort=configured_effort,
        )

    def effective_model(self, model: str | None) -> str | None:
        return _normalized_text(model) or self.configured_model

    def model_info(self, model: str | None) -> CodexModelInfo | None:
        effective_model = self.effective_model(model)
        return self.models.get(effective_model) if effective_model else None

    def model_options(self, current_model: str | None) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [("default", "")]
        seen = {""}
        for info in self.models.values():
            if info.visible and info.slug not in seen:
                options.append((info.slug, info.slug))
                seen.add(info.slug)
        current = _normalized_text(current_model)
        if current and current not in seen:
            options.append((current, current))
        return options

    def supported_efforts(self, model: str | None) -> tuple[str, ...]:
        info = self.model_info(model)
        if info is not None and info.supported_efforts:
            return info.supported_efforts

        observed = {
            effort
            for candidate in self.models.values()
            for effort in candidate.supported_efforts
        }
        if not observed:
            return FALLBACK_REASONING_EFFORTS
        ordered = [effort for effort in FALLBACK_REASONING_EFFORTS if effort in observed]
        ordered.extend(sorted(observed.difference(ordered)))
        return tuple(ordered)

    def validate_effort(self, model: str | None, effort: str | None) -> None:
        requested = _normalized_effort(effort)
        if not requested:
            return
        info = self.model_info(model)
        if (
            info is not None
            and info.supported_efforts
            and requested not in info.supported_efforts
        ):
            allowed = ", ".join(info.supported_efforts)
            raise ValueError(
                f"effort {requested!r} is not supported by model {info.slug!r}; "
                f"choose one of: {allowed}"
            )

    def resolve_effort(self, model: str | None, effort: str | None) -> str | None:
        requested = _normalized_effort(effort)
        if requested:
            self.validate_effort(model, requested)
            return requested

        info = self.model_info(model)
        if info is not None and info.supported_efforts:
            if self.configured_effort in info.supported_efforts:
                return self.configured_effort
            if info.default_effort in info.supported_efforts:
                return info.default_effort
            return info.supported_efforts[0]
        return self.configured_effort

    def effort_options(
        self,
        model: str | None,
        current_effort: str | None,
    ) -> list[tuple[str, str]]:
        resolved = self.resolve_effort(model, None)
        auto_label = f"auto ({resolved})" if resolved else "auto"
        options: list[tuple[str, str]] = [(auto_label, "")]
        seen = {""}
        for effort in self.supported_efforts(model):
            if effort not in seen:
                options.append((effort, effort))
                seen.add(effort)
        current = _normalized_effort(current_effort)
        if current and current not in seen:
            options.append((current, current))
        return options

    def effort_display(self, model: str | None, effort: str | None) -> str:
        requested = _normalized_effort(effort)
        if requested:
            return requested
        resolved = self.resolve_effort(model, None)
        return f"auto ({resolved})" if resolved else "auto"

    def effort_is_supported(self, model: str | None, effort: str | None) -> bool:
        try:
            self.validate_effort(model, effort)
        except ValueError:
            return False
        return True


def _normalized_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalized_effort(value: Any) -> str | None:
    normalized = _normalized_text(value)
    return normalized.lower() if normalized else None


def _load_model_info(path: Path) -> dict[str, CodexModelInfo]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return {}

    models: dict[str, CodexModelInfo] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        slug = str(record.get("slug") or "").strip()
        if not slug:
            continue
        efforts: list[str] = []
        levels = record.get("supported_reasoning_levels")
        if not isinstance(levels, list):
            levels = record.get("supported_reasoning_efforts")
        if isinstance(levels, list):
            for level in levels:
                raw_effort = level.get("effort") if isinstance(level, dict) else level
                effort = _normalized_effort(raw_effort)
                if effort and effort not in efforts:
                    efforts.append(effort)
        models[slug] = CodexModelInfo(
            slug=slug,
            default_effort=_normalized_effort(
                record.get("default_reasoning_level")
                or record.get("default_reasoning_effort")
            ),
            supported_efforts=tuple(efforts),
            visible=record.get("visibility") != "hide",
        )
    return models


def _load_config_defaults(path: Path) -> tuple[str | None, str | None]:
    try:
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return (
        _normalized_text(payload.get("model")),
        _normalized_effort(payload.get("model_reasoning_effort")),
    )
