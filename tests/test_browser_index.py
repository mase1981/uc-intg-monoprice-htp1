import asyncio
import time
from types import SimpleNamespace

from intg_monoprice_htp1 import browser


def _seed_index(monkeypatch):
    entries = [
        {"id": f"{index:016x}", "title": title, "author": "", "year": "2024",
         "audioTypes": []}
        for index, title in enumerate(["Alpha", "Alpine", "Alto", "Beta"])
    ]
    monkeypatch.setattr(browser, "_index", {
        "entries": entries,
        "categories": {"film": [0, 1, 2]},
    })
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})
    monkeypatch.setattr(browser, "_index_loaded_at", time.monotonic())


def test_search_respects_requested_page_and_limit(monkeypatch):
    _seed_index(monkeypatch)
    options = SimpleNamespace(
        query="al",
        paging=SimpleNamespace(page=2, limit=2),
    )
    result = asyncio.run(browser.search(None, options))
    assert [item.title for item in result.media] == ["Alto"]
    assert result.pagination.page == 2
    assert result.pagination.limit == 2
    assert result.pagination.count == 3


def test_category_browse_uses_precomputed_offsets_and_limit(monkeypatch):
    _seed_index(monkeypatch)
    result = asyncio.run(browser._browse_category("film", page=2, limit=2))
    assert [item.title for item in result.media.items] == ["Alto"]
    assert result.pagination.page == 2
    assert result.pagination.limit == 2
    assert result.pagination.count == 3


def test_search_matches_author(monkeypatch):
    _seed_index(monkeypatch)
    browser._index["entries"][0]["author"] = "Preferred Author"
    options = SimpleNamespace(query="preferred", paging=SimpleNamespace(page=1, limit=10))
    result = asyncio.run(browser.search(None, options))
    assert [item.title for item in result.media] == ["Alpha Preferred Author"]


def test_search_returns_author_in_display_title(monkeypatch):
    _seed_index(monkeypatch)
    browser._index["entries"][0]["author"] = "Preferred Author"
    options = SimpleNamespace(query="alpha", paging=SimpleNamespace(page=1, limit=10))
    result = asyncio.run(browser.search(None, options))
    assert [item.title for item in result.media] == ["Alpha Preferred Author"]

import pytest


@pytest.mark.parametrize("index", [
    {"entries": [{"id": "0000000000000001", "title": 7, "author": "", "year": "", "audioTypes": []}],
     "categories": {"film": [0]}},
    {"entries": [{"id": "0000000000000001", "title": "Film", "author": "", "year": "", "audioTypes": "Atmos"}],
     "categories": {"film": [0]}},
    {"entries": [{"id": "0000000000000001", "title": "Film", "author": "", "year": "", "audioTypes": []}],
     "categories": {"film": [True]}},
    {"entries": [{"id": "0000000000000001", "title": "Film", "author": "", "year": "", "audioTypes": []}],
     "categories": {"film": []}},
    {"entries": [{"id": "0000000000000001", "title": "Film", "author": "", "year": "", "audioTypes": []}],
     "categories": {"film": [0], "tv": [0]}},
])
def test_index_validation_rejects_malformed_metadata_and_offsets(index):
    index = {
        "schema_version": 1,
        "version": "0123456789abcdef",
        **index,
    }
    with pytest.raises(ValueError):
        browser._validate_index(index, "0123456789abcdef")


def test_index_validation_accepts_empty_catalogue():
    browser._validate_index({
        "schema_version": 1,
        "version": "0123456789abcdef",
        "entries": [],
        "categories": {},
    }, "0123456789abcdef")


def test_entry_cache_is_bounded_lru(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(browser, "_entry_cache", OrderedDict())
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})

    async def load_index():
        return True

    calls = []

    async def fetch(version, key):
        calls.append(key)
        return {"title": key, "underlying": key, "filters": [{"type": "PeakingEQ"}]}

    monkeypatch.setattr(browser, "_load_index", load_index)
    monkeypatch.setattr(browser, "_fetch_entry_payload", fetch)

    async def exercise():
        for index in range(browser.ENTRY_CACHE_MAX):
            await browser.get_beq_entry(f"{index:016x}")
        # A cache hit promotes the oldest entry to most-recently-used.
        await browser.get_beq_entry("0000000000000000")
        await browser.get_beq_entry(f"{browser.ENTRY_CACHE_MAX:016x}")

    asyncio.run(exercise())

    assert len(browser._entry_cache) == browser.ENTRY_CACHE_MAX
    assert "0000000000000000" in browser._entry_cache
    assert "0000000000000001" not in browser._entry_cache
    assert len(calls) == browser.ENTRY_CACHE_MAX + 1


def test_entry_payload_validation_enforces_htp1_peq_ranges(monkeypatch):
    import pytest

    invalid_filters = [
        {"type": "PeakingEQ", "freq": 9.9, "gain": 0, "q": 1},
        {"type": "PeakingEQ", "freq": 20000.1, "gain": 0, "q": 1},
        {"type": "PeakingEQ", "freq": 100, "gain": -99.1, "q": 1},
        {"type": "PeakingEQ", "freq": 100, "gain": 20.1, "q": 1},
        {"type": "PeakingEQ", "freq": 100, "gain": 0, "q": 0.09},
        {"type": "PeakingEQ", "freq": 100, "gain": 0, "q": 10.1},
    ]

    async def exercise():
        for filt in invalid_filters:
            async def fetch(url, filt=filt):
                return {"title": "Film", "underlying": "Film", "filters": [filt]}

            monkeypatch.setattr(browser, "_get_json", fetch)
            with pytest.raises(ValueError):
                await browser._fetch_entry_payload("0123456789abcdef", "0000000000000001")

        async def valid_fetch(url):
            return {
                "title": "Film",
                "underlying": "Film",
                "filters": [{"type": "PeakingEQ", "freq": 10, "gain": -99, "q": 0.1}],
            }

        monkeypatch.setattr(browser, "_get_json", valid_fetch)
        payload = await browser._fetch_entry_payload("0123456789abcdef", "0000000000000001")
        assert payload["filters"][0]["freq"] == 10

    asyncio.run(exercise())


