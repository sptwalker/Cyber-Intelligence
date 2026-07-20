# -*- coding: utf-8 -*-
"""Regression coverage for embedding math, batching, and cache behavior."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from yuqing import embed
from yuqing.store import CleanDoc, Store


class EmbedTest(unittest.TestCase):
    def test_vector_helpers(self) -> None:
        vector = [0.1, 0.2, 0.3, 0.4]
        restored = embed.from_blob(embed.to_blob(vector))
        self.assertTrue(all(abs(left - right) < 1e-6 for left, right in zip(restored, vector)))
        self.assertAlmostEqual(1.0, embed.cosine([1, 0], [1, 0]))
        self.assertEqual(0.0, embed.cosine([1, 0], [0, 1]))
        self.assertLess(embed.cosine([1, 0], [-1, 0]), 0.0)
        self.assertEqual(0.0, embed.cosine([], [1]))
        ranked = embed.top_k_similar(
            [1, 0], [("a", [1, 0]), ("b", [0, 1]), ("c", [0.9, 0.1])], k=2,
        )
        self.assertEqual(["a", "c"], [item[0] for item in ranked])
        self.assertEqual([], embed.top_k_similar([1, 0], [("a", [0, 1])], min_sim=0.5))
        groups = embed.cluster(
            [("a", [1, 0]), ("b", [0.98, 0.02]), ("c", [0, 1])], threshold=0.9,
        )
        self.assertEqual([["a", "b"], ["c"]], sorted(sorted(group) for group in groups))

    def test_ensure_embeddings_batches_and_reuses_cache(self) -> None:
        store = Store(":memory:")
        for index in range(3):
            store.add_clean(CleanDoc.build(
                platform="weibo", entity_id="e", native_id=f"n{index}",
                text=f"帖子内容{index}", fetched_at="2026-07-07T00:00:00",
            ))
        store.commit()
        calls = {"count": 0}

        def fake_embed(texts, **_kwargs):
            calls["count"] += 1
            return [[float(len(text)), 1.0, 0.0] for text in texts]

        def resolve(key: str) -> str:
            return "test-key" if key == "EMBED_API_KEY" else ""

        with patch.object(embed.config, "resolve", side_effect=resolve), patch.object(
            embed, "embed_texts", side_effect=fake_embed,
        ):
            self.assertEqual(3, embed.ensure_embeddings(store, now="2026-07-07T00:00:00"))
            self.assertEqual(0, embed.ensure_embeddings(store, now="2026-07-07T00:00:00"))
        self.assertEqual(1, calls["count"])
        self.assertEqual(3, len(store.embeddings_for("e")))
        store.close()

    def test_incomplete_batch_is_discarded_and_no_key_degrades(self) -> None:
        store = Store(":memory:")
        for index in range(3):
            store.add_clean(CleanDoc.build(
                platform="weibo", entity_id="e", native_id=f"m{index}",
                text=f"内容{index}", fetched_at="t",
            ))
        store.commit()
        with patch.object(
            embed.config, "resolve",
            side_effect=lambda key: "test-key" if key == "EMBED_API_KEY" else "",
        ), patch.object(
            embed, "embed_texts", return_value=[[1.0, 0.0]],
        ):
            self.assertEqual(0, embed.ensure_embeddings(store, now="2026-07-07T00:00:00"))
        with patch.object(embed.config, "resolve", return_value=""):
            self.assertEqual(0, embed.ensure_embeddings(store))
        store.close()

    def test_module_cli_only_exposes_connectivity_probe(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            embed.main([])
        self.assertIn("python -m yuqing.embed ping", output.getvalue())


if __name__ == "__main__":
    unittest.main()
