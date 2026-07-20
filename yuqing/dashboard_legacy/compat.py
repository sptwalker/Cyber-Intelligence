# -*- coding: utf-8 -*-
"""Compatibility-aware access to watch configuration boundaries."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from ..watch_config import load_watch as _boundary_load_watch
from ..watch_config import watch_path as _boundary_watch_path


def _package_override(name: str, boundary: Callable[..., Any]) -> Callable[..., Any]:
    """Honor historic ``yuqing.<name>`` monkeypatches without owning the API."""
    package = sys.modules.get("yuqing")
    candidate = getattr(package, name, boundary) if package is not None else boundary
    return candidate if callable(candidate) else boundary


def load_watch() -> dict:
    return _package_override("load_watch", _boundary_load_watch)()


def watch_path() -> str:
    return _package_override("watch_path", _boundary_watch_path)()
