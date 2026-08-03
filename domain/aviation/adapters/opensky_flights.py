"""OpenSky Network flight state adapter.

Source: OpenSky Network REST API — public endpoint, no authentication required
        for anonymous access (rate-limited to ~10 req/min).
Docs:   https://openskynetwork.github.io/opensky-api/rest.html

Each aircraft state vector is normalised to a generic CanonicalEntity so the
rest of the pipeline (graph, vector store, reasoning) remains domain-agnostic.

State vector field positions (0-indexed array):
  0  icao24          — unique ICAO 24-bit transponder address (hex string)
  1  callsign        — aircraft call sign (may be null or blank)
  2  origin_country  — country of origin derived from ICAO24
  3  time_position   — Unix timestamp of last position update (may be null)
  4  last_contact    — Unix timestamp of last signal received
  5  longitude       — WGS-84 longitude (decimal degrees, may be null)
  6  latitude        — WGS-84 latitude  (decimal degrees, may be null)
  7  baro_altitude   — barometric altitude (metres, may be null)
  8  on_ground       — bool — true if on ground transponder flag set
  9  velocity        — ground speed (m/s, may be null)
  10 true_track      — track angle (degrees clockwise from north, may be null)
  11 vertical_rate   — climb/descent rate (m/s, may be null)
  12 sensors         — list of sensor serial numbers (may be null)
  13 geo_altitude    — geometric altitude (metres, may be null)
  14 squawk          — transponder squawk code (may be null)
  15 spi             — special purpose indicator bool
  16 position_source — 0=ADS-B, 1=ASTERIX, 2=MLAT, 3=FLARM
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.ingestion import CanonicalEntity

logger = logging.getLogger(__name__)

API_URL = "https://opensky-network.org/api/states/all"

ENTITY_TYPE = "moving_entity"

# Human-readable label for the position source field.
_POSITION_SOURCE = {0: "ADS-B", 1: "ASTERIX", 2: "MLAT", 3: "FLARM"}


class OpenSkyFlightsAdapter:
    """Fetches live aircraft states from the OpenSky Network and normalises
    them to CanonicalEntity records for the PostGIS live store.

    Aircraft without a known position (null lon/lat) are silently skipped —
    they cannot be placed on the map or correlated spatially.

    Each entity:
      id         — "opensky-<icao24>"
      type       — "moving_entity"
      geometry   — GeoJSON Point (lon, lat)
      timestamp  — time of last position update (UTC)
      status     — "airborne" | "on_ground"
      attributes — call_sign, origin_country, baro_altitude_m,
                   geo_altitude_m, velocity_ms, true_track_deg,
                   vertical_rate_ms, squawk, position_source
    """

    adapter_id: str = "opensky_flights"

    def __init__(
        self,
        api_url: str = API_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_url = api_url
        self._timeout = timeout_seconds

    async def fetch(self) -> dict[str, Any]:
        """GET /api/states/all and return the parsed JSON response."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                self._api_url,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        states = data.get("states") or []
        logger.debug(
            "OpenSky fetch: url=%s states=%d server_time=%s",
            self._api_url,
            len(states),
            data.get("time"),
        )
        return data

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEntity]:
        """Convert the OpenSky states/all response into CanonicalEntity list."""
        states: list[list[Any]] = raw.get("states") or []
        entities: list[CanonicalEntity] = []
        skipped = 0
        for state in states:
            entity = self._state_to_entity(state)
            if entity is not None:
                entities.append(entity)
            else:
                skipped += 1
        logger.debug(
            "OpenSky normalise: %d states → %d entities (%d skipped, no position)",
            len(states),
            len(entities),
            skipped,
        )
        return entities

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _state_to_entity(state: list[Any]) -> CanonicalEntity | None:
        """Map one OpenSky state vector (array) to a CanonicalEntity.

        Returns None if the aircraft has no position fix (lon or lat is null).
        """
        try:
            if len(state) < 17:
                logger.debug("Skipping malformed state vector (len=%d)", len(state))
                return None

            icao24: str = state[0] or ""
            if not icao24:
                return None

            lon = state[5]
            lat = state[6]
            if lon is None or lat is None:
                # No position fix — cannot place on map.
                return None

            callsign: str = (state[1] or "").strip()
            origin_country: str = state[2] or ""
            time_position: int | None = state[3]
            last_contact: int | None = state[4]
            on_ground: bool = bool(state[8])
            velocity: float | None = state[9]
            true_track: float | None = state[10]
            vertical_rate: float | None = state[11]
            baro_altitude: float | None = state[7]
            geo_altitude: float | None = state[13]
            squawk: str | None = state[14]
            position_source_code: int = state[16] if state[16] is not None else 0

            # Use time_position if available, fall back to last_contact.
            ts_epoch: int | None = time_position or last_contact
            if ts_epoch is None:
                timestamp = datetime.now(tz=timezone.utc)
            else:
                timestamp = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)

            status = "on_ground" if on_ground else "airborne"

            attributes: dict[str, Any] = {
                "call_sign": callsign or None,
                "origin_country": origin_country or None,
                "baro_altitude_m": baro_altitude,
                "geo_altitude_m": geo_altitude,
                "velocity_ms": velocity,
                "true_track_deg": true_track,
                "vertical_rate_ms": vertical_rate,
                "squawk": squawk,
                "position_source": _POSITION_SOURCE.get(position_source_code, "unknown"),
                "on_ground": on_ground,
            }
            # Drop None values to keep JSONB lean.
            attributes = {k: v for k, v in attributes.items() if v is not None}

            return CanonicalEntity(
                id=f"opensky-{icao24}",
                type=ENTITY_TYPE,
                geometry={"type": "Point", "coordinates": [lon, lat]},
                timestamp=timestamp,
                status=status,
                attributes=attributes,
            )
        except Exception:
            icao = state[0] if state else "unknown"
            logger.exception("Error normalising OpenSky state: icao24=%s", icao)
            return None
