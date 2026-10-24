"""Independent constructed counterexamples; these are not human review data."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

import leakage_audit as audit


class DuplicateReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        Image.new("RGB", (24, 16), "red").save(self.root / "red.png")
        Image.new("RGB", (24, 16), "blue").save(self.root / "blue.png")

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, aid, path="red.png", split="train", group="unknown", text=""):
        return {"asset_id": aid, "path": path, "split": split,
                "source_group": group, "text": text}

    def run_rows(self, rows, operation="audit"):
        path = self.root / "manifest.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return audit.run_audit(path, self.root / "run", operation=operation)[0]

    def test_same_pixels_different_encoding_does_not_confirm_source(self):
        with Image.open(self.root / "red.png") as image:
            image.save(self.root / "red.bmp")
        shutil.copyfile(self.root / "red.png", self.root / "copy.png")
        result = self.run_rows([self.row("a"), self.row("b", "red.bmp", "test"),
                                self.row("c", "copy.png", "calibration")])
        by_id = {a["asset_id"]: a for a in result["assets"]}
        self.assertNotEqual(by_id["a"]["sha256"], by_id["b"]["sha256"])
        self.assertEqual(by_id["a"]["canonical_pixel_sha256"], by_id["b"]["canonical_pixel_sha256"])
        self.assertEqual(by_id["a"]["sha256"], by_id["c"]["sha256"])
        self.assertEqual(audit.components(result), [["a", "c"], ["b"]])
        self.assertEqual(len(audit.accepted_edges(result)), 1)
        self.assertEqual(result["summary"]["confirmed_train_linked_test_assets"], 0)

    def test_dhash_collision_and_identical_text_are_candidates_only(self):
        text = "桌面上的杯子放在左边"
        result = self.run_rows([self.row("red", text=text),
                                self.row("blue", "blue.png", "test", text=text)])
        edge = result["candidate_edges"][0]
        methods = {r["method"] for r in edge["reasons"]}
        self.assertEqual(methods, {"image_dhash", "text_ngram_jaccard"})
        self.assertEqual(edge["evidence_status"], "candidate_only")
        self.assertEqual(audit.accepted_edges(result), [])
        self.assertEqual(audit.components(result), [["blue"], ["red"]])
        self.assertIsNone(result["summary"]["precision_on_adjudicated_candidates"])

    def test_video_shift_reverse_and_broken_order_have_distinct_outcomes(self):
        cfg = dict(audit.DEFAULT_CONFIG, video_hamming_max=0)
        hashes = ["0" * 16, "f" * 16, "a" * 16, "5" * 16]
        def sample(items, duration=2):
            return {"video_samples": [{"time_s": i * .5, "dhash": h}
                                      for i, h in enumerate(items)], "duration_s": duration}
        original = sample(hashes)
        shifted = audit.ordered_video_match(original, sample(["c" * 16] + hashes[:3]), cfg)
        self.assertEqual(shifted["consecutive"], 3)
        self.assertEqual(shifted["fraction"], .75)
        self.assertEqual([(p["a_time_s"], p["b_time_s"]) for p in shifted["matched_times"]],
                         [(0, .5), (.5, 1), (1, 1.5)])
        self.assertIsNone(audit.ordered_video_match(original, sample(hashes[::-1]), cfg))
        self.assertIsNone(audit.ordered_video_match(original, sample([hashes[i] for i in [1, 0, 2, 3]]), cfg))
        self.assertIsNone(audit.ordered_video_match(original, sample(hashes, duration=4), cfg))

    def test_chain_keeps_edges_and_test_lower_bound_nontransitive(self):
        assets = [self.row(aid, split=split) | {"decode_status": "ok"}
                  for aid, split in [("a", "train"), ("b", "calibration"),
                                     ("c", "test"), ("d", "test")]]
        pairs = [("a", "b"), ("b", "c"), ("a", "d")]
        edges = [{"pair_id": audit.pair_id(a, b), "asset_a": a, "asset_b": b,
                  "cross_split": True, "reasons": [{"method": "manual"}],
                  "evidence_status": "candidate_only"} for a, b in pairs]
        result = {"assets": assets, "candidate_edges": edges,
                  "reviews": [{"pair_id": e["pair_id"], "decision": "related",
                               "origin": "construction_truth_fixture"} for e in edges]}
        summary = audit.summarize(result)
        self.assertEqual(audit.components(result), [["a", "b", "c", "d"]])
        self.assertEqual(len(audit.accepted_edges(result)), 3)
        self.assertNotIn(audit.pair_id("a", "c"), [e["pair_id"] for e in audit.accepted_edges(result)])
        self.assertEqual(summary["confirmed_train_linked_test_assets"], 1)
        self.assertEqual(summary["test_asset_leakage_lower_bound"], .5)

    def test_cross_split_universes_keep_failed_asset_in_planned_counts(self):
        rows = [self.row("tr1"), self.row("tr2"), self.row("cal", split="calibration"),
                self.row("te1", split="test"), self.row("te2", split="test"),
                self.row("failed", "missing.png", "test")]
        summary = self.run_rows(rows)["summary"]
        self.assertEqual(summary["all_pair_universe"], 15)
        self.assertEqual(summary["decoded_pair_universe"], 10)
        self.assertEqual(summary["cross_split_pair_universe"],
                         {"calibration__test": 3, "calibration__train": 2, "test__train": 6})
        self.assertEqual(summary["eligible_cross_split_pair_universe"],
                         {"calibration__test": 2, "calibration__train": 2, "test__train": 4})
        self.assertEqual(summary["confirmed_train_linked_test_assets"], 2)

    def test_validation_only_cannot_bypass_grouping_via_proposal_or_review(self):
        result = self.run_rows([self.row(f"a{i}", group="one_family") for i in range(6)],
                               operation="validate")
        self.assertFalse(result["pair_scan_completed"])
        with self.assertRaisesRegex(ValueError, "PAIR_SCAN_REQUIRED"):
            audit.propose_split(self.root / "run", self.root / "proposal")
        self.assertFalse((self.root / "proposal").exists())
        payload = {"schema_version": "2.0", **{k: result[k] for k in
                   ("manifest_digest", "input_digest", "config_digest")}, "reviews": [],
                   "manual_pairs": [{"asset_a": "a0", "asset_b": "a1", "reason": "fixture"}]}
        audit.write_json(self.root / "reviews.json", payload)
        with self.assertRaisesRegex(ValueError, "PAIR_SCAN_REQUIRED"):
            audit.import_reviews(self.root / "run", self.root / "reviews.json", self.root / "reviewed")
        self.assertFalse((self.root / "reviewed").exists())
        legacy = dict(result)
        del legacy["pair_scan_completed"]
        with self.assertRaisesRegex(ValueError, "PAIR_SCAN_REQUIRED"):
            audit.require_pair_scan(legacy)

    def test_proposal_preserves_all_confirmed_members_even_when_ratios_deviate(self):
        self.run_rows([self.row(f"a{i}", group="one_family") for i in range(6)])
        rows, _ = audit.propose_split(self.root / "run", self.root / "proposal")
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({r["group_id"] for r in rows}), 1)
        self.assertEqual(len({r["proposed_split"] for r in rows}), 1)

    def test_long_chain_within_default_asset_limit_does_not_recurse(self):
        count = audit.DEFAULT_CONFIG["max_assets"]
        ids = [f"{i:04}" for i in range(count)]
        uf = audit.UnionFind(ids)
        for i in range(count - 1, 0, -1):
            uf.union(ids[i], ids[i - 1])
        self.assertEqual(uf.find(ids[-1]), ids[0])
        self.assertEqual({uf.find(aid) for aid in ids}, {ids[0]})


if __name__ == "__main__":
    unittest.main()
