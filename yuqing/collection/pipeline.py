# -*- coding: utf-8 -*-
"""Per-platform fetch -> normalize/filter -> persist -> health pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass


ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class FetchOutcome:
    items: list[dict]
    status: str
    note: str


@dataclass(frozen=True)
class PersistOutcome:
    inserted: int
    valid: int
    filtered_must_not: int
    filtered_no_alias: int
    semantic_rescued: int


def fetch_items(
    *,
    platform: str,
    keyword: str,
    limit: int,
    fixture,
    entry: str,
    user: str | None,
    opencli_sites: Mapping[str, str],
    fetch_opencli: Callable[[str, str, int], list[dict]],
    fetch_userposts: Callable[[str, str, int], list[dict]],
    fetch_heimao: Callable[[str, int], list[dict]],
) -> FetchOutcome:
    """Fetch one source and convert adapter failures into an auditable outcome."""
    try:
        if fixture is not None:
            items = fixture
        elif entry == "user-posts" and user:
            items = fetch_userposts(opencli_sites[platform], user, limit)
        elif platform == "heimao":
            items = fetch_heimao(keyword, limit)
        else:
            items = fetch_opencli(platform, keyword, limit)
        return FetchOutcome(items=items, status="ok", note="")
    except Exception as exc:
        return FetchOutcome(items=[], status="error", note=str(exc)[:200])


def normalize_filter_persist(
    store,
    *,
    items: list[dict],
    run_id: str,
    entity_id: str,
    platform: str,
    keyword: str,
    now: str,
    backend: str,
    entry: str,
    aliases,
    must_not,
    opencli_sites: Mapping[str, str],
    normalize_document: Callable,
    judge_relevance: Callable,
    semantic_setup: Callable,
    semantic_similarity: Callable,
    timestamp_pattern=ISO_TIMESTAMP,
) -> PersistOutcome:
    """Normalize and persist one fetched batch without committing it."""
    require_alias = entry.startswith("search") and bool(aliases) and platform in opencli_sites
    watermark = store.get_watermark(entity_id, platform, entry)
    max_timestamp = watermark or ""
    inserted = valid = filtered_must_not = filtered_no_alias = semantic_rescued = 0
    semantic_on, semantic_threshold, entity_vector = semantic_setup(require_alias, aliases)

    for item in items:
        document = normalize_document(platform, entity_id, item, backend, now)
        if not document.native_id:
            continue
        valid += 1
        store.add_raw(
            document,
            item,
            run_id=run_id,
            entry=entry,
            source_query=keyword,
        )
        verdict = judge_relevance(
            document.text,
            aliases or [],
            must_not,
            require_alias=require_alias,
        )
        if not verdict.relevant and verdict.reason == "no_alias" and semantic_on:
            similarity = semantic_similarity(semantic_on, entity_vector, document.text)
            verdict = judge_relevance(
                document.text,
                aliases or [],
                must_not,
                require_alias=require_alias,
                sem_sim=similarity,
                sem_threshold=semantic_threshold,
            )
        if not verdict.relevant:
            if verdict.reason.startswith("must_not"):
                filtered_must_not += 1
            else:
                filtered_no_alias += 1
            continue
        if verdict.reason.startswith("semantic"):
            semantic_rescued += 1

        timestamp = document.publish_ts if timestamp_pattern.match(document.publish_ts) else ""
        if (
            watermark
            and timestamp
            and timestamp < watermark
            and not store.document_exists(document.doc_id)
        ):
            continue
        if timestamp > max_timestamp:
            max_timestamp = timestamp
        is_new = store.add_clean(document)
        store.add_entity_match(
            document.doc_id,
            entity_id,
            match_reason=verdict.reason,
            source_query=keyword,
            observed_at=now,
        )
        if is_new:
            inserted += 1

    if max_timestamp and max_timestamp != watermark:
        store.set_watermark(entity_id, platform, entry, max_timestamp)
    return PersistOutcome(
        inserted=inserted,
        valid=valid,
        filtered_must_not=filtered_must_not,
        filtered_no_alias=filtered_no_alias,
        semantic_rescued=semantic_rescued,
    )


def finalize_run(
    store,
    *,
    fetched: FetchOutcome,
    persisted: PersistOutcome,
    run_id: str,
    entity_id: str,
    platform: str,
    keyword: str,
    now: str,
    entry: str,
    assess_health: Callable,
) -> str:
    """Assess health, write the run audit row, and preserve the commit boundary."""
    state = assess_health(
        store,
        platform=platform,
        entity_id=entity_id,
        n_fetched=len(fetched.items),
        status=fetched.status,
        entry=entry,
        source_query=keyword,
    )
    note = fetched.note
    if fetched.status == "ok" and fetched.items and persisted.valid == 0:
        state = "suspect"
        note = note or (
            f"字段映射失败：抓到 {len(fetched.items)} 条但 0 条含有效ID"
            "（平台格式可能变了）"
        )
    filtered = persisted.filtered_must_not + persisted.filtered_no_alias
    if filtered:
        suffix = (
            f"过滤 must_not{persisted.filtered_must_not}/"
            f"无别名{persisted.filtered_no_alias}(共{filtered}/{persisted.valid})"
        )
        note = (note + "；" if note else "") + suffix
    if persisted.semantic_rescued:
        suffix = f"语义救回{persisted.semantic_rescued}(不含别名但语义相关)"
        note = (note + "；" if note else "") + suffix
    store.log_run(
        run_id,
        platform,
        entity_id,
        len(fetched.items),
        fetched.status,
        state,
        note,
        now,
        entry=entry,
        source_query=keyword,
    )
    store.commit()
    return state


def collect_platform(
    store,
    *,
    run_id: str,
    entity_id: str,
    platform: str,
    keyword: str,
    now: str,
    limit: int,
    fixture,
    backend: str,
    entry: str,
    user: str | None,
    aliases,
    must_not,
    opencli_sites: Mapping[str, str],
    fetch_opencli: Callable,
    fetch_userposts: Callable,
    fetch_heimao: Callable,
    normalize_document: Callable,
    judge_relevance: Callable,
    semantic_setup: Callable,
    semantic_similarity: Callable,
    assess_health: Callable,
    timestamp_pattern=ISO_TIMESTAMP,
) -> tuple[int, str]:
    """Run one complete platform collection while keeping one commit boundary."""
    fetched = fetch_items(
        platform=platform,
        keyword=keyword,
        limit=limit,
        fixture=fixture,
        entry=entry,
        user=user,
        opencli_sites=opencli_sites,
        fetch_opencli=fetch_opencli,
        fetch_userposts=fetch_userposts,
        fetch_heimao=fetch_heimao,
    )
    persisted = normalize_filter_persist(
        store,
        items=fetched.items,
        run_id=run_id,
        entity_id=entity_id,
        platform=platform,
        keyword=keyword,
        now=now,
        backend=backend,
        entry=entry,
        aliases=aliases,
        must_not=must_not,
        opencli_sites=opencli_sites,
        normalize_document=normalize_document,
        judge_relevance=judge_relevance,
        semantic_setup=semantic_setup,
        semantic_similarity=semantic_similarity,
        timestamp_pattern=timestamp_pattern,
    )
    state = finalize_run(
        store,
        fetched=fetched,
        persisted=persisted,
        run_id=run_id,
        entity_id=entity_id,
        platform=platform,
        keyword=keyword,
        now=now,
        entry=entry,
        assess_health=assess_health,
    )
    return persisted.inserted, state
