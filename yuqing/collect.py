# -*- coding: utf-8 -*-
"""Backward-compatible facade for the collection pipeline.

The implementation is split under :mod:`yuqing.collection` into external
fetchers, semantic relevance, per-platform persistence/health, and watch-level
orchestration.  This module deliberately keeps the historic public and private
entry points because the collector sidecar, login helper, and existing
integrations import or monkeypatch them.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from . import health, relevance
from .collection import fetchers as _fetchers
from .collection import orchestration as _orchestration
from .collection import pipeline as _pipeline
from .collection import semantic as _semantic
from .normalization import normalize
from .store import Store


OPENCLI_SITE = _fetchers.OPENCLI_SITE
_OPENCLI = _fetchers.OPENCLI_BINARY
_HEIMAO_LINK = _fetchers.HEIMAO_LINK
_ISO_TS = _pipeline.ISO_TIMESTAMP
_SEM_THRESHOLD = _semantic.DEFAULT_THRESHOLD


def _parse_opencli_json(stdout: str, returncode: int, site: str, limit: int) -> list[dict]:
    """Compatibility wrapper for the extracted opencli parser."""
    return _fetchers.parse_opencli_json(stdout, returncode, site, limit)


def _fetch_opencli(platform: str, keyword: str, limit: int) -> list[dict]:
    """Compatibility wrapper preserving ``yuqing.collect`` monkeypatch points."""
    return _fetchers.fetch_opencli(
        platform,
        keyword,
        limit,
        opencli=_OPENCLI,
        opencli_sites=OPENCLI_SITE,
        run_command=subprocess.run,
        parse_json=_parse_opencli_json,
    )


def _fetch_opencli_userposts(site: str, user: str, limit: int) -> list[dict]:
    """Compatibility wrapper for tracked-account collection."""
    return _fetchers.fetch_opencli_userposts(
        site,
        user,
        limit,
        opencli=_OPENCLI,
        run_command=subprocess.run,
    )


def parse_heimao_markdown(md: str) -> list[dict]:
    """Compatibility wrapper for Heimao markdown parsing."""
    return _fetchers.parse_heimao_markdown(md, link_pattern=_HEIMAO_LINK)


def _opencli_browser(session: str, *args: str, timeout: int = 60) -> str:
    """Compatibility wrapper for the authenticated browser bridge."""
    return _fetchers.opencli_browser(
        session,
        *args,
        timeout=timeout,
        opencli=_OPENCLI,
        run_command=subprocess.run,
    )


def _heimao_is_login_wall(md: str) -> bool:
    """Compatibility wrapper for the Heimao login-state detector."""
    return _fetchers.heimao_is_login_wall(md)


def _fetch_heimao(keyword: str, limit: int, *, pages: int = 1) -> list[dict]:
    """Compatibility wrapper retaining patchable browser/parser dependencies."""
    return _fetchers.fetch_heimao(
        keyword,
        limit,
        pages=pages,
        session=os.getenv("YUQING_OPENCLI_SESSION", "yuqing"),
        browser_call=_opencli_browser,
        parse_markdown=parse_heimao_markdown,
        login_wall=_heimao_is_login_wall,
        sleep=time.sleep,
        link_pattern=_HEIMAO_LINK,
    )


def _semantic_setup(require: bool, aliases):
    """Compatibility wrapper for optional semantic relevance setup."""
    return _semantic.setup(require, aliases, default_threshold=_SEM_THRESHOLD)


def _semantic_sim(sem_on: bool, ent_vec, text: str):
    """Compatibility wrapper for one semantic relevance comparison."""
    return _semantic.similarity(sem_on, ent_vec, text)


def collect_platform(
    store: Store,
    *,
    run_id: str,
    entity_id: str,
    platform: str,
    keyword: str,
    now: str,
    limit: int = 50,
    fixture: Optional[list[dict]] = None,
    backend: str = "opencli",
    entry: str = "search",
    user: Optional[str] = None,
    aliases: Optional[list] = None,
    must_not: Optional[list] = None,
) -> tuple[int, str]:
    """Collect one entity/platform pair and return ``(inserted, health_state)``."""
    return _pipeline.collect_platform(
        store,
        run_id=run_id,
        entity_id=entity_id,
        platform=platform,
        keyword=keyword,
        now=now,
        limit=limit,
        fixture=fixture,
        backend=backend,
        entry=entry,
        user=user,
        aliases=aliases,
        must_not=must_not,
        opencli_sites=OPENCLI_SITE,
        fetch_opencli=_fetch_opencli,
        fetch_userposts=_fetch_opencli_userposts,
        fetch_heimao=_fetch_heimao,
        normalize_document=normalize,
        judge_relevance=relevance.judge,
        semantic_setup=_semantic_setup,
        semantic_similarity=_semantic_sim,
        assess_health=health.assess,
        timestamp_pattern=_ISO_TS,
    )


def collect_all(
    store: Store,
    watch: dict,
    *,
    run_id: str,
    now: str,
    fixtures: Optional[dict] = None,
    on_progress=None,
    should_stop=None,
) -> dict[str, str]:
    """Collect all configured entities/platforms in the historic execution order."""
    return _orchestration.collect_all(
        store,
        watch,
        run_id=run_id,
        now=now,
        fixtures=fixtures,
        on_progress=on_progress,
        should_stop=should_stop,
        collect_one=collect_platform,
        worst_health=health.worst,
        opencli_sites=OPENCLI_SITE,
    )


if __name__ == "__main__":
    # Keep the historical ``python -m yuqing.collect`` smoke-test entry point
    # useful while the implementation itself remains free of production tests.
    from .selfcheck import demo

    demo()
