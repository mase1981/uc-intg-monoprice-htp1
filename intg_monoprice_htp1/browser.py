"""
Memory-conscious BEQ media browser backed by the hosted compact catalogue.

The browse index contains metadata only. Individual filter payloads are fetched
only when a BEQ is selected and retained in a bounded in-memory LRU.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import aiohttp

from ucapi import StatusCodes
from ucapi.api_definitions import Pagination
from ucapi.media_player import (
    BrowseMediaItem, BrowseOptions, BrowseResults, MediaClass,
    SearchMediaItem, SearchOptions, SearchResults,
)

if TYPE_CHECKING:
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

CATALOGUE_BASE_URL = (
    "https://mase1981.github.io/uc-intg-monoprice-htp1/beq/"
)
ITEMS_PER_PAGE = 50
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5, sock_read=15)
ENTRY_CACHE_MAX = 12
INDEX_CACHE_LIFE = 86400

_index: dict[str, Any] | None = None
_manifest: dict[str, Any] | None = None
_index_loaded_at: float = 0.0
_index_lock = asyncio.Lock()
_entry_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_entry_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _new_session() -> aiohttp.ClientSession:
    """Create an HTTP session using the original Remote-compatible TLS setting."""
    connector = aiohttp.TCPConnector(ssl=False)
    return aiohttp.ClientSession(connector=connector, timeout=REQUEST_TIMEOUT)


async def _get_json(url: str) -> Any:
    async with _new_session() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


def _validate_index(index: Any, version: str) -> None:
    """Reject malformed index data before browse/search can consume it."""
    if (
        not isinstance(index, dict)
        or index.get("schema_version") != 1
        or index.get("version") != version
        or not isinstance(index.get("entries"), list)
        or not isinstance(index.get("categories"), dict)
    ):
        raise ValueError("invalid BEQ index")

    entries = index["entries"]
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("BEQ index contains a non-object entry")
        entry_id = entry.get("id")
        if (not isinstance(entry_id, str) or len(entry_id) != 16
                or any(char not in "0123456789abcdef" for char in entry_id)):
            raise ValueError("BEQ index contains an invalid entry ID")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise ValueError("BEQ index contains an invalid title")
        if not isinstance(entry.get("author"), str) or not isinstance(entry.get("year"), str):
            raise ValueError("BEQ index contains invalid author/year metadata")
        audio_types = entry.get("audioTypes")
        if not isinstance(audio_types, list) or any(
            not isinstance(value, str) for value in audio_types
        ):
            raise ValueError("BEQ index contains invalid audio type metadata")

    entry_count = len(entries)
    categorized: set[int] = set()
    for category, offsets in index["categories"].items():
        if not isinstance(category, str) or not category:
            raise ValueError("BEQ index contains an invalid category name")
        if not isinstance(offsets, list):
            raise ValueError("BEQ index contains invalid category offsets")
        for offset in offsets:
            # bool is a subclass of int, but is never a valid array offset.
            if (isinstance(offset, bool) or not isinstance(offset, int)
                    or offset < 0 or offset >= entry_count or offset in categorized):
                raise ValueError("BEQ index contains an invalid or duplicate category offset")
            categorized.add(offset)

    if len(categorized) != entry_count:
        raise ValueError("BEQ index categories do not cover every entry")


async def _load_index() -> bool:
    """Load and validate the compact index, retaining a last-known-good copy."""
    global _index, _manifest, _index_loaded_at
    if _index is not None and time.monotonic() - _index_loaded_at < INDEX_CACHE_LIFE:
        return True

    async with _index_lock:
        if _index is not None and time.monotonic() - _index_loaded_at < INDEX_CACHE_LIFE:
            return True
        try:
            manifest = await _get_json(CATALOGUE_BASE_URL + "manifest.json")
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise ValueError("unsupported BEQ manifest schema")
            version = manifest.get("version")
            index_path = manifest.get("index")
            index_sha256 = manifest.get("index_sha256")
            if (not isinstance(version, str) or len(version) != 16
                    or any(char not in "0123456789abcdef" for char in version)
                    or index_path != f"{version}/index.json"
                    or not isinstance(index_sha256, str) or len(index_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in index_sha256)):
                raise ValueError("invalid BEQ manifest")

            async with _new_session() as session:
                async with session.get(CATALOGUE_BASE_URL + index_path) as response:
                    response.raise_for_status()
                    index_bytes = await response.read()
            if hashlib.sha256(index_bytes).hexdigest() != index_sha256:
                raise ValueError("BEQ index checksum mismatch")

            index = json.loads(index_bytes)
            _validate_index(index, version)
            declared_count = manifest.get("entry_count")
            if (isinstance(declared_count, bool) or not isinstance(declared_count, int)
                    or declared_count != len(index["entries"])):
                raise ValueError("BEQ manifest entry count does not match its index")

            previous_version = _manifest.get("version") if _manifest else None
            _manifest, _index = manifest, index
            _index_loaded_at = time.monotonic()
            if previous_version != version:
                _entry_cache.clear()
            _LOG.info("BEQ index loaded: %d entries (version %s)",
                      len(index["entries"]), version)
            return True
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, json.JSONDecodeError) as err:
            _LOG.warning("Unable to load hosted BEQ index: %s", err)
            if _index is not None:
                # Avoid hammering a failed endpoint; retry after five minutes.
                _index_loaded_at = time.monotonic() - INDEX_CACHE_LIFE + 300
                return True
            return False


async def _fetch_entry_payload(version: str, key: str) -> dict[str, Any]:
    url = f"{CATALOGUE_BASE_URL}{version}/entries/{key}.json"
    payload = await _get_json(url)
    if (not isinstance(payload, dict) or not isinstance(payload.get("title"), str)
            or not isinstance(payload.get("underlying"), str)
            or not isinstance(payload.get("filters"), list)):
        raise ValueError("invalid BEQ entry payload")
    filters = payload["filters"]
    if not filters or len(filters) > 16:
        raise ValueError("BEQ entry filter count is outside the HTP-1's 1-16 slot range")
    for item in filters:
        if not isinstance(item, dict) or item.get("type") not in {
            "PeakingEQ", "LowShelf", "HighShelf"
        }:
            raise ValueError("BEQ entry contains an unsupported filter type")
        for field in ("freq", "gain", "q"):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"BEQ entry contains an invalid {field}")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"BEQ entry contains a non-finite {field}")
        if not 10 <= item["freq"] <= 20000:
            raise ValueError("BEQ entry frequency is outside the HTP-1 BEQ range")
        if not -99 <= item["gain"] <= 20:
            raise ValueError("BEQ entry gain is outside the HTP-1 BEQ range")
        if not 0.1 <= item["q"] <= 10:
            raise ValueError("BEQ entry Q is outside the HTP-1 PEQ range")
    return payload


async def get_beq_entry(key: str) -> dict[str, Any] | None:
    """Fetch a selected payload once per catalogue version and entry ID."""
    global _index_loaded_at
    if not await _load_index() or _manifest is None:
        return None
    if len(key) != 16 or any(char not in "0123456789abcdef" for char in key):
        return None

    version = _manifest["version"]
    if key in _entry_cache:
        _entry_cache.move_to_end(key)
        return _entry_cache[key]

    # Multiple UI requests can select the same item concurrently. Serialize
    # only requests for the same version/ID, then recheck the cache after
    # acquiring the lock so only one network fetch is performed.
    lock = _entry_locks.setdefault((version, key), asyncio.Lock())
    async with lock:
        if key in _entry_cache:
            _entry_cache.move_to_end(key)
            return _entry_cache[key]

        payload_version = version
        try:
            payload = await _fetch_entry_payload(payload_version, key)
        except aiohttp.ClientResponseError as err:
            if err.status != 404:
                _LOG.error("Unable to load BEQ entry %s: %s", key, err)
                return None
            # A Pages deployment may replace the previous version while an old
            # browse result is still open. Refresh and retry if this ID survived.
            _index_loaded_at = time.monotonic() - INDEX_CACHE_LIFE - 1
            if not await _load_index() or _manifest is None or _manifest["version"] == version:
                return None
            try:
                payload_version = _manifest["version"]
                payload = await _fetch_entry_payload(payload_version, key)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, json.JSONDecodeError) as retry_err:
                _LOG.error("Unable to load BEQ entry %s after catalogue refresh: %s", key, retry_err)
                return None
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, json.JSONDecodeError) as err:
            _LOG.error("Unable to load BEQ entry %s: %s", key, err)
            return None

        # An older in-flight request may finish after another request has
        # refreshed the index and cleared the cache. Never repopulate the
        # current-version cache with a payload from the previous deployment.
        if _manifest is not None and _manifest.get("version") == payload_version:
            _entry_cache[key] = payload
            _entry_cache.move_to_end(key)
            while len(_entry_cache) > ENTRY_CACHE_MAX:
                _entry_cache.popitem(last=False)
        return payload


def _entry_to_item(entry: dict[str, Any]) -> BrowseMediaItem:
    title = entry.get("title", "Unknown")
    audio_types = ", ".join(entry.get("audioTypes", []))
    subtitle = str(entry.get("year", ""))
    if audio_types:
        subtitle += f" | {audio_types}"
    display_title = f"{title} {entry.get('author', '')}".strip()[:255]
    return BrowseMediaItem(
        title=display_title,
        media_class=MediaClass.TRACK,
        media_type="beq_entry",
        media_id=f"beq:{entry['id']}",
        can_play=entry.get("has_filters", True),
        can_browse=False,
        subtitle=subtitle[:255] if subtitle else None,
    )


async def browse(device: HTP1Device, options: BrowseOptions) -> BrowseResults | StatusCodes:
    media_type = options.media_type or "root"
    media_id = options.media_id or ""
    if media_type == "root":
        return _browse_root(device)
    if media_type == "beq_categories":
        return await _browse_categories()
    if media_type == "beq_category":
        paging = options.paging
        page = max(1, int(paging.page if paging and paging.page else 1))
        limit = max(1, min(1000, int(paging.limit if paging and paging.limit else ITEMS_PER_PAGE)))
        return await _browse_category(media_id, page, limit)
    return StatusCodes.NOT_FOUND


async def search(device: HTP1Device, options: SearchOptions) -> SearchResults | StatusCodes:
    query = options.query.casefold().strip()
    if not query:
        return SearchResults(media=[], pagination=Pagination(page=1, limit=0, count=0))
    if not await _load_index():
        return StatusCodes.SERVER_ERROR

    paging = options.paging
    page = max(1, int(paging.page if paging and paging.page else 1))
    limit = max(1, min(1000, int(paging.limit if paging and paging.limit else ITEMS_PER_PAGE)))
    start = (page - 1) * limit
    items: list[SearchMediaItem] = []
    total_matches = 0
    for entry in _index["entries"]:
        if (
            query in entry.get("title", "").casefold()
            or query in entry.get("author", "").casefold()
        ):
            if start <= total_matches < start + limit:
                items.append(_entry_to_item(entry))
            total_matches += 1
    return SearchResults(
        media=items,
        pagination=Pagination(page=page, limit=limit, count=total_matches),
    )


def _browse_root(device: HTP1Device) -> BrowseResults:
    items = [
        BrowseMediaItem(
            title="BEQ Catalogue", media_class=MediaClass.DIRECTORY,
            media_type="beq_categories", media_id="beq_categories",
            can_browse=True, can_play=False,
            subtitle="Bass EQ correction filters for movies & TV",
        ),
    ]
    if device.beq_active:
        items.append(BrowseMediaItem(
            title=f"Clear BEQ: {device.beq_active}", media_class=MediaClass.TRACK,
            media_type="beq_clear", media_id="beq:clear",
            can_play=True, can_browse=False,
            subtitle="Remove currently loaded BEQ filter",
        ))
    return BrowseResults(
        media=BrowseMediaItem(
            title=device.name, media_class=MediaClass.DIRECTORY,
            media_type="root", media_id="root", can_browse=True,
            can_search=True, items=items,
        ),
        pagination=Pagination(page=1, limit=len(items), count=len(items)),
    )


async def _browse_categories() -> BrowseResults | StatusCodes:
    if not await _load_index():
        return StatusCodes.SERVER_ERROR
    categories = _index["categories"]
    items = [
        BrowseMediaItem(
            title=category.title(), media_class=MediaClass.DIRECTORY,
            media_type="beq_category", media_id=category,
            can_browse=True, can_play=False,
            subtitle=f"{len(ids)} entries",
        )
        for category, ids in sorted(categories.items())
    ]
    return BrowseResults(
        media=BrowseMediaItem(
            title="BEQ Catalogue", media_class=MediaClass.DIRECTORY,
            media_type="beq_categories", media_id="beq_categories",
            can_browse=True, can_search=True, items=items,
        ),
        pagination=Pagination(page=1, limit=len(items), count=len(items)),
    )


async def _browse_category(
    content_type: str, page: int = 1, limit: int = ITEMS_PER_PAGE
) -> BrowseResults | StatusCodes:
    if not await _load_index():
        return StatusCodes.SERVER_ERROR
    entry_indexes = _index["categories"].get(content_type, [])
    start = (page - 1) * limit
    page_entries = (_index["entries"][entry_index]
                    for entry_index in entry_indexes[start:start + limit])
    items = [_entry_to_item(entry) for entry in page_entries]

    return BrowseResults(
        media=BrowseMediaItem(
            title=content_type.title(), media_class=MediaClass.DIRECTORY,
            media_type="beq_category", media_id=content_type,
            can_browse=True, can_search=True, items=items,
        ),
        pagination=Pagination(page=page, limit=limit, count=len(entry_indexes)),
    )
