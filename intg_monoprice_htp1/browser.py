"""
Monoprice HTP-1 media browser for BEQ catalogue.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import aiohttp

from ucapi import StatusCodes
from ucapi.api_definitions import (
    BrowseMediaItem,
    BrowseOptions,
    BrowseResults,
    MediaClass,
    Pagination,
    SearchOptions,
    SearchResults,
)

if TYPE_CHECKING:
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

BEQ_DB_URL = "https://beqcatalogue.readthedocs.io/en/latest/database.json"
ITEMS_PER_PAGE = 50

_beq_cache: list[dict] | None = None


async def _fetch_beq_catalogue() -> list[dict]:
    global _beq_cache
    if _beq_cache is not None:
        return _beq_cache

    _LOG.info("Fetching BEQ catalogue from %s", BEQ_DB_URL)
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(BEQ_DB_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    _LOG.error("BEQ catalogue fetch URL: %s", BEQ_DB_URL)
                    _LOG.error("BEQ catalogue fetch failed: %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
                if isinstance(data, list):
                    _beq_cache = data
                    _LOG.info("BEQ catalogue loaded: %d entries", len(data))
                    return data
    except Exception as err:
        _LOG.error("BEQ catalogue fetch error: %s", err)
    return []


def _build_beq_media_id(entry: dict) -> str:
    compact = {
        "title": entry.get("title", "Unknown"),
        "underlying": entry.get("underlying", ""),
        "filters": entry.get("filters", []),
    }
    for f in compact["filters"]:
        f.pop("biquads", None)
    return "beq:" + json.dumps(compact, separators=(",", ":"))


def _entry_to_item(entry: dict) -> BrowseMediaItem:
    title = entry.get("title", "Unknown")
    year = entry.get("year", "")
    audio_types = ", ".join(entry.get("audioTypes", []))
    author = entry.get("author", "")
    subtitle = f"{year}"
    if audio_types:
        subtitle += f" | {audio_types}"

    images = entry.get("images", [])
    image_url = images[0] if images else ""

    return BrowseMediaItem(
        title=title + " " + author + "\n" + audio_types,
        media_class=MediaClass.TRACK,
        media_type="beq_entry",
        media_id=_build_beq_media_id(entry),
        can_play=True,
        can_browse=False,
        subtitle=subtitle,
        image_url=image_url,
    )


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

    paging = options.paging
    page = paging.page
    limit = int((paging.limit if paging and paging.limit else None) or ITEMS_PER_PAGE)
    catalogue = await _fetch_beq_catalogue()
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


async def _browse_categories() -> BrowseResults:
    catalogue = await _fetch_beq_catalogue()

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
    catalogue = await _fetch_beq_catalogue()

    entries = [e for e in catalogue if e.get("content_type", "") == content_type]
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
