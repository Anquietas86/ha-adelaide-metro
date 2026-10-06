"""On-disk cache for the static GTFS bundle.

The bundle is large and only changes every few days, so it is kept in
.storage and re-validated with a conditional GET (ETag / Last-Modified)
instead of being downloaded on every restart.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, STATIC_GTFS_URL

_LOGGER = logging.getLogger(__name__)

ZIP_NAME = "google_transit.zip"
META_NAME = "google_transit.json"


class StaticGtfsCache:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._dir = Path(hass.config.path(".storage", DOMAIN))
        self._session = async_get_clientsession(hass)

    @property
    def zip_path(self) -> Path:
        return self._dir / ZIP_NAME

    @property
    def meta_path(self) -> Path:
        return self._dir / META_NAME

    def _read_meta(self) -> dict:
        try:
            meta = json.loads(self.meta_path.read_text())
        except (OSError, ValueError):
            return {}
        return meta if self.zip_path.exists() else {}

    def _read_zip(self) -> bytes:
        return self.zip_path.read_bytes()

    def _write(self, data: bytes | None, meta: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if data is not None:
            tmp = self.zip_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, self.zip_path)
        self.meta_path.write_text(json.dumps(meta))

    async def async_get(self, max_age: timedelta, force: bool = False) -> bytes:
        """Return the bundle bytes, downloading only when the cache is stale.

        Falls back to the cached copy if the download fails.
        """
        meta = await self.hass.async_add_executor_job(self._read_meta)
        now = datetime.now(UTC)
        fetched_at = None
        if meta.get("fetched_at"):
            try:
                fetched_at = datetime.fromisoformat(meta["fetched_at"])
            except ValueError:
                fetched_at = None

        if not force and fetched_at and now - fetched_at < max_age:
            _LOGGER.debug("Using cached static GTFS from %s", fetched_at)
            return await self.hass.async_add_executor_job(self._read_zip)

        headers = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

        try:
            async with self._session.get(STATIC_GTFS_URL, headers=headers) as resp:
                if resp.status == 304 and meta:
                    _LOGGER.debug("Static GTFS unchanged on server")
                    meta["fetched_at"] = now.isoformat()
                    await self.hass.async_add_executor_job(self._write, None, meta)
                    return await self.hass.async_add_executor_job(self._read_zip)
                resp.raise_for_status()
                data = await resp.read()
                new_meta = {
                    "fetched_at": now.isoformat(),
                    "etag": resp.headers.get("ETag"),
                    "last_modified": resp.headers.get("Last-Modified"),
                }
        except (TimeoutError, ClientError) as err:
            if meta:
                _LOGGER.warning("Static GTFS download failed, using cached copy: %s", err)
                return await self.hass.async_add_executor_job(self._read_zip)
            raise

        await self.hass.async_add_executor_job(self._write, data, new_meta)
        return data

    @property
    def last_fetched(self) -> str | None:
        """Best-effort read of when the bundle was last fetched (for diagnostics)."""
        try:
            return json.loads(self.meta_path.read_text()).get("fetched_at")
        except (OSError, ValueError):
            return None
