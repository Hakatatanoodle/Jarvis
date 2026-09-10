"""
Config loader (§7, §8, INF-27/28/29). YAML in, dict-like access out.
No binary or code-embedded config — everything a human can read and edit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
import os

from infra.logging import get_logger

log = get_logger("config")

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "default.yaml"

# Loaded once at import time — .env sits at repo root, same convention
# docker-compose.yml and setup.sh use.
load_dotenv(Path(__file__).parent.parent / ".env")

_ENV_OVERRIDES = {
    "storage.postgres.host": "POSTGRES_HOST",
    "storage.postgres.port": "POSTGRES_PORT",
    "storage.postgres.user": "POSTGRES_USER",
    "storage.postgres.password": "POSTGRES_PASSWORD",
    "storage.postgres.database": "POSTGRES_DB",
}


class Config:
    """Thin wrapper so callers do config.get("storage.postgres.host") instead
    of hand-rolling nested dict lookups everywhere."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def version(self) -> int:
        return self._data.get("config_version", 0)

    def raw(self) -> dict[str, Any]:
        return self._data


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}

    for dotted_key, env_var in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        node = data
        parts = dotted_key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = int(value) if parts[-1] == "port" else value

    log.info(f"Loaded config v{data.get('config_version')} from {config_path}")
    return Config(data)
