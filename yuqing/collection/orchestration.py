# -*- coding: utf-8 -*-
"""Watch-level collection orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping


def entity_aliases(entity: dict) -> list[str]:
    """Return stable, case-insensitively de-duplicated search aliases."""
    aliases: list[str] = []
    seen: set[str] = set()
    for alias in entity.get("aliases") or [entity["id"]]:
        key = (alias or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            aliases.append(alias.strip())
    return aliases or [entity["id"]]


def collect_all(
    store,
    watch: dict,
    *,
    run_id: str,
    now: str,
    fixtures,
    on_progress,
    should_stop,
    collect_one: Callable,
    worst_health: Callable[[str | None, str], str],
    opencli_sites: Mapping[str, str],
) -> dict[str, str]:
    """Collect all configured entity/platform sources in their legacy order."""
    fixtures = fixtures or {}
    health_by_platform: dict[str, str] = {}
    for entity in watch["entities"]:
        entity_id = entity["id"]
        aliases = entity_aliases(entity)
        keyword = aliases[0]
        must_not = entity.get("must_not", [])
        for platform in watch["platforms"]:
            if should_stop and should_stop():
                return health_by_platform
            fixture = (fixtures.get(platform) or {}).get(entity_id) if fixtures else None
            queries = [keyword] if fixture is not None else aliases
            for query in queries:
                if should_stop and should_stop():
                    return health_by_platform
                if on_progress:
                    on_progress(entity_id, platform)
                query_fixture = fixture.get(query) if isinstance(fixture, dict) else fixture
                _, state = collect_one(
                    store,
                    run_id=run_id,
                    entity_id=entity_id,
                    platform=platform,
                    keyword=query,
                    now=now,
                    fixture=query_fixture,
                    entry=f"search:{query}",
                    aliases=aliases,
                    must_not=must_not,
                )
                health_by_platform[platform] = worst_health(
                    health_by_platform.get(platform), state
                )
        if should_stop and should_stop():
            return health_by_platform
        for spec in entity.get("track_users", []):
            site, _, user = spec.partition(":")
            if site in opencli_sites and user:
                collect_one(
                    store,
                    run_id=run_id,
                    entity_id=entity_id,
                    platform=site,
                    keyword=keyword,
                    now=now,
                    entry="user-posts",
                    user=user,
                    aliases=aliases,
                    must_not=must_not,
                )
    return health_by_platform
