import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

import leakage_audit as audit


class LeakageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        Image.new("RGB", (32, 24), "red").save(self.root / "one.png")
        shutil.copyfile(self.root / "one.png", self.root / "copy.png")
        Image.new("RGB", (32, 24), "blue").save(self.root / "other.png")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, rows):
        p = self.root / "manifest.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return p

    def row(self, aid, path, split="train", group="unknown", **extra):
        return {"asset_id": aid, "path": path, "split": split, "source_group": group, **extra}

    def run_small(self):
        manifest = self.manifest([self.row("a", "one.png"), self.row("b", "copy.png", "test"), self.row("c", "other.png", "calibration")])
        return audit.run_audit(manifest, self.root / "run")[0]

    def test_duplicate_ids_rejected_before_output(self):
        manifest = self.manifest([self.row("a", "one.png"), self.row("a", "other.png")])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            audit.run_audit(manifest, self.root / "run")
        self.assertFalse((self.root / "run").exists())

    def test_bytes_confirm_but_visual_candidate_does_not_union(self):
        result = self.run_small()
        # Red and blue flat images have the same dHash: a deliberate collision.
        self.assertEqual(len(result["candidate_edges"]), 3)
        self.assertEqual(audit.components(result), [["a", "b"], ["c"]])
        self.assertEqual(result["summary"]["confirmed_train_linked_test_assets"], 1)
        self.assertEqual(result["summary"]["test_asset_leakage_lower_bound"], 1)

    def test_failed_decode_keeps_denominator_and_no_clean(self):
        manifest = self.manifest([self.row("a", "missing.png"), self.row("b", "also_missing.png", "test")])
        result, envelope = audit.run_audit(manifest, self.root / "run")
        self.assertEqual(result["summary"]["planned_assets"], 2)
        self.assertEqual(result["summary"]["audited_assets"], 0)
        self.assertEqual(result["summary"]["cross_split_pair_universe"]["test__train"], 1)
        self.assertIsNone(result["summary"]["test_asset_leakage_lower_bound"])
        self.assertEqual(envelope["quality"]["verdict"], "incomplete")

    def test_chinese_ngram_normalization(self):
        a = audit.normalize_text("小猫，坐在窗台。ＡＢＣ")
        b = audit.normalize_text("小猫坐在窗台abc")
        self.assertEqual(a, b)
        self.assertEqual(audit.text_ngrams(a, 3), audit.text_ngrams(b, 3))
        self.assertNotEqual(audit.text_ngrams(a, 3), audit.text_ngrams("小狗站在马路边abc", 3))

    def test_review_binding_is_atomic_and_valid_review_splits(self):
        result = self.run_small()
        payload = {"schema_version": "2.0", **{k: result[k] for k in ("manifest_digest", "input_digest", "config_digest")}, "reviews": [{"pair_id": audit.pair_id("a", "c"), "decision": "related", "evidence": "known transformation", "reviewer": "tester", "origin": "construction_truth_fixture"}]}
        file = self.root / "reviews.json"
        wrong = copy.deepcopy(payload)
        wrong["input_digest"] = "wrong"
        audit.write_json(file, wrong)
        with self.assertRaisesRegex(ValueError, "REVIEW_BINDING_MISMATCH"):
            audit.import_reviews(self.root / "run", file, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())
        audit.write_json(file, payload)
        reviewed, _ = audit.import_reviews(self.root / "run", file, self.root / "reviewed")
        self.assertEqual(audit.components(reviewed), [["a", "b", "c"]])
        rows, _ = audit.propose_split(self.root / "reviewed", self.root / "proposal")
        self.assertEqual(len({r["proposed_split"] for r in rows}), 1)
        self.assertEqual(len(result["reviews"]), 0)

    def test_chain_never_invents_pair_confirmation(self):
        assets = [self.row(x, "one.png") | {"decode_status": "ok"} for x in "abc"]
        edges = [{"pair_id": audit.pair_id(a, b), "asset_a": a, "asset_b": b, "cross_split": False, "reasons": [{"method": "image_dhash"}], "evidence_status": "candidate_only"} for a, b in [("a", "b"), ("b", "c")]]
        result = {"assets": assets, "candidate_edges": edges, "reviews": [{"pair_id": e["pair_id"], "decision": "related"} for e in edges]}
        self.assertEqual(audit.components(result), [["a", "b", "c"]])
        self.assertEqual(len(audit.accepted_edges(result)), 2)
        self.assertNotIn(audit.pair_id("a", "c"), [e["pair_id"] for e in audit.accepted_edges(result)])

    def test_video_requires_order_and_multiple_frames(self):
        cfg = dict(audit.DEFAULT_CONFIG)
        cfg["video_hamming_max"] = 0
        hashes = ["0" * 16, "f" * 16, "a" * 16, "5" * 16]
        def sample(h):
            return {"video_samples": [{"time_s": i * .5, "dhash": x} for i, x in enumerate(h)], "duration_s": 2}
        self.assertIsNotNone(audit.ordered_video_match(sample(hashes), sample(hashes), cfg))
        self.assertIsNone(audit.ordered_video_match(sample(hashes), sample(hashes[::-1]), cfg))
        self.assertIsNone(audit.ordered_video_match(sample(hashes), sample([hashes[0], "c" * 16, "c" * 16, "c" * 16]), cfg))

    def test_one_test_asset_twenty_train_edges_count_once(self):
        rows = [self.row(f"train_{i}", "one.png") for i in range(20)] + [self.row("test", "copy.png", "test")]
        result, _ = audit.run_audit(self.manifest(rows), self.root / "run")
        self.assertEqual(result["summary"]["confirmed_cross_split_edges"], 20)
        self.assertEqual(result["summary"]["confirmed_train_linked_test_assets"], 1)

    def test_tampered_artifact_refuses_review_and_split(self):
        self.run_small()
        (self.root / "run/audit_summary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "ARTIFACT_MISMATCH"):
            audit.load_audit(self.root / "run")

    def test_unknown_source_not_one_group(self):
        result = self.run_small()
        self.assertFalse(any(r["method"] == "declared_source_group" for e in result["candidate_edges"] for r in e["reasons"]))

    def test_manual_pair_can_record_algorithm_miss(self):
        result = self.run_small()
        payload = {"schema_version": "2.0", **{k: result[k] for k in ("manifest_digest", "input_digest", "config_digest")}, "reviews": [], "manual_pairs": [{"asset_a": "a", "asset_b": "missing", "reason": "known"}]}
        audit.write_json(self.root / "reviews.json", payload)
        with self.assertRaisesRegex(ValueError, "INVALID_MANUAL_PAIR"):
            audit.import_reviews(self.root / "run", self.root / "reviews.json", self.root / "rejected")

    def test_valid_manual_pair_is_added_with_review(self):
        Image.fromarray(__import__("numpy").random.default_rng(17).integers(0, 256, (24, 32, 3), dtype="uint8")).save(self.root / "noise.png")
        cfg = dict(audit.DEFAULT_CONFIG)
        cfg["image_hamming_max"] = 0
        audit.write_json(self.root / "config.json", cfg)
        manifest = self.manifest([self.row("a", "one.png"), self.row("z", "noise.png", "test")])
        result, _ = audit.run_audit(manifest, self.root / "run", self.root / "config.json")
        self.assertEqual(len(result["candidate_edges"]), 0)
        pid = audit.pair_id("a", "z")
        payload = {"schema_version": "2.0", **{k: result[k] for k in ("manifest_digest", "input_digest", "config_digest")}, "manual_pairs": [{"asset_a": "a", "asset_b": "z", "reason": "construction ledger"}], "reviews": [{"pair_id": pid, "decision": "related", "evidence": "known construction", "reviewer": "tester", "origin": "construction_truth_fixture"}]}
        audit.write_json(self.root / "reviews.json", payload)
        reviewed, _ = audit.import_reviews(self.root / "run", self.root / "reviews.json", self.root / "reviewed")
        self.assertEqual(reviewed["candidate_edges"][0]["reasons"][0]["method"], "manual")
        self.assertEqual(audit.components(reviewed), [["a", "z"]])

    def test_invalid_cli_writes_blocked_envelope(self):
        manifest = self.manifest([self.row("a", "one.png"), self.row("a", "other.png")])
        status = audit.main(["validate", "--manifest", str(manifest), "--out", str(self.root / "blocked")])
        envelope = json.loads((self.root / "blocked/result.json").read_text())
        self.assertEqual(status, 2)
        self.assertEqual(envelope["status"], "blocked")
        self.assertEqual(envelope["quality"]["verdict"], "not_assessed")

    def test_asset_change_changes_input_binding(self):
        original = self.run_small()
        Image.new("RGB", (32, 24), "green").save(self.root / "one.png")
        newer, _ = audit.run_audit(self.root / "manifest.jsonl", self.root / "new")
        self.assertEqual(original["manifest_digest"], newer["manifest_digest"])
        self.assertNotEqual(original["input_digest"], newer["input_digest"])


if __name__ == "__main__":
    unittest.main()
