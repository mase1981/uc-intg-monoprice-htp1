import json
from pathlib import Path

import pytest

from tools.build_beq_catalogue import build
from tools.validate_beq_catalogue import validate


def _catalogue(tmp_path: Path) -> Path:
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    source.write_text(json.dumps([
        {
            "title": "Playable title",
            "underlying": "Playable title (2024)",
            "author": "Author",
            "year": "2024",
            "audioTypes": ["Atmos"],
            "content_type": "film",
            "filters": [{"type": "PeakingEQ", "freq": 30, "gain": 3, "q": 1}],
        },
        {"title": "No filters", "content_type": "film", "filters": []},
    ]), encoding="utf-8")
    build(source, output)
    return output


def test_validate_accepts_complete_catalogue_and_checks_payloads(tmp_path: Path):
    result = validate(_catalogue(tmp_path))
    assert result["entry_count"] == 1
    assert result["filterless_count"] == 1
    assert result["unsupported_count"] == 0
    assert result["payload_files_checked"] == 1


def test_validate_rejects_index_checksum_mismatch(tmp_path: Path):
    root = _catalogue(tmp_path)
    index_path = root / json.loads((root / "manifest.json").read_text())["index"]
    index_path.write_text("{}")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate(root)


def test_validate_rejects_category_offsets_that_do_not_cover_entries(tmp_path: Path):
    root = _catalogue(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    index_path = root / manifest["index"]
    index = json.loads(index_path.read_text())
    index["categories"] = {}
    data = json.dumps(index, separators=(",", ":"), sort_keys=True).encode()
    index_path.write_bytes(data)
    manifest["index_sha256"] = __import__("hashlib").sha256(data).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="do not cover"):
        validate(root)


def test_validate_rejects_report_count_mismatch(tmp_path: Path):
    root = _catalogue(tmp_path)
    report_path = root / "filterless-entries.json"
    report = json.loads(report_path.read_text())
    report["count"] = 99
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="report count"):
        validate(root)


def test_validate_rejects_missing_entry_payload(tmp_path: Path):
    root = _catalogue(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    index = json.loads((root / manifest["index"]).read_text())
    (root / manifest["version"] / "entries" / f"{index['entries'][0]['id']}.json").unlink()
    with pytest.raises(ValueError, match="payload files do not match"):
        validate(root)
