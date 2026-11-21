#!/usr/bin/env python3
"""Validate the complete generated BEQ catalogue directory structure."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_VERSION_RE = re.compile(r"^[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPORTS = {
    "filterless": ("filterless_count", "filterless_report"),
    "unsupported": ("unsupported_count", "unsupported_report"),
    "adjusted": ("adjusted_count", "adjusted_report"),
}
_FILTER_TYPES = {"PeakingEQ", "LowShelf", "HighShelf"}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"manifest has invalid {label}")
    return value


def validate(root: Path) -> dict[str, Any]:
    """Validate manifest, reports, index, category offsets, and every entry payload."""
    root = root.resolve()
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported manifest schema")
    version = manifest.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ValueError("manifest has invalid catalogue version")
    source_sha = manifest.get("source_sha256")
    if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
        raise ValueError("manifest has invalid source SHA-256")
    if version != source_sha[:16]:
        raise ValueError("catalogue version does not match source SHA-256")

    index_rel = manifest.get("index")
    if index_rel != f"{version}/index.json":
        raise ValueError("manifest index path does not match catalogue version")
    index_path = root / index_rel
    index_bytes = index_path.read_bytes()
    expected_index_sha = manifest.get("index_sha256")
    if not isinstance(expected_index_sha, str) or not _SHA256_RE.fullmatch(expected_index_sha):
        raise ValueError("manifest has invalid index SHA-256")
    if hashlib.sha256(index_bytes).hexdigest() != expected_index_sha:
        raise ValueError("catalogue index checksum mismatch")
    try:
        index = json.loads(index_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"catalogue index is not valid JSON: {exc}") from exc
    if (not isinstance(index, dict) or index.get("schema_version") != SCHEMA_VERSION
            or index.get("version") != version):
        raise ValueError("catalogue index schema/version mismatch")
    entries = index.get("entries")
    categories = index.get("categories")
    if not isinstance(entries, list) or not isinstance(categories, dict):
        raise ValueError("catalogue index entries/categories have invalid types")
    if len(entries) != _count(manifest.get("entry_count"), "entry_count"):
        raise ValueError("manifest entry_count does not match index")

    categorized: set[int] = set()
    for category, offsets in categories.items():
        if not isinstance(category, str) or not category or not isinstance(offsets, list):
            raise ValueError("invalid category or category offset list")
        for offset in offsets:
            if (isinstance(offset, bool) or not isinstance(offset, int)
                    or offset < 0 or offset >= len(entries) or offset in categorized):
                raise ValueError("category offsets contain an invalid or duplicate index")
            categorized.add(offset)
    if len(categorized) != len(entries):
        raise ValueError("category offsets do not cover every entry")

    entry_by_id: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"index entry {position} is not an object")
        entry_id = item.get("id")
        if (not isinstance(entry_id, str) or not re.fullmatch(r"[0-9a-f]{16}", entry_id)
                or not isinstance(item.get("title"), str) or not item["title"].strip()
                or not isinstance(item.get("author"), str)
                or not isinstance(item.get("year"), str)
                or not isinstance(item.get("audioTypes"), list)
                or any(not isinstance(value, str) for value in item["audioTypes"])):
            raise ValueError(f"index entry {position} has invalid metadata")
        entry_by_id.setdefault(entry_id, item)

    entry_ids = set(entry_by_id)
    entries_dir = root / version / "entries"
    actual_payloads = {path.name for path in entries_dir.glob("*.json")} if entries_dir.is_dir() else set()
    expected_payloads = {f"{entry_id}.json" for entry_id in entry_ids}
    if actual_payloads != expected_payloads:
        raise ValueError("entry payload files do not match the index")
    for entry_id in entry_ids:
        payload_path = entries_dir / f"{entry_id}.json"
        payload = _read_json(payload_path)
        if (not isinstance(payload, dict) or not isinstance(payload.get("title"), str)
                or not isinstance(payload.get("underlying"), str)
                or not isinstance(payload.get("filters"), list)):
            raise ValueError(f"entry payload {entry_id} has invalid structure")
        if payload["title"] != entry_by_id[entry_id]["title"]:
            raise ValueError(f"entry payload {entry_id} title does not match index")
        if len(payload["filters"]) > 16:
            raise ValueError(f"entry payload {entry_id} exceeds the 16-filter limit")
        for filter_index, item in enumerate(payload["filters"]):
            if not isinstance(item, dict) or set(item) != {"type", "freq", "gain", "q"}:
                raise ValueError(f"entry payload {entry_id} filter {filter_index} has invalid fields")
            if item["type"] not in _FILTER_TYPES:
                raise ValueError(f"entry payload {entry_id} has unsupported filter type")
            for field, lower, upper in (("freq", 10, 20000), ("gain", -99, 20), ("q", 0.1, 10)):
                value = item[field]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not lower <= value <= upper):
                    raise ValueError(f"entry payload {entry_id} has invalid {field}")

    for report_name, (count_key, path_key) in _REPORTS.items():
        expected_path = f"{report_name}-entries.json"
        if manifest.get(path_key) != expected_path:
            raise ValueError(f"manifest has invalid {report_name} report path")
        report = _read_json(root / expected_path)
        if (not isinstance(report, dict) or report.get("source_version") != version
                or not isinstance(report.get("entries"), list)):
            raise ValueError(f"{report_name} report has invalid structure or version")
        report_count = report.get("count")
        if (isinstance(report_count, bool) or not isinstance(report_count, int)
                or report_count != len(report["entries"])):
            raise ValueError(f"{report_name} report count does not match its entries")
        if len(report["entries"]) != _count(manifest.get(count_key), count_key):
            raise ValueError(f"manifest {count_key} does not match report")
        text_path = root / f"{report_name}-entries.txt"
        if not text_path.is_file() or text_path.stat().st_size == 0:
            raise ValueError(f"{report_name} text report is missing or empty")

    return {
        "version": version,
        "source_sha256": source_sha,
        "entry_count": len(entries),
        "filterless_count": manifest["filterless_count"],
        "unsupported_count": manifest["unsupported_count"],
        "adjusted_count": manifest["adjusted_count"],
        "payload_files_checked": len(entry_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", type=Path, help="Generated catalogue directory")
    args = parser.parse_args()
    print(json.dumps(validate(args.catalogue), indent=2))


if __name__ == "__main__":
    main()
