# -*- coding: utf-8 -*-
"""Backward-compatible SQLite store facade.

The public API remains here while bounded-context repositories live under
``yuqing.storage``.  ``Store`` owns only connection lifecycle and repository
assembly; transaction boundaries remain defined by the existing public methods.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .storage.documents import (
    CleanDoc,
    DocumentRepository,
    content_cluster_id,
    doc_id_for,
)
from .storage.operations import OperationsRepository
from .storage.reviews import ReviewRepository
from .storage.schema import SCHEMA as _SCHEMA
from .storage.schema import SCHEMA_VERSION, SchemaRepository, initialize_schema

__all__ = [
    "CleanDoc",
    "SCHEMA_VERSION",
    "Store",
    "content_cluster_id",
    "doc_id_for",
]


class Store(
    SchemaRepository,
    DocumentRepository,
    OperationsRepository,
    ReviewRepository,
):
    """Compose domain repositories around one SQLite connection."""

    def __init__(self, path: str | Path = "yuqing.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # Wait for concurrent dashboard/batch writes instead of failing immediately.
        self.conn.execute("PRAGMA busy_timeout=15000")
        # WAL permits concurrent readers while retaining SQLite's single-writer model.
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        initialize_schema(self.conn)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
