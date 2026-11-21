import hashlib
import json
from pathlib import Path

import pytest

from tools.build_beq_catalogue import build


def test_build_writes_compact_index_and_on_demand_entries(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    source.write_text(json.dumps([{
        "title": "Example Film",
        "underlying": "Example Film (2024)",
        "author": "Author",
        "year": 2024,
        "audioTypes": ["Atmos"],
        "content_type": "film",
        "filters": [{
            "type": "PeakingEQ", "freq": 20, "gain": 4.5, "q": 1.2,
            "biquads": {"96000": {"b": ["1"], "a": ["1"]}},
        }],
    }]), encoding="utf-8")

    manifest = build(source, output)
    assert manifest["entry_count"] == 1
    assert manifest["schema_version"] == 1

    index = json.loads((output / manifest["index"]).read_text(encoding="utf-8"))
    assert index["categories"] == {"film": [0]}
    assert index["entries"][0]["title"] == "Example Film"
    assert index["entries"][0]["author"] == "Author"
    assert "content_type" not in index["entries"][0]
    assert "search_title" not in index["entries"][0]
    legacy_id_material = {
        "title": "Example Film",
        "underlying": "Example Film (2024)",
        "filters": [{"type": "PeakingEQ", "freq": 20, "gain": 4.5, "q": 1.2}],
    }
    expected_id = hashlib.md5(json.dumps(
        legacy_id_material, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()[:16]
    assert index["entries"][0]["id"] == expected_id
    assert "filters" not in index["entries"][0]

    payload = json.loads(
        (output / manifest["version"] / "entries" /
         f"{index['entries'][0]['id']}.json").read_text(encoding="utf-8")
    )
    assert payload["underlying"] == "Example Film (2024)"
    assert "biquads" not in payload["filters"][0]
    assert payload["filters"] == [{
        "type": "PeakingEQ", "freq": 20, "gain": 4.5, "q": 1.2,
    }]


@pytest.mark.parametrize("records", [[], {}, [None], [{"title": "Invalid filters", "filters": {}}]])
def test_rejects_invalid_source_records(tmp_path: Path, records):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        build(source, tmp_path / "public")


def test_identical_filter_records_keep_legacy_duplicate_ids(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    record = {
        "title": "Same Film",
        "underlying": "same-film",
        "author": "Author A",
        "year": "2024",
        "audioTypes": ["Atmos"],
        "content_type": "film",
        "filters": [{"type": "PeakingEQ", "freq": 30, "gain": 3, "q": 1}],
    }
    second = {**record, "author": "Author B"}
    source.write_text(json.dumps([record, second]), encoding="utf-8")

    manifest = build(source, output)
    index = json.loads((output / manifest["index"]).read_text(encoding="utf-8"))
    assert manifest["entry_count"] == 2
    assert index["entries"][0]["id"] == index["entries"][1]["id"]
    assert [item["author"] for item in index["entries"]] == ["Author A", "Author B"]



def test_filterless_records_are_excluded_and_written_to_downloadable_report(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    source.write_text(json.dumps([{
        "title": "8mm",
        "underlying": "8mm",
        "author": "",
        "year": "",
        "audioTypes": [],
        "content_type": "film",
    }]), encoding="utf-8")

    manifest = build(source, output)
    index = json.loads((output / manifest["index"]).read_text(encoding="utf-8"))
    assert manifest["entry_count"] == 0
    assert manifest["filterless_count"] == 1
    assert index["entries"] == []
    assert index["categories"] == {}

    report = json.loads((output / "filterless-entries.json").read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["entries"] == [{
        "title": "8mm", "author": "", "year": "", "content_type": "film",
    }]
    text_report = (output / "filterless-entries.txt").read_text(encoding="utf-8")
    assert "Excluded entries: 1" in text_report
    assert "8mm [film]" in text_report



def test_unsupported_types_and_excess_bands_are_reported_but_limits_are_normalized(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    records = [
        {
            "title": "Supported boundary values",
            "author": "A",
            "content_type": "film",
            "filters": [
                {"type": "PeakingEQ", "freq": 10, "gain": -99, "q": 0.1},
                {"type": "HighShelf", "freq": 20000, "gain": 20, "q": 10},
            ],
        },
        {
            "title": "Below BEQ minimum frequency",
            "author": "B",
            "content_type": "film",
            "filters": [{"type": "PeakingEQ", "freq": 9, "gain": 0, "q": 1}],
        },
        {
            "title": "Unsupported filter type",
            "author": "C",
            "content_type": "film",
            "filters": [{"type": "AllPass1", "freq": 100, "gain": 0, "q": 1}],
        },
        {
            "title": "Too many bands",
            "author": "D",
            "content_type": "film",
            "filters": [
                {"type": "PeakingEQ", "freq": 100, "gain": 0, "q": 1}
                for _ in range(17)
            ],
        },
    ]
    source.write_text(json.dumps(records), encoding="utf-8")

    manifest = build(source, output)
    index = json.loads((output / manifest["index"]).read_text(encoding="utf-8"))
    unsupported = json.loads(
        (output / manifest["unsupported_report"]).read_text(encoding="utf-8")
    )
    adjusted = json.loads(
        (output / manifest["adjusted_report"]).read_text(encoding="utf-8")
    )
    payload_path = output / manifest["version"] / "entries" / f"{index['entries'][0]['id']}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    assert manifest["entry_count"] == 2
    assert manifest["unsupported_count"] == 2
    assert manifest["adjusted_count"] == 1
    assert [entry["title"] for entry in index["entries"]] == [
        "Below BEQ minimum frequency",
        "Supported boundary values",
    ]
    assert [entry["title"] for entry in unsupported["entries"]] == [
        "Too many bands",
        "Unsupported filter type",
    ]
    assert payload["filters"][0]["freq"] == 10
    assert adjusted["entries"][0]["adjustments"] == [{
        "filter_index": 0,
        "type": "PeakingEQ",
        "field": "freq",
        "original": 9,
        "applied": 10,
    }]
    assert "filter type not supported by integration: AllPass1" in str(unsupported)
    assert "more than 16 filters per channel" in str(unsupported)
    assert (output / "unsupported-entries.txt").exists()
    assert (output / "adjusted-entries.txt").exists()


@pytest.mark.parametrize(("filter_value", "expected"), [
    ({"type": "PeakingEQ", "freq": 9, "gain": 0, "q": 1}, {"freq": 10, "gain": 0, "q": 1}),
    ({"type": "PeakingEQ", "freq": 100, "gain": 25.7, "q": 1}, {"freq": 100, "gain": 20, "q": 1}),
    ({"type": "PeakingEQ", "freq": 100, "gain": 0, "q": 11}, {"freq": 100, "gain": 0, "q": 10}),
    ({"type": "PeakingEQ", "freq": 100, "gain": -100, "q": 0.05}, {"freq": 100, "gain": -99, "q": 0.1}),
])
def test_catalogue_builder_normalizes_values_to_beq_limits(
    tmp_path: Path, filter_value, expected
):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    source.write_text(json.dumps([{
        "title": "Adjusted filter",
        "content_type": "film",
        "filters": [filter_value],
    }]), encoding="utf-8")

    manifest = build(source, output)
    index = json.loads((output / manifest["index"]).read_text(encoding="utf-8"))
    payload = json.loads(
        (output / manifest["version"] / "entries" / f"{index['entries'][0]['id']}.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["entry_count"] == 1
    assert manifest["unsupported_count"] == 0
    assert manifest["adjusted_count"] == 1
    assert {key: payload["filters"][0][key] for key in ("freq", "gain", "q")} == expected



def test_unsupported_text_report_uses_real_line_breaks(tmp_path: Path):
    source = tmp_path / "source.json"
    output = tmp_path / "public"
    source.write_text(json.dumps([{
        "title": "Unsupported filter",
        "content_type": "film",
        "filters": [{"type": "AllPass1", "freq": 100, "gain": 0, "q": 1}],
    }]), encoding="utf-8")

    build(source, output)
    report = (output / "unsupported-entries.txt").read_text(encoding="utf-8")
    assert "\\n" not in report
    assert "Excluded entries: 1\n\nUnsupported filter: filter 1:" in report


def test_reset_output_directory_does_not_follow_symlink(tmp_path: Path):
    from tools.build_beq_catalogue import _reset_output_directory

    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    output_link = tmp_path / "output"
    output_link.symlink_to(target, target_is_directory=True)

    _reset_output_directory(output_link)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not output_link.is_symlink()
    assert output_link.is_dir()


def test_reset_output_directory_refuses_current_working_directory(tmp_path: Path, monkeypatch):
    from tools.build_beq_catalogue import _reset_output_directory

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unsafe output directory"):
        _reset_output_directory(Path("."))
