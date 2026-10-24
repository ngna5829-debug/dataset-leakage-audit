"""Offline, conservative media leakage auditing. No model or remote service."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import time
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

VERSION = "2.0.0"
DEFAULT_CONFIG = {
    "schema_version": "2.0", "declared_splits": ["train", "calibration", "test"],
    "dhash_bits": 64, "image_hamming_max": 6, "text_ngram": 3,
    "text_jaccard_min": 0.8, "text_min_chars": 8,
    "video_sample_interval_s": 0.5, "video_max_frames": 900,
    "video_hamming_max": 6, "video_min_consecutive": 3,
    "video_min_match_fraction": 0.75, "video_min_duration_ratio": 0.8,
    "max_assets": 1000,
    "policy": "exact_bytes_or_declared_source_or_confirmed_duplicate_related",
}
DECISIONS = {"duplicate", "related", "distinct", "unsure"}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def strict_json(text):
    def object_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"INVALID_JSON：重复字段 {key}")
            obj[key] = value
        return obj
    value = json.loads(text, object_pairs_hook=object_pairs)
    # Also rejects exponent overflow (1e999), not only NaN/Infinity tokens.
    try:
        canonical(value)
    except ValueError as exc:
        raise ValueError("INVALID_JSON：不允许 NaN、Infinity 或溢出的数值") from exc
    return value


def read_json(path):
    return strict_json(Path(path).read_text(encoding="utf-8-sig"))


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def csv_write(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fresh_out(out):
    out = Path(out)
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ValueError(f"OUTPUT_EXISTS：目录已有内容，请使用新目录：{out}")
    out.mkdir(parents=True, exist_ok=True)
    return out


def require_separate_output(out, files=(), directories=()):
    target = Path(out).resolve()
    for source in files:
        try:
            source = Path(source).resolve()
        except (OSError, ValueError, RuntimeError):
            continue  # Invalid media paths are recorded by the decode ledger.
        if source == target or source.is_relative_to(target):
            raise ValueError("INPUT_OUTPUT_OVERLAP：输出目录不能包含输入文件或媒体路径")
    for source in directories:
        source = Path(source).resolve()
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("INPUT_OUTPUT_OVERLAP：输出与原审计目录不能重叠")


def confined_path(directory, name, code):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError(f"{code}：需为目录内的相对路径")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{code}：不允许绝对路径或父目录跳转")
    path = (Path(directory) / relative).resolve()
    if not path.is_relative_to(Path(directory).resolve()):
        raise ValueError(f"{code}：符号链接不能指向目录外")
    return path


def load_config(path=None):
    return validate_config(read_json(path) if path else {})


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_config(incoming):
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(incoming, dict) or set(incoming) - set(cfg):
        raise ValueError("INVALID_CONFIG：配置含未知字段或不是对象")
    cfg.update(incoming)
    if cfg["schema_version"] != "2.0" or type(cfg["dhash_bits"]) is not int or cfg["dhash_bits"] != 64 or cfg["policy"] != DEFAULT_CONFIG["policy"]:
        raise ValueError("INVALID_CONFIG：不支持此 schema、hash 位数或 policy")
    splits = cfg["declared_splits"]
    if not isinstance(splits, list) or len(splits) < 2 or not all(isinstance(s, str) and s.strip() == s and s for s in splits) or len(splits) != len(set(splits)):
        raise ValueError("INVALID_CONFIG：declared_splits 至少两个不重复字符串")
    for name in ("image_hamming_max", "video_hamming_max"):
        if type(cfg[name]) is not int or not 0 <= cfg[name] <= 64:
            raise ValueError(f"INVALID_CONFIG：{name} 需为 0 到 64 的整数")
    for name in ("text_ngram", "text_min_chars", "video_max_frames", "video_min_consecutive", "max_assets"):
        if type(cfg[name]) is not int or cfg[name] < 1:
            raise ValueError(f"INVALID_CONFIG：{name} 需为正整数")
    for name in ("text_jaccard_min", "video_min_match_fraction", "video_min_duration_ratio"):
        if not finite_number(cfg[name]) or not 0 < cfg[name] <= 1:
            raise ValueError(f"INVALID_CONFIG：{name} 需在 (0,1] 内")
    if not finite_number(cfg["video_sample_interval_s"]) or cfg["video_sample_interval_s"] <= 0:
        raise ValueError("INVALID_CONFIG：video_sample_interval_s 需为有限正数")
    return cfg


def load_manifest(path, cfg):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, strict=True)
            fields = reader.fieldnames
            if fields and (any(not field.strip() for field in fields) or len(fields) != len(set(fields))):
                raise ValueError("INVALID_CSV：表头为空或重复")
            raw = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in raw):
                raise ValueError("INVALID_CSV：行字段数与表头不一致")
    elif path.suffix.lower() == ".jsonl":
        raw = [strict_json(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        raise ValueError("MANIFEST_FORMAT：清单需为 CSV 或 JSONL")
    if not raw:
        raise ValueError("EMPTY_MANIFEST：清单没有资产，不能比较")
    if len(raw) > cfg["max_assets"]:
        raise ValueError(f"ASSET_LIMIT：{len(raw)} 条超过本次上限 {cfg['max_assets']}；请分组或显式提高限制")
    assets, ids = [], set()
    for line, row in enumerate(raw, 2):
        if not isinstance(row, dict):
            raise ValueError(f"INVALID_ROW：第 {line} 行不是对象")
        for name in ("asset_id", "path", "split", "source_group"):
            if name not in row or not isinstance(row[name], str):
                raise ValueError(f"MISSING_FIELD：第 {line} 行缺字符串字段 {name}")
        aid = row["asset_id"].strip()
        if not aid or aid in ids:
            raise ValueError(f"DUPLICATE_ID：第 {line} 行 asset_id 为空或重复：{aid}")
        ids.add(aid)
        if row["split"] not in cfg["declared_splits"]:
            raise ValueError(f"INVALID_SPLIT：{aid} 的 split={row['split']} 未声明")
        if not row["path"].strip():
            raise ValueError(f"EMPTY_PATH：{aid} 没有媒体路径")
        for name in ("media_type", "text", "prompt", "license_note", "parent_asset_id", "provenance"):
            if name in row and not isinstance(row[name], str):
                raise ValueError(f"INVALID_FIELD：{aid} 的 {name} 需为字符串")
        media = row.get("media_type") or ("video" if Path(row["path"]).suffix.lower() in {".mp4", ".mov", ".avi", ".webm"} else "image")
        if media not in {"image", "video"}:
            raise ValueError(f"INVALID_MEDIA：{aid} 只支持 image/video")
        text_value = row.get("text", row.get("prompt", "")) or ""
        if not isinstance(text_value, str):
            raise ValueError(f"INVALID_TEXT：{aid} 的 text/prompt 需为字符串")
        assets.append({"asset_id": aid, "path": row["path"], "split": row["split"],
                       "source_group": row["source_group"].strip(), "media_type": media,
                       "text": text_value, "license_note": row.get("license_note", "unknown"),
                       "parent_asset_id": row.get("parent_asset_id", ""),
                       "provenance": row.get("provenance", "user_declared")})
    return assets


def normalize_text(text):
    # Character ngrams work without a Chinese tokenization/model dependency.
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in normalized if not unicodedata.category(c).startswith(("P", "Z", "C")))


def text_ngrams(text, n):
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


def dhash(image):
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    gray = Image.alpha_composite(white, rgba).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    arr = np.asarray(gray)
    bits = (arr[:, 1:] > arr[:, :-1]).reshape(-1)
    return f"{sum(int(v) << i for i, v in enumerate(bits)):016x}"


def hamming(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def thumbnail(image, path):
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((320, 240))
    image.save(path, "JPEG", quality=85)


def video_features(path, cfg, thumb_path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("VIDEO_OPEN_FAILED：无法打开视频")
    fps = cap.get(cv2.CAP_PROP_FPS)
    declared_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if not finite_number(declared_count) or declared_count < 0:
        cap.release()
        raise ValueError("VIDEO_FRAME_COUNT_INVALID：视频声明帧数无效")
    declared_count = int(declared_count)
    if not math.isfinite(fps) or fps <= 0:
        cap.release()
        raise ValueError("VIDEO_FPS_UNKNOWN：无法建立时间顺序")
    if declared_count > cfg["video_max_frames"]:
        cap.release()
        raise ValueError(f"VIDEO_LIMIT：声明 {declared_count} 帧超过上限，不把片段当整片")
    samples, count, next_t, last_t = [], 0, 0.0, -1.0
    first_image = None
    timestamp_modes = set()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            count += 1
            if count > cfg["video_max_frames"]:
                raise ValueError("VIDEO_LIMIT：解码帧超过上限，不截断继续")
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            mode = "opencv_pos_msec"
            if not math.isfinite(t) or t <= last_t:
                t = (count - 1) / fps
                mode = "frame_index_over_declared_fps"
            if t <= last_t:
                raise ValueError("VIDEO_TIME_NONMONOTONIC：无法建立单调时间顺序")
            timestamp_modes.add(mode)
            last_t = t
            if t + 1e-6 >= next_t:
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if first_image is None:
                    first_image = image.copy()
                samples.append({"frame_index": count - 1, "time_s": round(t, 6), "dhash": dhash(image)})
                next_t = t + cfg["video_sample_interval_s"]
    finally:
        cap.release()
    if count == 0 or not samples:
        raise ValueError("VIDEO_NO_FRAMES：没有可解码帧")
    if declared_count > 0 and count < declared_count:
        raise ValueError(f"VIDEO_DECODE_INCOMPLETE：声明 {declared_count} 帧，仅解码 {count} 帧")
    duration = last_t + 1 / fps
    if not finite_number(duration) or duration <= 0:
        raise ValueError("VIDEO_DURATION_INVALID：无法建立有限时长")
    thumbnail(first_image, thumb_path)
    return {"video_samples": samples, "decoded_frames": count, "declared_frames": declared_count,
            "duration_s": round(duration, 6), "fps": fps,
            "timestamp_method": sorted(timestamp_modes), "width": first_image.width, "height": first_image.height}


def ordered_video_match(a, b, cfg):
    """Longest consecutive matching run along a fixed offset; never reorder frames."""
    aa, bb = a["video_samples"], b["video_samples"]
    if not aa or not bb:
        return None
    duration_ratio = min(a["duration_s"], b["duration_s"]) / max(a["duration_s"], b["duration_s"])
    best = {"consecutive": 0, "pairs": []}
    for offset in range(-len(bb) + 1, len(aa)):
        run = []
        for j in range(len(bb)):
            i = j + offset
            if 0 <= i < len(aa) and hamming(aa[i]["dhash"], bb[j]["dhash"]) <= cfg["video_hamming_max"]:
                run.append({"a_time_s": aa[i]["time_s"], "b_time_s": bb[j]["time_s"],
                            "distance": hamming(aa[i]["dhash"], bb[j]["dhash"])})
                if len(run) > best["consecutive"]:
                    best = {"consecutive": len(run), "pairs": list(run)}
            else:
                run = []
    fraction = best["consecutive"] / min(len(aa), len(bb))
    if best["consecutive"] < cfg["video_min_consecutive"] or fraction < cfg["video_min_match_fraction"] or duration_ratio < cfg["video_min_duration_ratio"]:
        return None
    return {"method": "ordered_video_dhash", "consecutive": best["consecutive"],
            "fraction": round(fraction, 6), "duration_ratio": round(duration_ratio, 6),
            "hamming_max": cfg["video_hamming_max"], "matched_times": best["pairs"]}


def pair_id(a, b):
    return digest(sorted([a, b]))[:24]


def known_group(group):
    return bool(group) and group.casefold() not in {"unknown", "未知", "none", "null"}


def candidate_pairs(assets, cfg):
    candidates = []
    for a, b in itertools.combinations(assets, 2):
        reasons = []
        if known_group(a["source_group"]) and a["source_group"] == b["source_group"]:
            reasons.append({"method": "declared_source_group", "value": a["source_group"]})
        if a.get("sha256") and a["sha256"] == b.get("sha256"):
            reasons.append({"method": "sha256_exact", "value": a["sha256"]})
        if a.get("canonical_pixel_sha256") and a["canonical_pixel_sha256"] == b.get("canonical_pixel_sha256"):
            reasons.append({"method": "canonical_pixels_exact", "note": "EXIF 后 RGBA 尺寸与像素完全一致"})
        if a.get("dhash") and b.get("dhash"):
            distance = hamming(a["dhash"], b["dhash"])
            if distance <= cfg["image_hamming_max"]:
                reasons.append({"method": "image_dhash", "distance": distance, "threshold": cfg["image_hamming_max"]})
        if a.get("video_samples") and b.get("video_samples"):
            hit = ordered_video_match(a, b, cfg)
            if hit:
                reasons.append(hit)
        if a.get("text_ngrams") and b.get("text_ngrams"):
            ga, gb = set(a["text_ngrams"]), set(b["text_ngrams"])
            jaccard = len(ga & gb) / len(ga | gb)
            if jaccard >= cfg["text_jaccard_min"]:
                reasons.append({"method": "text_ngram_jaccard", "similarity": round(jaccard, 6), "threshold": cfg["text_jaccard_min"]})
        if reasons:
            methods = {r["method"] for r in reasons}
            candidates.append({"pair_id": pair_id(a["asset_id"], b["asset_id"]),
                               "asset_a": a["asset_id"], "asset_b": b["asset_id"],
                               "cross_split": a["split"] != b["split"], "reasons": reasons,
                               "evidence_status": "exact_bytes" if "sha256_exact" in methods else "declared_source" if "declared_source_group" in methods else "candidate_only"})
    return sorted(candidates, key=lambda e: (not e["cross_split"], e["evidence_status"] == "candidate_only", e["pair_id"]))


class UnionFind:
    def __init__(self, ids):
        self.parent = {x: x for x in ids}

    def find(self, x):
        # A valid chain can reach the asset limit; avoid Python's recursion limit.
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != x:
            parent = self.parent[x]
            self.parent[x] = root
            x = parent
        return root

    def union(self, a, b):
        ra, rb = sorted([self.find(a), self.find(b)])
        self.parent[rb] = ra


def accepted_edges(audit):
    reviews = {r["pair_id"]: r for r in audit["reviews"]}
    edges = []
    for e in audit["candidate_edges"]:
        reason = []
        methods = {r["method"] for r in e["reasons"]}
        if "sha256_exact" in methods:
            reason.append("exact_bytes")
        if "declared_source_group" in methods:
            reason.append("declared_source_group")
        review = reviews.get(e["pair_id"])
        if review and review["decision"] in {"duplicate", "related"}:
            reason.append("review_" + review["decision"])
        if reason:
            edges.append({**e, "grouping_evidence": reason})
    return edges


def components(audit, possible=False):
    uf = UnionFind([a["asset_id"] for a in audit["assets"]])
    edges = audit["candidate_edges"] if possible else accepted_edges(audit)
    for e in edges:
        uf.union(e["asset_a"], e["asset_b"])
    groups = defaultdict(list)
    for a in audit["assets"]:
        groups[uf.find(a["asset_id"])].append(a["asset_id"])
    return sorted((sorted(v) for v in groups.values()), key=lambda v: (-len(v), v))


def summarize(audit):
    assets, candidates = audit["assets"], audit["candidate_edges"]
    counts = Counter(a["split"] for a in assets)
    eligible_counts = Counter(a["split"] for a in assets if a["decode_status"] == "ok")
    confirmed = accepted_edges(audit)
    by_id = {a["asset_id"]: a for a in assets}
    contaminated_test = set()
    for e in confirmed:
        a, b = by_id[e["asset_a"]], by_id[e["asset_b"]]
        if {a["split"], b["split"]} == {"train", "test"}:
            contaminated_test.add(a["asset_id"] if a["split"] == "test" else b["asset_id"])
    eligible_test = {a["asset_id"] for a in assets if a["split"] == "test" and a["decode_status"] == "ok"}
    determinate = [r for r in audit["reviews"] if r["decision"] != "unsure"]
    positive = [r for r in determinate if r["decision"] in {"duplicate", "related"}]
    intrinsic_ids = {e["pair_id"] for e in candidates if e["evidence_status"] in {"exact_bytes", "declared_source"}}
    groups = components(audit)
    cross_components = [g for g in groups if len({by_id[x]["split"] for x in g}) > 1]
    total, decoded = len(assets), sum(a["decode_status"] == "ok" for a in assets)
    return {"planned_assets": total, "audited_assets": decoded, "decode_failures": total - decoded,
            "coverage": decoded / total if total else None, "planned_split_counts": dict(counts),
            "eligible_split_counts": dict(eligible_counts), "all_pair_universe": total * (total - 1) // 2,
            "decoded_pair_universe": decoded * (decoded - 1) // 2,
            "cross_split_pair_universe": {f"{a}__{b}": counts[a] * counts[b] for a, b in itertools.combinations(sorted(counts), 2)},
            "eligible_cross_split_pair_universe": {f"{a}__{b}": eligible_counts[a] * eligible_counts[b] for a, b in itertools.combinations(sorted(counts), 2)},
            "text_eligible_assets": sum(bool(a.get("text_ngrams")) for a in assets),
            "candidate_pairs": len(candidates), "cross_split_candidate_pairs": sum(e["cross_split"] for e in candidates),
            "reviewed_pairs": len(audit["reviews"]), "unresolved_candidate_pairs": len(candidates) - len(determinate),
            "precision_on_adjudicated_candidates": len(positive) / len(determinate) if determinate else None,
            "precision_denominator": len(determinate), "review_origin_counts": dict(Counter(r["origin"] for r in audit["reviews"])),
            "distinct_reviews_conflicting_with_bytes_or_declaration": sum(r["decision"] == "distinct" and r["pair_id"] in intrinsic_ids for r in audit["reviews"]),
            "confirmed_or_declared_edges": len(confirmed), "confirmed_cross_split_edges": sum(e["cross_split"] for e in confirmed),
            "confirmed_train_linked_test_assets": len(contaminated_test & eligible_test),
            "eligible_test_assets": len(eligible_test),
            "test_asset_leakage_lower_bound": len(contaminated_test & eligible_test) / len(eligible_test) if eligible_test else None,
            "confirmed_components": len(groups), "cross_split_components": len(cross_components),
            "max_component_size": max(map(len, groups), default=0), "possible_components": len(components(audit, True))}


def sql_export(audit, path):
    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    with con:
        con.executescript("""
        CREATE TABLE assets(asset_id TEXT PRIMARY KEY, media_type TEXT, path TEXT, split TEXT, source_group TEXT, sha256 TEXT, decode_status TEXT, details_json TEXT);
        CREATE TABLE features(asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id), dhash TEXT, canonical_pixel_sha256 TEXT, normalized_text TEXT, video_samples_json TEXT);
        CREATE TABLE candidate_edges(pair_id TEXT PRIMARY KEY, asset_a TEXT REFERENCES assets(asset_id), asset_b TEXT REFERENCES assets(asset_id), cross_split INTEGER, evidence_status TEXT, reasons_json TEXT);
        CREATE TABLE reviews(pair_id TEXT PRIMARY KEY REFERENCES candidate_edges(pair_id), decision TEXT, evidence TEXT, reviewer TEXT, origin TEXT, manifest_digest TEXT, config_digest TEXT, input_digest TEXT);
        CREATE TABLE failures(asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id), stage TEXT, code TEXT, message TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
        """)
        con.execute("PRAGMA foreign_keys=ON")
        for a in audit["assets"]:
            con.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)", (a["asset_id"], a["media_type"], a["path"], a["split"], a["source_group"], a.get("sha256"), a["decode_status"], json.dumps(a, ensure_ascii=False)))
            con.execute("INSERT INTO features VALUES (?,?,?,?,?)", (a["asset_id"], a.get("dhash"), a.get("canonical_pixel_sha256"), a["normalized_text"], json.dumps(a.get("video_samples", []))))
        for e in audit["candidate_edges"]:
            con.execute("INSERT INTO candidate_edges VALUES (?,?,?,?,?,?)", (e["pair_id"], e["asset_a"], e["asset_b"], int(e["cross_split"]), e["evidence_status"], json.dumps(e["reasons"], ensure_ascii=False)))
        for r in audit["reviews"]:
            con.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?)", (r["pair_id"], r["decision"], r["evidence"], r["reviewer"], r["origin"], audit["manifest_digest"], audit["config_digest"], audit["input_digest"]))
        for f in audit["failure_ledger"]:
            con.execute("INSERT INTO failures VALUES (?,?,?,?)", (f["asset_id"], f["stage"], f["code"], f["message"]))
        for key in ("schema_version", "manifest_digest", "input_digest", "config_digest", "evaluator_version"):
            con.execute("INSERT INTO metadata VALUES (?,?)", (key, str(audit[key])))
    con.close()


def render_html(audit, out):
    # All dynamic content is rendered via textContent; user text is never inserted as HTML.
    payload = {k: audit[k] for k in ("assets", "candidate_edges", "reviews", "summary", "input_digest", "manifest_digest", "config_digest", "config", "failure_ledger")}
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    document = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据来源泄漏复核</title><style>
*{box-sizing:border-box}:focus-visible{outline:3px solid #bf7900;outline-offset:3px}body{overflow-wrap:anywhere;margin:0;background:#eef2f5;color:#183043;font:16px/1.6 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:24px}h1{margin:0;color:#103d50}header,article,.panel{background:white;border-radius:12px;padding:20px;margin:16px 0}button,select,input,textarea{font:inherit;padding:8px;border:1px solid #78929e;border-radius:6px}button{cursor:pointer;background:#103d50;color:white}input,select,textarea{max-width:100%}label{max-width:100%}textarea{display:block;width:100%;min-height:70px}.zoom-button{display:block;width:100%;padding:0;background:transparent;border:1px solid #78929e}.pair>div{min-width:0}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;position:sticky;top:0;background:#eef2f5;padding:8px;z-index:1}.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}.pair img{width:100%;max-height:330px;object-fit:contain;background:#eef2f5;cursor:zoom-in}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:10px}.risk{color:#aa3500}.muted{color:#566877}.badge{background:#e8f2f4;border-radius:6px;padding:3px 8px}details{margin:10px 0}dialog{max-width:96vw;border:0;border-radius:10px}dialog img{max-width:85vw;max-height:80vh}.pager{display:flex;gap:10px;margin:12px 0}@media(max-width:650px){.pair{grid-template-columns:1fr}.toolbar{position:static}main{padding:12px}.toolbar label{display:block;width:100%}.toolbar input{display:block;width:100%}}
</style><main><header><h1>数据来源泄漏复核</h1><p id="summary"></p><p>程序已完成扫描。候选 ≠ 确认重复；同文本、相似画面或连通分量均不能证明同源。SHA 相同只证明字节一致；source_group 是输入声明。</p><p>下一步：先看跨集合候选，逐对记录证据，导出复核 JSON，再用 import-reviews 生成新版本。分组建议不会删除或移动素材。</p><p id="binding" class="muted"></p><p id="storage" role="status"></p></header>
<div class="toolbar"><label>范围 <select id="filter"><option value="cross">跨集合</option><option value="all">全部候选</option><option value="pending">未明确复核</option></select></label><label>复核者代号 <input id="reviewer" value="local_reviewer"></label><button id="export">导出已填写复核</button><button id="clear">清除本机草稿</button><span id="progress"></span></div>
<section class="panel" id="export-preview" hidden><p><a id="export-download">再次下载复核文件</a> · 下载未出现时，可复制下方完整内容并保存为同名 UTF-8 文件。</p><label>导出内容预览（可复制）<textarea id="export-text" readonly rows="10"></textarea></label><p id="export-status" role="status"></p></section><section class="panel"><details><summary>覆盖、分母与失败记录</summary><pre id="coverage"></pre><pre id="failures"></pre></details><p>duplicate = 确认同内容复制；related = 确认同源派生；distinct = 不同来源；unsure = 证据不足。related 会加入不可拆分组。相似但独立创作请选择 distinct。</p><p>既有 SHA/来源声明证据与人工意见分别保留。要修正错误 source_group，请另存清单重跑。下方图为报告缩略证据；视频卡提供有序匹配时间，整片判断需核对原片。</p><details><summary>手工添加漏检候选</summary><p>将两个已有 asset_id 与具体理由加入导出文件 manual_pairs 数组。格式与绑定见 README；CLI 会作为人工线索保留。</p></details></section>
<div class="pager"><button id="prev">上一页</button><span id="page"></span><button id="next">下一页</button></div><div id="cards"></div><dialog id="zoom"><button id="close">关闭</button><br><img alt="放大证据"></dialog></main>
<script type="application/json" id="audit-data">__PAYLOAD__</script><script>
const A=JSON.parse(document.getElementById('audit-data').textContent), $=id=>document.getElementById(id), byId=Object.fromEntries(A.assets.map(a=>[a.asset_id,a]));
const key='leakage-review-v2:'+A.input_digest+':'+A.config_digest, imported=Object.fromEntries(A.reviews.map(r=>[r.pair_id,r]));let draft={},page=0;
try{draft=JSON.parse(localStorage.getItem(key)||'{}');$('storage').textContent='本机草稿可恢复。只有导出后再经 CLI 导入，才成为正式复核工件。'}catch(e){$('storage').textContent='浏览器阻止本机保存，请及时导出；刷新可能丢失草稿。'}
const restoredReviewers=[...new Set(Object.values(draft).map(r=>r.reviewer).filter(r=>typeof r==='string'&&r.trim()))];if(restoredReviewers.length===1)$('reviewer').value=restoredReviewers[0];
const S=A.summary;$('summary').textContent=`读取 ${S.planned_assets} 项；媒体成功 ${S.audited_assets} 项；失败 ${S.decode_failures} 项。候选 ${S.candidate_pairs} 对，其中 ${S.cross_split_candidate_pairs} 对跨集合。确认或声明的跨集合边 ${S.confirmed_cross_split_edges} 条。`;
$('binding').textContent=`输入 ${A.input_digest.slice(0,12)} / 清单 ${A.manifest_digest.slice(0,12)} / 配置 ${A.config_digest.slice(0,12)}`;$('coverage').textContent=JSON.stringify(S,null,2);$('failures').textContent=A.failure_ledger.length?JSON.stringify(A.failure_ledger,null,2):'没有媒体解码失败。未发现候选仍不证明来源独立。';
const reasonName={sha256_exact:'相同字节 SHA-256',canonical_pixels_exact:'规范化像素一致（仍需来源复核）',declared_source_group:'输入声明同来源组',image_dhash:'图像 dHash 距离，越小越近',text_ngram_jaccard:'字符 ngram Jaccard，越大越近',ordered_video_dhash:'时间顺序帧 hash 匹配',manual:'人工追加线索'};
function el(tag,text){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x}
function save(){try{localStorage.setItem(key,JSON.stringify(draft));$('storage').textContent='草稿已保存于本浏览器；尚需导出并 CLI 导入。'}catch(e){$('storage').textContent='本机保存失败，请立即导出复核。'}updateProgress()}
function updateProgress(){$('progress').textContent=`本机已填写 ${Object.keys(draft).length} 对；正式导入 ${A.reviews.length} 对`}
function draw(){const mode=$('filter').value, edges=A.candidate_edges.filter(e=>mode==='all'||mode==='cross'&&e.cross_split||mode==='pending'&&!['duplicate','related','distinct'].includes((draft[e.pair_id]||imported[e.pair_id]||{}).decision));const pages=Math.max(1,Math.ceil(edges.length/12));page=Math.min(page,pages-1);$('page').textContent=`第 ${page+1}/${pages} 页，筛选 ${edges.length} 对`;$('cards').replaceChildren();
for(const edge of edges.slice(page*12,page*12+12)){const card=el('article'),title=el('h2',`${edge.asset_a} ↔ ${edge.asset_b}`);card.append(title,el('p',`${edge.cross_split?'跨集合风险':'同集合候选'} · ${edge.evidence_status}`));const pair=el('div');pair.className='pair';for(const id of [edge.asset_a,edge.asset_b]){const a=byId[id],col=el('div');col.append(el('p',`${id} · ${a.split} · ${a.media_type} · ${a.width||'?'}×${a.height||'?'}`));if(a.thumbnail){const im=el('img');im.src=a.thumbnail;im.alt=id+' 证据缩略图';const zoomButton=el('button');zoomButton.className='zoom-button';zoomButton.setAttribute('aria-label','放大 '+id+' 证据图');zoomButton.onclick=()=>{$('zoom').querySelector('img').src=a.thumbnail;$('zoom').showModal()};zoomButton.append(im);col.append(zoomButton)}else col.append(el('p','未生成证据图：'+a.decode_status));col.append(el('p','提示词：'+(a.text||'未提供')),el('p','声明来源：'+(a.source_group||'unknown')));pair.append(col)}card.append(pair);const det=el('details'),sum=el('summary',edge.reasons.map(r=>reasonName[r.method]||r.method).join('；'));det.append(sum,el('pre',JSON.stringify(edge.reasons,null,2)));card.append(det);const r=draft[edge.pair_id]||imported[edge.pair_id]||{},sel=el('select');for(const [v,t] of [['','待复核'],['duplicate','确认复制 duplicate'],['related','确认同源派生 related'],['distinct','不同来源 distinct'],['unsure','证据不足 unsure']]){const opt=el('option',t);opt.value=v;sel.append(opt)}sel.value=r.decision||'';const area=el('textarea');area.placeholder='写可复核的画面/来源依据，不能只写“看起来像”';area.value=r.evidence||'';const update=()=>{if(!sel.value){delete draft[edge.pair_id]}else draft[edge.pair_id]={pair_id:edge.pair_id,decision:sel.value,evidence:area.value,reviewer:$('reviewer').value,origin:'human_local'};save()};sel.onchange=update;area.oninput=update;const decisionLabel=el('label','复核结论');decisionLabel.append(sel);const evidenceLabel=el('label','具体画面或来源证据');evidenceLabel.append(area);card.append(decisionLabel,evidenceLabel);if(r.origin)card.append(el('p','已导入来源：'+r.origin));$('cards').append(card)}if(!edges.length)$('cards').append(el('p','此筛选没有候选。请结合覆盖与失败记录解读，不能视为独立性证明。'));updateProgress()}
$('filter').onchange=()=>{page=0;draw()};$('prev').onclick=()=>{page=Math.max(0,page-1);draw()};$('next').onclick=()=>{page++;draw()};$('close').onclick=()=>$('zoom').close();$('clear').onclick=()=>{if(confirm('仅清除本浏览器草稿？正式导入记录仍保留。')){draft={};save();draw()}};
$('export').onclick=()=>{const reviews=Object.values(draft);if(!reviews.length){alert('还没有填写复核。');return}if(reviews.some(r=>!r.evidence.trim()||!r.reviewer.trim())){alert('请为每条意见填写具体证据和复核者代号。');return}const payload={schema_version:'2.0',manifest_digest:A.manifest_digest,input_digest:A.input_digest,config_digest:A.config_digest,reviews,manual_pairs:[]};const text=JSON.stringify(payload,null,2)+'\n',a=$('export-download');a.href='data:application/json;charset=utf-8,'+encodeURIComponent(text);a.download='reviews-'+A.input_digest.slice(0,12)+'.json';a.textContent='再次下载 '+a.download;$('export-text').value=text;$('export-preview').hidden=false;$('export-status').textContent='已生成 '+reviews.length+' 条复核的可复制预览并发起下载；请保存文件后用 CLI 导入新目录。';a.click();$('export-preview').scrollIntoView({block:'nearest'})};draw();
</script></html>'''
    (out / "review.html").write_text(document.replace("__PAYLOAD__", data), encoding="utf-8")


def write_result(out, audit, operation, started, extra_artifacts=None):
    summary = audit["summary"]
    verdict = "incomplete" if summary["decode_failures"] or summary["audited_assets"] < 2 else "confirmed_or_declared_cross_split" if summary["confirmed_cross_split_edges"] else "needs_review" if summary["candidate_pairs"] else "no_candidates_found"
    if operation == "validate" and verdict != "incomplete":
        verdict = "validation_only_no_pair_scan"
    artifact_paths = [p for p in out.rglob("*") if p.is_file() and p.name != "result.json" and "input" not in p.relative_to(out).parts]
    result = {"schema_version": "2.0", "project_id": "dataset-leakage-audit", "operation": operation,
              "status": "completed", "quality": {"verdict": verdict, "meaning": "扫描执行状态与来源风险分开；未发现不证明独立性"},
              "input_digest": audit["input_digest"], "manifest_digest": audit["manifest_digest"],
              "config_digest": audit["config_digest"], "evaluator_version": VERSION,
              "artifacts": [{"path": p.relative_to(out).as_posix(), "sha256": sha256(p), "type": p.suffix.lstrip(".")} for p in sorted(artifact_paths)],
              "counts": summary, "warnings": audit["warnings"],
              "elapsed_seconds": round(time.perf_counter() - started, 6),
              "created_utc": datetime.now(timezone.utc).isoformat()}
    write_json(out / "result.json", result)
    return result


def save_audit(audit, out, operation, started):
    audit["summary"] = summarize(audit)
    audit["confirmed_components"] = components(audit)
    audit["possible_components"] = components(audit, True)
    write_json(out / "audit.json", audit)
    write_json(out / "audit_summary.json", audit["summary"])
    write_json(out / "config.json", audit["config"])
    write_json(out / "review_template.json", {"schema_version": "2.0", **{k: audit[k] for k in ("manifest_digest", "input_digest", "config_digest")}, "reviews": [], "manual_pairs": []})
    csv_write(out / "candidate_edges.csv", [{**e, "reasons": json.dumps(e["reasons"], ensure_ascii=False)} for e in audit["candidate_edges"]], ["pair_id", "asset_a", "asset_b", "cross_split", "evidence_status", "reasons"])
    csv_write(out / "exact_leaks.csv", [e for e in audit["candidate_edges"] if e["cross_split"] and any(r["method"] == "sha256_exact" for r in e["reasons"])], ["pair_id", "asset_a", "asset_b", "cross_split", "evidence_status"])
    csv_write(out / "failure_ledger.csv", audit["failure_ledger"], ["asset_id", "stage", "code", "message"])
    sql_export(audit, out / "audit.sqlite")
    render_html(audit, out)
    return write_result(out, audit, operation, started)


def run_audit(manifest, out, config=None, operation="audit", allow_existing=False):
    started = time.perf_counter()
    cfg = load_config(config)
    rows = load_manifest(manifest, cfg)
    if not allow_existing:
        require_separate_output(out, [manifest, *([config] if config else []),
                                     *(Path(manifest).parent / r["path"] for r in rows)])
    out = Path(out) if allow_existing else fresh_out(out)
    (out / "evidence").mkdir(exist_ok=True)
    failures, assets, warnings = [], [], []
    for row in rows:
        a = dict(row)
        a["normalized_text"] = normalize_text(a["text"])
        a["normalized_text_sha256"] = hashlib.sha256(a["normalized_text"].encode()).hexdigest()
        a["text_ngrams"] = sorted(text_ngrams(a["normalized_text"], cfg["text_ngram"])) if len(a["normalized_text"]) >= cfg["text_min_chars"] else []
        path = Path(manifest).parent / row["path"]
        thumb_rel = f"evidence/{digest(a['asset_id'])[:24]}.jpg"
        a["sha256"], a["decode_status"] = None, "failed"
        try:
            path = confined_path(Path(manifest).parent, row["path"], "MEDIA_PATH_ESCAPE")
            if not path.is_file():
                raise ValueError("MEDIA_NOT_FILE：媒体缺失或不是普通文件")
            a["byte_size"] = path.stat().st_size
            if a["byte_size"] == 0:
                raise ValueError("EMPTY_MEDIA：媒体文件为零字节")
            a["sha256"] = sha256(path)
            if a["media_type"] == "image":
                with Image.open(path) as source:
                    source.load()
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    a.update({"width": image.width, "height": image.height, "dhash": dhash(image),
                              "canonical_pixel_sha256": hashlib.sha256(canonical({"width": image.width, "height": image.height, "mode": "RGBA"}) + image.tobytes()).hexdigest()})
                    thumbnail(image, out / thumb_rel)
            else:
                a.update(video_features(path, cfg, out / thumb_rel))
            a["decode_status"], a["thumbnail"] = "ok", thumb_rel
        except (OSError, ValueError, RuntimeError, cv2.error) as exc:
            message = str(exc)
            # Avoid leaking machine-specific absolute paths into distributable examples.
            message = message.replace(str(path), row["path"])
            failures.append({"asset_id": a["asset_id"], "stage": "media_decode", "code": message.split("：")[0] if "：" in message else type(exc).__name__, "message": message})
        assets.append(a)
    unknown = sum(not known_group(a["source_group"]) for a in assets)
    if unknown:
        warnings.append(f"{unknown} 项来源未知；unknown 不合并成一个来源组")
    if failures:
        warnings.append(f"{len(failures)} 项媒体失败；未计为 clean。声明、文本或字节证据仍可能可用")
    warnings.extend(["dHash/文本/有序帧匹配只产生候选；近重复链不生成两两确认标签。", "阈值为预声明教学默认值，未在真实自然数据上校准。"])
    normalized_digest = digest([{k: a.get(k) for k in ("asset_id", "path", "split", "source_group", "media_type", "text", "sha256", "decode_status")} for a in assets])
    audit = {"schema_version": "2.0", "evaluator_version": VERSION,
             "pair_scan_completed": operation != "validate",
             "manifest_digest": sha256(manifest), "input_digest": normalized_digest,
             "config_digest": digest(cfg), "config": cfg, "assets": assets,
             "candidate_edges": [] if operation == "validate" else candidate_pairs(assets, cfg),
             "reviews": [], "failure_ledger": failures, "warnings": warnings,
             "provenance": {"operation": operation, "manifest_filename": Path(manifest).name,
                            "pixel_policy": "EXIF transpose then RGBA; dimensions included; no resize for exact pixel hash"}}
    (out / "manifest.snapshot.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in rows), encoding="utf-8")
    shutil.copyfile(manifest, out / ("manifest.original" + Path(manifest).suffix.lower()))
    return audit, save_audit(audit, out, operation, started)


def load_audit(directory):
    directory = Path(directory)
    result = read_json(directory / "result.json")
    if not isinstance(result, dict) or result.get("schema_version") != "2.0" or result.get("project_id") != "dataset-leakage-audit" or result.get("status") != "completed" or result.get("operation") not in {"audit", "validate", "demo", "import-reviews"}:
        raise ValueError("AUDIT_SCHEMA：需为已完成的审计或校验目录")
    if not isinstance(result.get("artifacts"), list):
        raise ValueError("ARTIFACT_MANIFEST：artifacts 需为数组")
    artifacts = {}
    for artifact in result["artifacts"]:
        if not isinstance(artifact, dict):
            raise ValueError("ARTIFACT_MANIFEST：工件条目需为对象")
        name = artifact.get("path")
        file = confined_path(directory, name, "ARTIFACT_PATH_ESCAPE")
        if name in artifacts or Path(name).as_posix() != name or name == "result.json":
            raise ValueError("ARTIFACT_MANIFEST：工件路径重复或不规范")
        artifacts[name] = file
        if not file.is_file() or sha256(file) != artifact.get("sha256"):
            raise ValueError(f"ARTIFACT_MISMATCH：旧结果已缺失或改变：{artifact['path']}；请从原输入重跑")
    required = {"audit.json", "audit_summary.json", "config.json", "manifest.snapshot.jsonl",
                "audit.sqlite", "candidate_edges.csv", "exact_leaks.csv", "failure_ledger.csv",
                "review.html", "review_template.json"}
    originals = set(artifacts) & {"manifest.original.csv", "manifest.original.jsonl"}
    if not required <= set(artifacts) or len(originals) != 1:
        raise ValueError("ARTIFACT_MANIFEST：缺少必要审计工件或唯一原始清单")
    audit = read_json(artifacts["audit.json"])
    if not isinstance(audit, dict) or audit.get("schema_version") != "2.0":
        raise ValueError("AUDIT_SCHEMA：audit.json 需为 schema_version 2.0 对象")
    cfg = validate_config(audit.get("config"))
    if cfg != audit.get("config") or digest(cfg) != audit.get("config_digest") or read_json(artifacts["config.json"]) != cfg:
        raise ValueError("CONFIG_MISMATCH：配置摘要不一致")
    original = artifacts[originals.pop()]
    if sha256(original) != audit.get("manifest_digest"):
        raise ValueError("MANIFEST_MISMATCH：原始清单摘要不一致")
    rows = load_manifest(original, cfg)
    snapshot = [strict_json(line) for line in artifacts["manifest.snapshot.jsonl"].read_text(encoding="utf-8").splitlines() if line.strip()]
    assets = audit.get("assets")
    if snapshot != rows or not isinstance(assets, list) or len(assets) != len(rows):
        raise ValueError("AUDIT_SCHEMA：资产数量或清单快照不一致")
    for a, row in zip(assets, rows):
        if not isinstance(a, dict) or any(a.get(k) != v for k, v in row.items()) or a.get("decode_status") not in {"ok", "failed"}:
            raise ValueError("AUDIT_SCHEMA：资产身份、顺序或解码状态不一致")
        if a["decode_status"] == "ok":
            expected = f"evidence/{digest(a['asset_id'])[:24]}.jpg"
            if a.get("thumbnail") != expected or expected not in artifacts:
                raise ValueError("ARTIFACT_MANIFEST：成功资产的缩略图未绑定")
        sha = a.get("sha256")
        if sha is not None and (not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
            raise ValueError("AUDIT_SCHEMA：媒体 SHA-256 无效")
        if a["decode_status"] == "ok" and (sha is None or type(a.get("byte_size")) is not int or a["byte_size"] <= 0):
            raise ValueError("AUDIT_SCHEMA：成功媒体需有非空字节与 SHA-256")
        normalized = normalize_text(a["text"])
        grams = sorted(text_ngrams(normalized, cfg["text_ngram"])) if len(normalized) >= cfg["text_min_chars"] else []
        if a.get("normalized_text") != normalized or a.get("normalized_text_sha256") != hashlib.sha256(normalized.encode()).hexdigest() or a.get("text_ngrams") != grams:
            raise ValueError("AUDIT_SCHEMA：文本特征与清单内容不一致")
    expected_input = digest([{k: a.get(k) for k in ("asset_id", "path", "split", "source_group", "media_type", "text", "sha256", "decode_status")} for a in assets])
    if expected_input != audit.get("input_digest") or any(result.get(k) != audit.get(k) for k in ("manifest_digest", "input_digest", "config_digest")):
        raise ValueError("AUDIT_BINDING_MISMATCH：输入或结果封套摘要不一致")
    template = read_json(artifacts["review_template.json"])
    if not isinstance(template, dict) or any(template.get(k) != audit.get(k) for k in ("manifest_digest", "input_digest", "config_digest")):
        raise ValueError("AUDIT_BINDING_MISMATCH：复核模板摘要不一致")
    provenance = audit.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("operation") != result["operation"]:
        raise ValueError("AUDIT_SCHEMA：审计操作来源不一致")
    if "pair_scan_completed" in audit and type(audit["pair_scan_completed"]) is not bool:
        raise ValueError("AUDIT_SCHEMA：pair_scan_completed 需为布尔值")
    if provenance["operation"] == "validate" and (audit.get("pair_scan_completed") or audit.get("candidate_edges") or audit.get("reviews")):
        raise ValueError("PAIR_SCAN_REQUIRED：validate 不应包含扫描或复核结果")
    validate_audit_edges(audit)
    summary = summarize(audit)
    if audit.get("summary") != summary or result.get("counts") != summary or read_json(artifacts["audit_summary.json"]) != summary:
        raise ValueError("AUDIT_SUMMARY_MISMATCH：摘要与实际资产及关系不一致")
    if audit.get("confirmed_components") != components(audit) or audit.get("possible_components") != components(audit, True):
        raise ValueError("AUDIT_SUMMARY_MISMATCH：关联组与实际关系不一致")
    return audit


def validated_reviews(rows, edges):
    if not isinstance(rows, list):
        raise ValueError("INVALID_REVIEWS：reviews 需为数组")
    seen, clean = set(), []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("INVALID_REVIEW：每项需为对象")
        pid, decision = r.get("pair_id"), r.get("decision")
        if not isinstance(pid, str) or pid in seen or pid not in edges or not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("INVALID_REVIEW：候选 ID 不存在/重复或 decision 非法")
        seen.add(pid)
        if not all(isinstance(r.get(k), str) and r[k].strip() for k in ("evidence", "reviewer", "origin")):
            raise ValueError("INVALID_REVIEW：每项需 evidence、reviewer、origin 非空字符串")
        if r["origin"] not in {"human_local", "construction_truth_fixture"}:
            raise ValueError("INVALID_REVIEW_ORIGIN：请明确 human_local 或 construction_truth_fixture")
        clean.append({k: r[k] for k in ("pair_id", "decision", "evidence", "reviewer", "origin")})
    return clean


def validate_audit_edges(audit):
    edges = audit.get("candidate_edges")
    if not isinstance(edges, list):
        raise ValueError("AUDIT_SCHEMA：candidate_edges 需为数组")
    by_id = {a["asset_id"]: a for a in audit["assets"]}
    seen = set()
    methods_allowed = {"sha256_exact", "declared_source_group", "canonical_pixels_exact", "image_dhash", "text_ngram_jaccard", "ordered_video_dhash", "manual"}
    for e in edges:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("pair_id", "asset_a", "asset_b")):
            raise ValueError("AUDIT_SCHEMA：候选关系需为合法对象")
        a, b = e["asset_a"], e["asset_b"]
        if a == b or a not in by_id or b not in by_id or e["pair_id"] != pair_id(a, b) or e["pair_id"] in seen:
            raise ValueError("AUDIT_SCHEMA：候选关系 ID、端点或唯一性无效")
        seen.add(e["pair_id"])
        if type(e.get("cross_split")) is not bool or e["cross_split"] != (by_id[a]["split"] != by_id[b]["split"]):
            raise ValueError("AUDIT_SCHEMA：候选跨集合标记不一致")
        reasons = e.get("reasons")
        if not isinstance(reasons, list) or not reasons or any(not isinstance(r, dict) or not isinstance(r.get("method"), str) or r["method"] not in methods_allowed for r in reasons):
            raise ValueError("AUDIT_SCHEMA：候选证据方法无效")
        methods = {r["method"] for r in reasons}
        expected = "exact_bytes" if "sha256_exact" in methods else "declared_source" if "declared_source_group" in methods else "candidate_only"
        if e.get("evidence_status") != expected:
            raise ValueError("AUDIT_SCHEMA：候选证据状态不一致")
        if "sha256_exact" in methods and (not by_id[a].get("sha256") or by_id[a]["sha256"] != by_id[b].get("sha256")):
            raise ValueError("AUDIT_SCHEMA：同字节证据与资产摘要不一致")
        if "declared_source_group" in methods and (not known_group(by_id[a]["source_group"]) or by_id[a]["source_group"] != by_id[b]["source_group"]):
            raise ValueError("AUDIT_SCHEMA：声明来源证据与资产不一致")
    validated_reviews(audit.get("reviews"), seen)
    if not isinstance(audit.get("failure_ledger"), list) or not isinstance(audit.get("warnings"), list) or not all(isinstance(w, str) for w in audit["warnings"]):
        raise ValueError("AUDIT_SCHEMA：失败 ledger 与 warnings 需为数组")
    failed = set()
    for entry in audit["failure_ledger"]:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("asset_id", "stage", "code", "message")):
            raise ValueError("AUDIT_SCHEMA：失败 ledger 条目无效")
        aid = entry["asset_id"]
        if aid not in by_id or aid in failed or by_id[aid]["decode_status"] != "failed":
            raise ValueError("AUDIT_SCHEMA：失败 ledger 的资产无效或重复")
        failed.add(aid)
    if failed != {aid for aid, a in by_id.items() if a["decode_status"] == "failed"}:
        raise ValueError("AUDIT_SCHEMA：失败资产未完整记入 ledger")


def import_reviews(directory, reviews_path, out):
    started = time.perf_counter()
    require_separate_output(out, [reviews_path], [directory])
    audit = load_audit(directory)
    require_pair_scan(audit)
    payload = read_json(reviews_path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "2.0":
        raise ValueError("REVIEW_SCHEMA：复核文件需 schema_version 2.0")
    for key in ("manifest_digest", "input_digest", "config_digest"):
        if payload.get(key) != audit[key]:
            raise ValueError(f"REVIEW_BINDING_MISMATCH：{key} 不匹配，未导入任何条目")
    asset_ids = {a["asset_id"] for a in audit["assets"]}
    by_id = {a["asset_id"]: a for a in audit["assets"]}
    edges = {e["pair_id"]: e for e in audit["candidate_edges"]}
    manual_pairs = payload.get("manual_pairs", [])
    if not isinstance(manual_pairs, list):
        raise ValueError("INVALID_MANUAL_PAIR：manual_pairs 需为数组")
    manual_seen = set()
    for manual in manual_pairs:
        if not isinstance(manual, dict):
            raise ValueError("INVALID_MANUAL_PAIR：每项需为对象")
        a, b = manual.get("asset_a"), manual.get("asset_b")
        if not isinstance(a, str) or not isinstance(b, str) or a not in asset_ids or b not in asset_ids or a == b or not isinstance(manual.get("reason"), str) or not manual["reason"].strip():
            raise ValueError("INVALID_MANUAL_PAIR：需不同的已存在 ID 和理由")
        pid = pair_id(a, b)
        if pid in manual_seen:
            raise ValueError("INVALID_MANUAL_PAIR：同批次手工候选不能重复")
        manual_seen.add(pid)
        if pid not in edges:
            edge = {"pair_id": pid, "asset_a": a, "asset_b": b, "cross_split": by_id[a]["split"] != by_id[b]["split"], "evidence_status": "candidate_only", "reasons": [{"method": "manual", "reason": manual["reason"]}]}
            audit["candidate_edges"].append(edge)
            edges[pid] = edge
    new_reviews = validated_reviews(payload.get("reviews"), edges)
    existing = {r["pair_id"]: r for r in audit["reviews"]}
    for r in new_reviews:
        existing[r["pair_id"]] = r
    if not new_reviews and not payload.get("manual_pairs"):
        raise ValueError("EMPTY_REVIEWS：没有复核或手工候选，不生成空版本")
    audit["reviews"] = sorted(existing.values(), key=lambda r: r["pair_id"])
    audit["provenance"]["previous_audit_sha256"] = sha256(Path(directory) / "audit.json")
    audit["provenance"]["review_file_sha256"] = sha256(reviews_path)
    audit["provenance"]["operation"] = "import-reviews"
    out = fresh_out(out)
    (out / "evidence").mkdir()
    for a in audit["assets"]:
        if a["decode_status"] == "ok":
            # Copy only verified, bound thumbnails, never arbitrary extra files.
            shutil.copyfile(Path(directory) / a["thumbnail"], out / a["thumbnail"])
    shutil.copyfile(Path(directory) / "manifest.snapshot.jsonl", out / "manifest.snapshot.jsonl")
    originals = [entry["path"] for entry in read_json(Path(directory) / "result.json")["artifacts"]
                 if entry["path"] in {"manifest.original.csv", "manifest.original.jsonl"}]
    for name in originals:
        shutil.copyfile(Path(directory) / name, out / name)
    return audit, save_audit(audit, out, "import-reviews", started)


def require_pair_scan(audit):
    # Support earlier audited artifacts, while rejecting validation-only outputs.
    scanned = audit.get("pair_scan_completed", audit["provenance"]["operation"] != "validate")
    if scanned is not True:
        raise ValueError("PAIR_SCAN_REQUIRED：validate 未扫描关系边；请先运行 audit，再导入复核或生成划分建议")


def propose_split(directory, out, seed=17, ratios=(0.7, 0.15, 0.15)):
    started = time.perf_counter()
    require_separate_output(out, directories=[directory])
    audit = load_audit(directory)
    require_pair_scan(audit)
    splits = audit["config"]["declared_splits"]
    if len(ratios) != len(splits) or any(not finite_number(r) or r <= 0 for r in ratios) or abs(sum(ratios) - 1) > 1e-6:
        raise ValueError("INVALID_RATIOS：比例需与 declared_splits 顺序一致、为正且总和为 1")
    groups = components(audit)
    rng = random.Random(seed)
    rng.shuffle(groups)
    groups.sort(key=len, reverse=True)
    n = len(audit["assets"])
    targets = {s: r * n for s, r in zip(splits, ratios)}
    assigned, assignment = Counter(), {}
    for group in groups:
        # Whole-group greedy least squared deficit, fixed seed for tie order.
        chosen = min(splits, key=lambda s: sum((assigned[t] + (len(group) if s == t else 0) - targets[t]) ** 2 for t in splits))
        for aid in group:
            assignment[aid] = (chosen, "group_" + digest(group)[:16])
        assigned[chosen] += len(group)
    evidence = defaultdict(set)
    for e in accepted_edges(audit):
        for aid in (e["asset_a"], e["asset_b"]):
            evidence[aid].update(e["grouping_evidence"])
    rows = [{"asset_id": a["asset_id"], "old_split": a["split"], "proposed_split": assignment[a["asset_id"]][0],
             "group_id": assignment[a["asset_id"]][1], "changed": a["split"] != assignment[a["asset_id"]][0],
             "evidence": ";".join(sorted(evidence[a["asset_id"]])) or "singleton_no_confirmed_edge",
             "status": "proposal_only"} for a in audit["assets"]]
    out = fresh_out(out)
    csv_write(out / "split_proposal.csv", rows, ["asset_id", "old_split", "proposed_split", "group_id", "changed", "evidence", "status"])
    write_json(out / "split_proposal.json", {"schema_version": "2.0", "input_digest": audit["input_digest"], "config_digest": audit["config_digest"],
               "audit_sha256": sha256(Path(directory) / "audit.json"), "seed": seed, "target_ratios": dict(zip(splits, ratios)),
               "actual_asset_counts": dict(assigned), "actual_asset_ratios": {s: assigned[s] / n for s in splits},
               "actual_group_counts": dict(Counter(assignment[g[0]][0] for g in groups)), "group_count": len(groups),
               "changed_assets": sum(r["changed"] for r in rows), "rows": rows,
               "limitations": "只约束已确认/精确字节/声明来源边；候选未全部复核。组内不保证两两重复；不保证类别平衡。"})
    result = write_result(out, audit, "propose-split", started)
    return rows, result


def build_demo(input_dir):
    input_dir = Path(input_dir)
    (input_dir / "media").mkdir(parents=True)
    assets, truth_families, truth_pairs = [], defaultdict(list), []
    splits = ["train", "calibration", "test"]
    prompts = ["红色纸鸟停在旧车站的木椅上", "蓝色玻璃花瓶装着三支黄色郁金香", "圆形茶杯放在绿色书本旁边", "白色台灯照亮紫色折纸船", "小猫坐在窗台看雨水落下", "两个人撑伞走过夜晚石桥", "橙色机械手臂搬起木头箱子", "银色电车穿过有树荫的街道", "一名画家在花园描绘喷泉", "紫色热气球飞过棕色山谷", "夕阳下的风车转动白色叶片", "游泳池边摆放粉色沙滩椅", "机器人把绿色苹果放进篮子", "蓝帽厨师正在厨房揉面团", "黄色挖掘机停在碎石路口", "几只海鸥围绕红色灯塔飞行", "雪地里的松树挂满金色铃铛", "水底乌龟游过深绿色水草", "木桌上的三个陶罐大小不同", "穿着雨靴的孩子站在池塘边", "黑色钢琴上摆着一束白花", "长颈鹿从围栏后探出脑袋", "粉色云朵映照着海边船帆", "黑白棋子散落在竹制棋盘上"]

    def add(aid, filename, split, declared_group, true_family, transform, media="image", text=""):
        assets.append({"asset_id": aid, "path": "media/" + filename, "split": split,
                       "source_group": declared_group, "media_type": media, "text": text,
                       "license_note": "CC0-1.0 self-drawn synthetic fixture", "provenance": "constructed_fixture:" + transform})
        truth_families[true_family].append((aid, transform))

    for family in range(24):
        rng = random.Random(7300 + family)
        im = Image.new("RGB", (192, 144), (235 + family % 20, 238, 230))
        d = ImageDraw.Draw(im)
        for shape in range(8):
            x, y = rng.randrange(5, 145), rng.randrange(5, 100)
            box = (x, y, x + rng.randrange(15, 45), y + rng.randrange(15, 40))
            color = tuple(rng.randrange(25, 205) for _ in range(3))
            (d.ellipse if shape % 2 else d.rectangle)(box, fill=color, outline="black", width=2)
        d.text((8, 126), f"DRAWING {family:02d}", fill="black")
        name = f"f{family:02d}"
        original = input_dir / "media" / f"{name}_original.png"
        im.save(original)
        shutil.copyfile(original, input_dir / "media" / f"{name}_renamed.png")
        im.save(input_dir / "media" / f"{name}_jpeg.jpg", quality=48)
        im.resize((96, 72), Image.Resampling.LANCZOS).save(input_dir / "media" / f"{name}_resize.png")
        im.crop((12, 8, 180, 136)).save(input_dir / "media" / f"{name}_crop.png")
        for j, (variant, ext) in enumerate([("original", "png"), ("renamed", "png"), ("jpeg", "jpg"), ("resize", "png"), ("crop", "png")]):
            # Half of families deliberately do not declare shared origin; detection must use media.
            group = name if family < 12 else "unknown"
            prompt = prompts[family]
            add(f"{name}_{variant}", f"{name}_{variant}.{ext}", splits[(family + j) % 3], group, name, variant, text=prompt)
    # Hard negatives deliberately share a silhouette and prompt but have distinct constructed origins.
    for j in range(18):
        im = Image.new("RGB", (192, 144), (242, 242, 235))
        d = ImageDraw.Draw(im)
        d.rectangle((45, 28, 147, 116), fill=(30 + j * 9, 80 + j * 6, 160 - j * 4), outline="black", width=3)
        d.ellipse((72, 50, 120, 98), fill=(235, 220, 80 + j * 7))
        d.text((12, 126), f"INDEPENDENT {j:02d}", fill="black")
        filename = f"hard_{j:02d}.png"
        im.save(input_dir / "media" / filename)
        add(f"hard_{j:02d}", filename, splits[j % 3], f"independent_{j}", f"hard_{j}", "independent_same_style", text="一幅方形画框中有一个圆形装饰，保持相同布局和简洁平面设计。")
    for family in range(4):
        frames = []
        for f in range(30):
            im = Image.new("RGB", (160, 120), (235, 230, 215))
            d = ImageDraw.Draw(im)
            d.rectangle((4, 4, 155, 115), outline="black", width=3)
            x = 8 + f * 4
            y = 20 + family * 15
            d.ellipse((x, y, x + 23, y + 23), fill=(40 + family * 35, 45, 180))
            d.line((15, 90 - family * 8, 140, 105 - family * 8), fill="black", width=4)
            frames.append(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR))
        for variant in ("original", "reencoded", "reversed"):
            filename = f"v{family}_{variant}.mp4"
            writer = cv2.VideoWriter(str(input_dir / "media" / filename), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
            if not writer.isOpened():
                raise RuntimeError("VIDEO_WRITER_UNAVAILABLE：本环境没有 mp4v 写入器")
            if variant == "reencoded":
                cap = cv2.VideoCapture(str(input_dir / "media" / f"v{family}_original.mp4"))
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    writer.write(frame)
                cap.release()
            else:
                for frame in reversed(frames) if variant == "reversed" else frames:
                    writer.write(frame)
            writer.release()
            add(f"v{family}_{variant}", filename, "train" if variant == "original" else "test", "unknown", f"v{family}", variant, "video")
    for family, items in truth_families.items():
        for (a, ta), (b, tb) in itertools.combinations(items, 2):
            # Include reverse as true source derivative even if chronological matcher correctly abstains.
            truth_pairs.append({"pair_id": pair_id(a, b), "asset_a": a, "asset_b": b, "label": "same_source_derivative", "transform_pair": "+".join(sorted([ta, tb])), "true_family": family, "origin": "construction_truth"})
    hard = [a["asset_id"] for a in assets if a["asset_id"].startswith("hard_")]
    negatives = [{"pair_id": pair_id(a, b), "asset_a": a, "asset_b": b, "label": "independent_same_style", "origin": "construction_truth"} for a, b in itertools.combinations(hard, 2)]
    manifest = input_dir / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in assets), encoding="utf-8")
    write_json(input_dir / "construction_truth.json", {"origin": "self_drawn_construction_truth_not_model_or_human", "positive_pairs": truth_pairs, "hard_negative_pairs": negatives, "total_assets": len(assets), "family_count": len(truth_families)})
    return manifest


def demo(out):
    out = fresh_out(out)
    started = time.perf_counter()
    manifest = build_demo(out / "input")
    audit, result = run_audit(manifest, out, operation="demo", allow_existing=True)
    truth = json.loads((out / "input/construction_truth.json").read_text(encoding="utf-8"))
    edges = {e["pair_id"]: e for e in audit["candidate_edges"]}
    by_transform = defaultdict(lambda: {"known_pairs": 0, "retrieved_pairs": 0, "media_candidate_pairs": 0})
    media_methods = {"sha256_exact", "canonical_pixels_exact", "image_dhash", "ordered_video_dhash"}
    for p in truth["positive_pairs"]:
        group = by_transform[p["transform_pair"]]
        group["known_pairs"] += 1
        group["retrieved_pairs"] += p["pair_id"] in edges
        group["media_candidate_pairs"] += p["pair_id"] in edges and any(r["method"] in media_methods for r in edges[p["pair_id"]]["reasons"])
    metrics = {"origin": truth["origin"], "known_source_positive_pairs": len(truth["positive_pairs"]),
               "retrieved_known_pairs": sum(p["pair_id"] in edges for p in truth["positive_pairs"]),
               "known_hard_negative_pairs": len(truth["hard_negative_pairs"]),
               "hard_negative_candidates": sum(p["pair_id"] in edges for p in truth["hard_negative_pairs"]),
               "by_transform": dict(by_transform), "not_estimated": ["natural-data recall", "human precision", "copyright", "training membership"],
               "interpretation": "所有已知同源 pair（包括未检索到的反序视频）均计分母。文本与声明命中可能掩盖媒体漏检，故另列 media_candidate_pairs。"}
    write_json(out / "construction_metrics.json", metrics)
    # Provide review examples, never silently import fabricated human opinions.
    positive = next(p for p in truth["positive_pairs"] if p["pair_id"] in edges)
    negative = next(p for p in truth["hard_negative_pairs"] if p["pair_id"] in edges)
    reviews = [{"pair_id": p["pair_id"], "decision": decision, "evidence": "构造脚本记录的" + label + "；非真人评分", "reviewer": "fixture_builder", "origin": "construction_truth_fixture"} for p, decision, label in [(positive, "related", "同源变换"), (negative, "distinct", "独立绘制反例")]]
    write_json(out / "fixture_reviews.json", {"schema_version": "2.0", **{k: audit[k] for k in ("manifest_digest", "input_digest", "config_digest")}, "reviews": reviews, "manual_pairs": []})
    result = write_result(out, audit, "demo", started)
    return audit, result


def main(argv=None):
    parser = argparse.ArgumentParser(description="数据来源泄漏审计：候选与确认分离，不移动/删除素材")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "audit"):
        p = sub.add_parser(command)
        p.add_argument("--manifest", required=True)
        p.add_argument("--config")
        p.add_argument("--out", required=True)
    p = sub.add_parser("demo")
    p.add_argument("--out", required=True)
    p = sub.add_parser("import-reviews")
    p.add_argument("--audit", required=True)
    p.add_argument("--reviews", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("propose-split")
    p.add_argument("--audit", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--ratios", default="0.7,0.15,0.15")
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        output_was_fresh = not out.is_symlink() and (not out.exists() or (out.is_dir() and not any(out.iterdir())))
    except OSError:
        output_was_fresh = False
    try:
        if args.command == "demo":
            _, result = demo(args.out)
        elif args.command in {"validate", "audit"}:
            _, result = run_audit(args.manifest, args.out, args.config, args.command)
        elif args.command == "import-reviews":
            _, result = import_reviews(args.audit, args.reviews, args.out)
        else:
            _, result = propose_split(args.audit, args.out, args.seed, tuple(float(x) for x in args.ratios.split(",")))
        print(json.dumps({"status": result["status"], "quality": result["quality"], "counts": result["counts"], "result": str(Path(args.out) / "result.json"), "report": str(Path(args.out) / "review.html") if args.command != "propose-split" else str(Path(args.out) / "split_proposal.csv")}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, csv.Error, sqlite3.Error) as exc:
        # Fresh invalid invocations still produce a machine-readable envelope.
        # Never replace an existing result, including failed partial output.
        if output_was_fresh and not str(exc).startswith("INPUT_OUTPUT_OVERLAP") and not (out / "result.json").exists():
            try:
                out.mkdir(parents=True, exist_ok=True)
                write_json(out / "result.json", {"schema_version": "2.0", "project_id": "dataset-leakage-audit", "operation": args.command,
                           "status": "failed" if isinstance(exc, (OSError, RuntimeError, sqlite3.Error)) else "blocked",
                           "quality": {"verdict": "not_assessed", "meaning": "输入或运行未满足要求，未做质量判断"},
                           "input_digest": None, "config_digest": None, "evaluator_version": VERSION,
                           "artifacts": [], "counts": {}, "warnings": [str(exc)]})
            except OSError:
                pass  # The original error remains reportable even without writable output.
        print(f"未完成：{exc}\n请修正该项后使用新的输出目录重试。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
