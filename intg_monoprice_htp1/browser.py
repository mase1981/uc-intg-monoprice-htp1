"""
Monoprice HTP-1 media browser for BEQ catalogue.

Two-phase loading to minimize memory usage on the Remote:
- Phase 1 (startup): Download catalogue, keep only browse metadata in memory (~20MB),
  write filter data to disk (~5MB file).
- Phase 2 (on BEQ select): Read filter data from disk on demand.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from ucapi import StatusCodes
from ucapi.api_definitions import Pagination
from ucapi.media_player import (
    BrowseMediaItem,
    BrowseOptions,
    BrowseResults,
    MediaClass,
    SearchMediaItem,
    SearchOptions,
    SearchResults,
)

if TYPE_CHECKING:
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

BEQ_DB_URL = "https://beqcatalogue.readthedocs.io/en/latest/database.json"
ITEMS_PER_PAGE = 50
BEQ_CACHE_LIFE = 86400
BEQ_FILTERS_FILE = "beq_filters.json"
DOWNLOAD_CHUNK_SIZE = 16384

_data_dir: Path | None = None
_beq_cache: list[dict] | None = None
_beq_cache_timestamp: int | None = None
_beq_lookup: dict[str, str] = {}
_beq_fetching: asyncio.Lock = asyncio.Lock()
_beq_refresh_task: asyncio.Task | None = None


def init(data_dir: str | Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)
    _data_dir.mkdir(parents=True, exist_ok=True)
    _LOG.info("BEQ browser data directory: %s", _data_dir)


def _filters_path() -> Path:
    return (_data_dir or Path(".")) / BEQ_FILTERS_FILE


def _raw_catalogue_path() -> Path:
    return (_data_dir or Path(".")) / "beq_catalogue_raw.json"


async def prefetch_catalogue() -> None:
    await _fetch_beq_catalogue()


async def start_refresh_loop() -> None:
    global _beq_refresh_task
    if _beq_refresh_task and not _beq_refresh_task.done():
        return
    _beq_refresh_task = asyncio.create_task(_refresh_loop())


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(BEQ_CACHE_LIFE)
        _LOG.info("Scheduled BEQ catalogue refresh")
        await _fetch_beq_catalogue()


def _compute_entry_hash(title: str, underlying: str, filters: list[dict]) -> str:
    compact = {
        "title": title or "Unknown",
        "underlying": underlying or "",
        "filters": [{k: v for k, v in f.items() if k != "biquads"} for f in filters],
    }
    return hashlib.md5(
        json.dumps(compact, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()[:16]


async def _fetch_beq_catalogue() -> list[dict]:
    global _beq_cache, _beq_cache_timestamp, _beq_lookup

    if _beq_cache is not None:
        if int(time.time()) - _beq_cache_timestamp < BEQ_CACHE_LIFE:
            return _beq_cache

    if _beq_fetching.locked():
        _LOG.debug("BEQ catalogue fetch already in progress, waiting...")
        async with _beq_fetching:
            return _beq_cache if _beq_cache is not None else []

    async with _beq_fetching:
        if _beq_cache is not None:
            if int(time.time()) - _beq_cache_timestamp < BEQ_CACHE_LIFE:
                return _beq_cache

        raw_file = _raw_catalogue_path()
        try:
            _LOG.info("Downloading BEQ catalogue from %s", BEQ_DB_URL)
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(BEQ_DB_URL, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status != 200:
                        _LOG.error("BEQ catalogue fetch failed: %d", resp.status)
                        return []
                    with open(raw_file, "wb") as f:
                        async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                            f.write(chunk)

            _LOG.info("BEQ catalogue downloaded, processing...")
            slim_cache, lookup = _process_catalogue(raw_file)

            _beq_cache = slim_cache
            _beq_cache_timestamp = int(time.time())
            _beq_lookup = lookup
            _LOG.info("BEQ catalogue ready: %d entries (slim cache + filters on disk)", len(slim_cache))
            return slim_cache

        except Exception as err:
            _LOG.error("BEQ catalogue fetch error: %s", err)
            return []
        finally:
            try:
                raw_file.unlink(missing_ok=True)
            except OSError:
                pass


def _process_catalogue(raw_file: Path) -> tuple[list[dict], dict[str, str]]:
    with open(raw_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        _LOG.error("BEQ catalogue is not a list")
        return [], {}

    slim_cache: list[dict] = []
    lookup: dict[str, str] = {}
    filters_file = _filters_path()
    temp_file = filters_file.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as ff:
        ff.write("{")
        for i, entry in enumerate(data):
            title = entry.get("title", "")
            underlying = entry.get("underlying", "")
            raw_filters = entry.get("filters", [])

            key = _compute_entry_hash(title, underlying, raw_filters)

            slim_cache.append({
                "title": title,
                "year": entry.get("year", ""),
                "content_type": entry.get("content_type", ""),
                "audioTypes": entry.get("audioTypes", []),
                "author": entry.get("author", ""),
                "underlying": underlying,
                "key": key,
            })

            lookup[key] = underlying or title or "Unknown"

            stripped = [
                {"type": fl.get("type", "PeakingEQ"), "freq": fl.get("freq", 100),
                 "gain": fl.get("gain", 0), "q": fl.get("q", 1)}
                for fl in raw_filters
            ]
            if i > 0:
                ff.write(",")
            ff.write(f'"{key}":')
            ff.write(json.dumps(stripped, separators=(",", ":")))

            data[i] = None

        ff.write("}")

    temp_file.rename(filters_file)

    del data
    gc.collect()

    slim_cache.sort(key=lambda e: e.get("title", ""))
    return slim_cache, lookup


def _entry_to_item(entry: dict) -> BrowseMediaItem:
    title = entry.get("title", "Unknown")
    year = entry.get("year", "")
    audio_types = ", ".join(entry.get("audioTypes", []))
    author = entry.get("author", "")
    subtitle = f"{year}"
    if audio_types:
        subtitle += f" | {audio_types}"

    display_title = f"{title} {author}".strip()[:255]
    key = entry["key"]

    _beq_lookup[key] = entry.get("underlying", title) or title or "Unknown"

    return BrowseMediaItem(
        title=display_title,
        media_class=MediaClass.TRACK,
        media_type="beq_entry",
        media_id=f"beq:{key}",
        can_play=True,
        can_browse=False,
        subtitle=subtitle[:255] if subtitle else None,
    )


def _load_filters_for_key(key: str) -> list[dict] | None:
    fp = _filters_path()
    if not fp.exists():
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            all_filters = json.load(f)
        result = all_filters.get(key)
        del all_filters
        return result
    except Exception as err:
        _LOG.error("Failed to load BEQ filters from disk: %s", err)
        return None


async def get_beq_entry(key: str) -> dict | None:
    title = _beq_lookup.get(key)
    if not title:
        return None
    loop = asyncio.get_event_loop()
    filters = await loop.run_in_executor(None, _load_filters_for_key, key)
    if not filters:
        return None
    return {"underlying": title, "filters": filters}


async def clear_cache() -> bool:
    global _beq_cache, _beq_cache_timestamp, _beq_lookup
    if _beq_fetching.locked():
        _LOG.debug("BEQ fetch in progress, skipping cache clear")
        return True
    _beq_cache = None
    _beq_cache_timestamp = None
    _beq_lookup = {}
    try:
        fp = _filters_path()
        if fp.exists():
            fp.unlink()
    except OSError:
        pass
    return True


async def browse(device: HTP1Device, options: BrowseOptions) -> BrowseResults | StatusCodes:
    media_type = options.media_type or "root"
    media_id = options.media_id or ""

    if media_type == "root" or (options.media_id is None and options.media_type is None):
        return _browse_root(device)

    if media_type == "beq_categories":
        return await _browse_categories()

    if media_type == "beq_category":
        paging = options.paging
        limit = int((paging.limit if paging and paging.limit else None) or ITEMS_PER_PAGE)
        page = int((paging.page if paging and paging.page else None) or 1)
        return await _browse_category(media_id, page)

    return StatusCodes.NOT_FOUND


async def search(device: HTP1Device, options: SearchOptions) -> SearchResults | StatusCodes:

    query = options.query.lower().strip()
    if not query:
        return SearchResults(media=[], pagination=Pagination(page=1, limit=0, count=0))

    if _beq_cache is None:
        if not await _wait_for_cache():
            return _loading_searchresponse()

    paging = options.paging
    page = paging.page
    limit = int((paging.limit if paging and paging.limit else None) or ITEMS_PER_PAGE)
    catalogue = _beq_cache
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE

    results = []
    for entry in catalogue:
        title = entry.get("title", "").lower()
        if query in title:
            results.append(_entry_to_item(entry))
            if len(results) >= end_index:
                break

    return SearchResults(
        media=results[start_index:end_index],
        pagination=Pagination(page, limit=len(results), count=len(results)),
    )


def _browse_root(device: HTP1Device) -> BrowseResults:
    items = [
        BrowseMediaItem(
            title="BEQ Catalogue",
            media_class=MediaClass.DIRECTORY,
            media_type="beq_categories",
            media_id="beq_categories",
            can_browse=True,
            can_play=False,
            subtitle="Bass EQ correction filters for movies & TV",
        ),
    ]

    if device.beq_active:
        items.append(
            BrowseMediaItem(
                title=f"Clear BEQ: {device.beq_active}",
                media_class=MediaClass.TRACK,
                media_type="beq_clear",
                media_id="beq:clear",
                can_play=True,
                can_browse=False,
                subtitle="Remove currently loaded BEQ filter",
            ),
        )

    items.append(
            BrowseMediaItem(
                title="Clear BEQ Cache",
                media_class=MediaClass.TRACK,
                media_type="beq_reload",
                media_id="beq:reload",
                can_play=True,
                can_browse=False,
                subtitle="Clear cached catalogue. Browse again to reload.",
            ),
        )
    return BrowseResults(
        media=BrowseMediaItem(
            title=device.name,
            media_class=MediaClass.DIRECTORY,
            media_type="root",
            media_id="root",
            can_browse=True,
            can_search=True,
            items=items,
        ),
        pagination=Pagination(page=1, limit=len(items), count=len(items)),
    )


def _loading_response(title: str = "BEQ Catalogue") -> BrowseResults:
    items = [
        BrowseMediaItem(
            title="Loading BEQ catalogue...",
            media_class=MediaClass.DIRECTORY,
            media_type="beq_loading",
            media_id="beq_loading",
            can_play=False,
            can_browse=False,
            subtitle="Catalogue is downloading. Press back and browse again in ~2 min.",
        ),
    ]
    return BrowseResults(
        media=BrowseMediaItem(
            title=title,
            media_class=MediaClass.DIRECTORY,
            media_type="beq_categories",
            media_id="beq_categories",
            can_browse=True,
            items=items,
        ),
        pagination=Pagination(page=1, limit=1, count=1),
    )


def _loading_searchresponse(title: str = "BEQ Catalogue") -> SearchResults | StatusCodes:
    return SearchResults(
        media=[
            SearchMediaItem(
                title="Loading BEQ catalogue...",
                media_class=MediaClass.DIRECTORY,
                media_type="beq_loading",
                media_id="beq_loading",
                can_play=False,
                can_browse=False,
                subtitle="Catalogue is downloading. Press back and browse again in ~2 min.",
            )
        ],
        pagination=Pagination(page=1, limit=1, count=1),
    )

async def _wait_for_cache() -> bool:
    if _beq_cache is not None:
        return True
    if not _beq_fetching.locked():
        asyncio.create_task(prefetch_catalogue())
    return False


async def _browse_categories() -> BrowseResults:
    if _beq_cache is None:
        if not await _wait_for_cache():
            return _loading_response()

    catalogue = _beq_cache

    content_types: dict[str, int] = {}
    for entry in catalogue:
        ct = entry.get("content_type", "other")
        content_types[ct] = content_types.get(ct, 0) + 1

    items = []
    for ct in sorted(content_types.keys()):
        count = content_types[ct]
        items.append(
            BrowseMediaItem(
                title=ct.title(),
                media_class=MediaClass.DIRECTORY,
                media_type="beq_category",
                media_id=ct,
                can_browse=True,
                can_play=False,
                subtitle=f"{count} entries",
            ),
        )

    return BrowseResults(
        media=BrowseMediaItem(
            title="BEQ Catalogue",
            media_class=MediaClass.DIRECTORY,
            media_type="beq_categories",
            media_id="beq_categories",
            can_browse=True,
            can_search=True,
            items=items,
        ),
        pagination=Pagination(page=1, limit=len(items), count=len(items)),
    )


async def _browse_category(content_type: str, page: int = 1) -> BrowseResults:
    if _beq_cache is None:
        if not await _wait_for_cache():
            return _loading_response(content_type.title())

    entries = [e for e in _beq_cache if e.get("content_type", "") == content_type]
    entries.sort(key=lambda e: e.get("title", ""))

    total = len(entries)
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_entries = entries[start:end]

    items = [_entry_to_item(e) for e in page_entries]

    return BrowseResults(
        media=BrowseMediaItem(
            title=content_type.title(),
            media_class=MediaClass.DIRECTORY,
            media_type="beq_category",
            media_id=content_type,
            can_browse=True,
            can_search=True,
            items=items,
        ),
        pagination=Pagination(page=page, limit=ITEMS_PER_PAGE, count=total),
    )
