# -*- coding: utf-8 -*-
"""Compatibility checks for the Store repository split."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from yuqing.storage.documents import DocumentRepository
from yuqing.storage.operations import OperationsRepository
from yuqing.storage.reviews import ReviewRepository
from yuqing.storage.schema import SchemaRepository
from yuqing.store import CleanDoc, Store, content_cluster_id, doc_id_for


PUBLIC_METHODS = {
    "account_type",
    "add_account",
    "add_annotation",
    "add_clean",
    "add_entity_match",
    "add_feature",
    "add_raw",
    "add_review",
    "add_usage",
    "annotated_count",
    "annotation_candidates",
    "clean_missing_embedding",
    "clean_missing_features",
    "close",
    "commit",
    "create_incident",
    "delete_account",
    "document_exists",
    "embeddings_for",
    "entities_for_doc",
    "get_embedding",
    "get_heartbeat",
    "get_incident",
    "get_watermark",
    "joined",
    "joined_with_entities",
    "latest_annotation",
    "list_accounts",
    "list_incidents",
    "load_annotations",
    "log_run",
    "pending_review_count",
    "platform_baseline",
    "recent_alert",
    "record_alert",
    "record_engagement",
    "record_heartbeat",
    "review_queue",
    "review_stats",
    "save_report",
    "schema_version",
    "set_embedding",
    "set_watermark",
    "transition_incident",
    "usage_today",
}


class StoreBoundaryTest(unittest.TestCase):
    def test_store_composes_bounded_context_repositories(self) -> None:
        self.assertTrue(issubclass(Store, DocumentRepository))
        self.assertTrue(issubclass(Store, OperationsRepository))
        self.assertTrue(issubclass(Store, ReviewRepository))
        self.assertTrue(issubclass(Store, SchemaRepository))
        self.assertEqual("yuqing.storage.documents", Store.add_clean.__module__)
        self.assertEqual("yuqing.storage.operations", Store.create_incident.__module__)
        self.assertEqual("yuqing.storage.reviews", Store.add_review.__module__)

    def test_legacy_store_exports_and_public_methods_remain_available(self) -> None:
        doc = CleanDoc.build(
            platform="weibo", native_id="native-1", entity_id="entity-1", text="测试"
        )
        self.assertEqual(doc_id_for("weibo", "native-1"), doc.doc_id)
        self.assertEqual(content_cluster_id("测试"), doc.content_cluster)
        self.assertTrue(PUBLIC_METHODS <= set(dir(Store)))

        add_review = inspect.signature(Store.add_review).parameters
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, add_review["commit"].kind)
        self.assertTrue(add_review["commit"].default)
        pending = inspect.signature(Store.pending_review_count).parameters
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, pending["entity_id"].kind)

    def test_explicit_commit_boundary_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "store.db"
            writer = Store(path)
            reader = Store(path)
            try:
                doc = CleanDoc.build(
                    platform="weibo",
                    native_id="native-2",
                    entity_id="entity-1",
                    text="尚未提交",
                    fetched_at="2026-07-20T00:00:00+08:00",
                )
                writer.add_clean(doc)
                self.assertFalse(reader.document_exists(doc.doc_id))
                writer.commit()
                self.assertTrue(reader.document_exists(doc.doc_id))

                writer.add_review(doc.doc_id, "ok", commit=False)
                self.assertEqual(
                    0, reader.conn.execute("SELECT COUNT(*) FROM review").fetchone()[0]
                )
                writer.commit()
                self.assertEqual(
                    1, reader.conn.execute("SELECT COUNT(*) FROM review").fetchone()[0]
                )
            finally:
                reader.close()
                writer.close()


if __name__ == "__main__":
    unittest.main()
