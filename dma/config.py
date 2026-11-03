from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .version import UPDATE_MANIFEST_URL

PROVIDERS = {
    "pollinations": "免费公共通道",
    "openai": "OpenAI 兼容接口",
    "ollama": "本地 Ollama",
}


@dataclass
class AIConfig:
    provider: str = "pollinations"
    base_url: str = "https://api.openai.com/v1"
    model: str = "openai-fast"
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 8192
    timeout: int = 180

    def normalized(self) -> "AIConfig":
        provider = self.provider if self.provider in PROVIDERS else "openai"
        model = self.model.strip()
        if provider == "pollinations":
            model = model or "openai-fast"
        elif provider == "ollama":
            model = model or "qwen2.5-coder:7b"
        else:
            model = model or "gpt-4o-mini"
        return AIConfig(
            provider=provider,
            base_url=(self.base_url or "").strip(),
            model=model,
            api_key=(self.api_key or "").strip(),
            temperature=max(0.0, min(1.5, float(self.temperature or 0.2))),
            max_tokens=max(512, min(32768, int(self.max_tokens or 8192))),
            timeout=max(30, min(600, int(self.timeout or 180))),
        )


@dataclass
class Config:
    ai: AIConfig = field(default_factory=AIConfig)
    project_root: str = ""
    last_project: str = ""
    auto_apply: bool = False
    max_steps: int = 8
    context_turns: int = 12
    shortcut_offered: bool = False
    update_url: str = UPDATE_MANIFEST_URL
    window_width: int = 1320
    window_height: int = 860
    font_size: int = 14

    def normalized(self) -> "Config":
        root = self.project_root.strip()
        if not root:
            root = str(paths.default_project_root())
        else:
            try:
                candidate = Path(root).expanduser().resolve(strict=False)
                legacy = {
                    item.expanduser().resolve(strict=False)
                    for item in paths.legacy_project_roots()
                }
                if candidate in legacy:
                    root = str(paths.default_project_root())
            except OSError:
                pass
        return Config(
            ai=self.ai.normalized(),
            project_root=root,
            last_project=self.last_project.strip(),
            auto_apply=bool(self.auto_apply),
            max_steps=max(1, min(20, int(self.max_steps or 8))),
            context_turns=max(2, min(40, int(self.context_turns or 12))),
            shortcut_offered=bool(self.shortcut_offered),
            update_url=(self.update_url or UPDATE_MANIFEST_URL).strip(),
            window_width=max(980, int(self.window_width or 1320)),
            window_height=max(680, int(self.window_height or 860)),
            font_size=max(11, min(20, int(self.font_size or 14))),
        )


def _from_dict(raw: dict[str, Any]) -> Config:
    ai_raw = raw.get("ai") if isinstance(raw.get("ai"), dict) else {}
    ai_fields = {item.name for item in AIConfig.__dataclass_fields__.values()}
    ai = AIConfig(**{k: v for k, v in ai_raw.items() if k in ai_fields})
    fields = {item.name for item in Config.__dataclass_fields__.values()}
    payload = {k: v for k, v in raw.items() if k in fields and k != "ai"}
    return Config(ai=ai, **payload)


def load_config(path: Path | None = None) -> Config:
    target = Path(path or paths.config_path())
    if not target.is_file():
        return Config().normalized()
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return Config().normalized()
    if not isinstance(raw, dict):
        return Config().normalized()
    try:
        return _from_dict(raw).normalized()
    except Exception:
        return Config().normalized()


def save_config(config: Config, path: Path | None = None) -> Path:
    target = Path(path or paths.config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config.normalized())
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(target.parent),
        prefix=".config-",
        suffix=".tmp",
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(handle.name, target)
    finally:
        try:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
        except OSError:
            pass
    return target
