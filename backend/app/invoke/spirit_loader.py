from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SPIRIT_NAMES = ["faithful", "rebel", "stranger", "lunatic", "oracle"]
_SPIRITS_DIR = Path(__file__).parent / "spirits"

_cache: dict[str, dict] = {}


def load_spirit(name: str) -> dict:
    if name in _cache:
        return _cache[name]
    path = _SPIRITS_DIR / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _cache[name] = data
    return data


def list_spirits() -> list[str]:
    return SPIRIT_NAMES


def preload_all() -> None:
    for name in SPIRIT_NAMES:
        try:
            load_spirit(name)
        except Exception as e:
            logger.error("Failed to load spirit '%s': %s", name, e)
            raise