def test_entry_fetch_retries_after_catalogue_version_changes(monkeypatch):
    import aiohttp
    from collections import OrderedDict

    monkeypatch.setattr(browser, "_entry_cache", OrderedDict())
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})
    load_calls = 0
    fetched_versions = []

    async def load_index():
        nonlocal load_calls
        load_calls += 1
        if load_calls == 2:
            browser._manifest = {"version": "fedcba9876543210"}
        return True

    async def fetch(version, key):
        fetched_versions.append(version)
        if version == "0123456789abcdef":
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=404
            )
        return {"title": "Film", "underlying": "Film", "filters": [
            {"type": "PeakingEQ", "freq": 20, "gain": 3, "q": 1}
        ]}

    monkeypatch.setattr(browser, "_load_index", load_index)
    monkeypatch.setattr(browser, "_fetch_entry_payload", fetch)

    result = asyncio.run(browser.get_beq_entry("0123456789abcdef"))

    assert result["title"] == "Film"
    assert fetched_versions == ["0123456789abcdef", "fedcba9876543210"]





def test_concurrent_entry_requests_share_one_payload_fetch(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(browser, "_entry_cache", OrderedDict())
    monkeypatch.setattr(browser, "_entry_locks", {})
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})
    calls = 0

    async def load_index():
        return True

    async def fetch(version, key):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"title": "Film", "underlying": "Film", "filters": [
            {"type": "PeakingEQ", "freq": 100, "gain": 2, "q": 1}
        ]}

    monkeypatch.setattr(browser, "_load_index", load_index)
    monkeypatch.setattr(browser, "_fetch_entry_payload", fetch)

    async def exercise():
        results = await asyncio.gather(*[
            browser.get_beq_entry("0123456789abcdef") for _ in range(8)
        ])
        assert all(result == results[0] for result in results)
        assert calls == 1

    asyncio.run(exercise())


def test_entry_fetch_failure_does_not_poison_cache(monkeypatch):
    import aiohttp
    from collections import OrderedDict

    monkeypatch.setattr(browser, "_entry_cache", OrderedDict())
    monkeypatch.setattr(browser, "_entry_locks", {})
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})
    attempts = 0

    async def load_index():
        return True

    async def fetch(version, key):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.TimeoutError()
        return {"title": "Film", "underlying": "Film", "filters": [
            {"type": "PeakingEQ", "freq": 100, "gain": 2, "q": 1}
        ]}

    monkeypatch.setattr(browser, "_load_index", load_index)
    monkeypatch.setattr(browser, "_fetch_entry_payload", fetch)

    async def exercise():
        assert await browser.get_beq_entry("0123456789abcdef") is None
        assert "0123456789abcdef" not in browser._entry_cache
        assert (await browser.get_beq_entry("0123456789abcdef"))["title"] == "Film"
        assert attempts == 2

    asyncio.run(exercise())


def test_index_refresh_failure_uses_last_known_good_index(monkeypatch):
    monkeypatch.setattr(browser, "_index", {
        "entries": [{"id": "0123456789abcdef", "title": "Film", "author": "",
                     "year": "", "audioTypes": []}],
        "categories": {"film": [0]},
    })
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})
    monkeypatch.setattr(browser, "_index_loaded_at", 0)

    async def fail_fetch(url):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(browser, "_get_json", fail_fetch)

    async def exercise():
        assert await browser._load_index()
        assert browser._index["entries"][0]["title"] == "Film"

    asyncio.run(exercise())



def test_old_inflight_payload_is_not_cached_after_version_change(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(browser, "_entry_cache", OrderedDict())
    monkeypatch.setattr(browser, "_entry_locks", {})
    monkeypatch.setattr(browser, "_manifest", {"version": "0123456789abcdef"})

    async def load_index():
        return True

    async def fetch(version, key):
        assert version == "0123456789abcdef"
        # Simulate a different request refreshing the catalogue while this
        # older version's payload request is still in flight.
        browser._manifest = {"version": "fedcba9876543210"}
        return {"title": "Old Film", "underlying": "Old Film", "filters": [
            {"type": "PeakingEQ", "freq": 100, "gain": 2, "q": 1}
        ]}

    monkeypatch.setattr(browser, "_load_index", load_index)
    monkeypatch.setattr(browser, "_fetch_entry_payload", fetch)

    async def exercise():
        result = await browser.get_beq_entry("0123456789abcdef")
        assert result["title"] == "Old Film"
        assert "0123456789abcdef" not in browser._entry_cache

    asyncio.run(exercise())



def test_new_session_uses_original_remote_tls_setting(monkeypatch):
    connector_options = {}
    session_options = {}
    connector = object()

    def fake_connector(**kwargs):
        connector_options.update(kwargs)
        return connector

    def fake_session(**kwargs):
        session_options.update(kwargs)
        return object()

    monkeypatch.setattr(browser.aiohttp, "TCPConnector", fake_connector)
    monkeypatch.setattr(browser.aiohttp, "ClientSession", fake_session)

    browser._new_session()

    assert connector_options == {"ssl": False}
    assert session_options == {
        "connector": connector,
        "timeout": browser.REQUEST_TIMEOUT,
    }
