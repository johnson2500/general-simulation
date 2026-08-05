"""USGS Earthquake adapter.

Source: USGS Earthquake Hazards Program — GeoJSON feed (public, no auth).
Docs:   https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php

Treats each earthquake event as a generic "moving_entity" with a geographic
position and status.  This is deliberately not a domain-specific model; the
"earthquake" is just an entity with coordinates, timestamp, and status.

Default feed: all earthquakes in the past hour, worldwide.
Override ``feed_url`` in the constructor to use a different time window
(past_day, past_week, past_month) or magnitude filter.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.ingestion import CanonicalEntity

logger = logging.getLogger(__name__)

# Public USGS GeoJSON feeds — no API key required.
FEED_PAST_HOUR = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
)
FEED_PAST_DAY = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
)

# Entity type tag written to the live store.  Deliberately generic.
ENTITY_TYPE = "moving_entity"


class USGSEarthquakeAdapter:
    """Fetches USGS earthquake GeoJSON and normalises to CanonicalEntity.

    Each earthquake feature maps to one CanonicalEntity:
      id        — "usgs-<feature_id>"  (prefixed to avoid collisions)
      type      — "moving_entity"
      geometry  — GeoJSON Point (lon, lat) — depth stored in attributes
      timestamp — event origin time (UTC)
      status    — "reviewed" | "automatic"
      attributes — magnitude, place, depth_km, mag_type, title
    """

    adapter_id: str = "usgs_earthquakes"

    def __init__(
        self,
        feed_url: str = FEED_PAST_HOUR,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._feed_url = feed_url
        self._timeout = timeout_seconds

    async def fetch(self) -> dict[str, Any]:
        """GET the USGS GeoJSON feed and return the parsed response body."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._feed_url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        logger.debug(
            "USGS fetch: url=%s features=%d",
            self._feed_url,
            len(data.get("features", [])),
        )
        return data

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEntity]:
        """Convert a USGS GeoJSON FeatureCollection into CanonicalEntity list."""
        features: list[dict[str, Any]] = raw.get("features", [])
        entities: list[CanonicalEntity] = []
        for feature in features:
            entity = self._feature_to_entity(feature)
            if entity is not None:
                entities.append(entity)
        logger.debug("USGS normalise: %d → %d entities", len(features), len(entities))
        return entities

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _feature_to_entity(feature: dict[str, Any]) -> CanonicalEntity | None:
        """Map one GeoJSON Feature to a CanonicalEntity.  Returns None on bad data."""
        try:
            props: dict[str, Any] = feature.get("properties") or {}
            coords: list[float] = (feature.get("geometry") or {}).get("coordinates", [])
            feature_id: str = feature.get("id", "")

            if not feature_id or len(coords) < 2:
                logger.debug("Skipping feature with missing id or coords: %s", feature_id)
                return None

            lon, lat = coords[0], coords[1]
            depth_km: float | None = coords[2] if len(coords) > 2 else None

            # Epoch milliseconds → UTC datetime
            time_ms: int | None = props.get("time")
            if time_ms is None:
                logger.debug("Skipping feature with no time: %s", feature_id)
                return None
            timestamp = datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc)

            status: str = props.get("status") or "unknown"
            mag: float | None = props.get("mag")

            attributes: dict[str, Any] = {
                "magnitude": mag,
                "place": props.get("place"),
                "depth_km": depth_km,
                "mag_type": props.get("magType"),
                "title": props.get("title"),
                "source_url": props.get("url"),
            }
            # Drop None values to keep JSONB lean
            attributes = {k: v for k, v in attributes.items() if v is not None}

            return CanonicalEntity(
                id=f"usgs-{feature_id}",
                type=ENTITY_TYPE,
                geometry={"type": "Point", "coordinates": [lon, lat]},
                timestamp=timestamp,
                status=status,
                attributes=attributes,
            )
        except Exception:
            logger.exception("Error normalising USGS feature: %s", feature.get("id"))
            return None
