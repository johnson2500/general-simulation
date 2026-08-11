"""Synthetic shipping adapter (fixture-backed, API-swappable).

``fetch`` loads a committed JSON fixture that mimics a future AIS / freight
API payload.  Swap ``fetch`` to an HTTP client later without changing
``normalize`` — keep the fixture schema aligned with the real API when you
pick a vendor.

Graph edges and simulation events are *not* produced here; wire those in
``domain.shipping.bootstrap_graph`` / ``scripts/seed_shipping.py``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.ingestion import CanonicalEntity

logger = logging.getLogger(__name__)

# Default fixture — same shape a live API response should return.
_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "shipping_demo.json"
)

VESSEL_TYPE = "moving_entity"
PORT_TYPE = "fixed_node"
CARGO_TYPE = "cargo_item"


class ShippingDemoAdapter:
    """Normalises synthetic (or future live) shipping JSON to CanonicalEntity.

    Each vessel / port / cargo record maps to one CanonicalEntity:
      vessels  — id as given, type moving_entity, Point geometry
      ports    — id as given, type fixed_node, Point geometry
      cargo    — id as given, type cargo_item, no geometry

    Records missing required fields (id, or lon/lat for vessels/ports) are
    skipped silently so a flaky live feed does not fail the whole pull.
    """

    adapter_id: str = "shipping_demo"

    def __init__(
        self,
        fixture_path: Path | str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._fixture_path = Path(fixture_path) if fixture_path else _DEFAULT_FIXTURE
        # Kept so a future HTTP fetch can reuse the same constructor knobs.
        self._timeout = timeout_seconds

    async def fetch(self) -> dict[str, Any]:
        """Load the synthetic shipping payload.

        Replace this method body with an HTTP GET when a live API is available;
        return the parsed JSON in the same shape as ``shipping_demo.json``.
        """
        text = self._fixture_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(text)
        logger.debug(
            "shipping_demo fetch: path=%s ports=%d vessels=%d cargo=%d",
            self._fixture_path,
            len(data.get("ports", [])),
            len(data.get("vessels", [])),
            len(data.get("cargo", [])),
        )
        return data

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEntity]:
        """Convert a shipping payload into CanonicalEntity records."""
        now = datetime.now(tz=timezone.utc)
        entities: list[CanonicalEntity] = []

        for port in raw.get("ports", []) or []:
            entity = self._port_to_entity(port, now)
            if entity is not None:
                entities.append(entity)

        for vessel in raw.get("vessels", []) or []:
            entity = self._vessel_to_entity(vessel, now)
            if entity is not None:
                entities.append(entity)

        for item in raw.get("cargo", []) or []:
            entity = self._cargo_to_entity(item, now)
            if entity is not None:
                entities.append(entity)

        logger.debug(
            "shipping_demo normalise: ports+vessels+cargo → %d entities",
            len(entities),
        )
        return entities

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_timestamp(value: Any, fallback: datetime) -> datetime:
        if value is None:
            return fallback
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return fallback
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        return fallback

    @classmethod
    def _port_to_entity(
        cls, port: dict[str, Any], now: datetime
    ) -> CanonicalEntity | None:
        port_id = str(port.get("id") or "").strip()
        lon, lat = port.get("lon"), port.get("lat")
        if not port_id or lon is None or lat is None:
            logger.debug("Skipping port with missing id or coords: %s", port_id)
            return None
        try:
            lon_f, lat_f = float(lon), float(lat)
        except (TypeError, ValueError):
            logger.debug("Skipping port with non-numeric coords: %s", port_id)
            return None

        attributes = {
            "name": port.get("name"),
            "country": port.get("country"),
            "role": port.get("role"),
            "throughput_teu_day": port.get("throughput_teu_day"),
        }
        attributes = {k: v for k, v in attributes.items() if v is not None}

        return CanonicalEntity(
            id=port_id,
            type=PORT_TYPE,
            timestamp=cls._parse_timestamp(port.get("timestamp"), now),
            status=str(port.get("status") or "active"),
            geometry={"type": "Point", "coordinates": [lon_f, lat_f]},
            attributes=attributes,
        )

    @classmethod
    def _vessel_to_entity(
        cls, vessel: dict[str, Any], now: datetime
    ) -> CanonicalEntity | None:
        vessel_id = str(vessel.get("id") or "").strip()
        lon, lat = vessel.get("lon"), vessel.get("lat")
        if not vessel_id or lon is None or lat is None:
            logger.debug("Skipping vessel with missing id or coords: %s", vessel_id)
            return None
        try:
            lon_f, lat_f = float(lon), float(lat)
        except (TypeError, ValueError):
            logger.debug("Skipping vessel with non-numeric coords: %s", vessel_id)
            return None

        attributes = {
            "name": vessel.get("name"),
            "route": vessel.get("route"),
            "origin_port_id": vessel.get("origin_port_id"),
            "dest_port_id": vessel.get("dest_port_id"),
            "imo": vessel.get("imo"),
            "teu_capacity": vessel.get("teu_capacity"),
            "revenue_usd": vessel.get("revenue_usd"),
            "eta_utc": vessel.get("eta_utc"),
        }
        attributes = {k: v for k, v in attributes.items() if v is not None}

        return CanonicalEntity(
            id=vessel_id,
            type=VESSEL_TYPE,
            timestamp=cls._parse_timestamp(vessel.get("timestamp"), now),
            status=str(vessel.get("status") or "underway"),
            geometry={"type": "Point", "coordinates": [lon_f, lat_f]},
            attributes=attributes,
        )

    @classmethod
    def _cargo_to_entity(
        cls, item: dict[str, Any], now: datetime
    ) -> CanonicalEntity | None:
        cargo_id = str(item.get("id") or "").strip()
        carrier_id = str(item.get("carrier_id") or "").strip()
        if not cargo_id or not carrier_id:
            logger.debug("Skipping cargo with missing id or carrier_id: %s", cargo_id)
            return None

        quantity = item.get("quantity")
        unit_price = item.get("unit_price_usd")
        try:
            qty_f = float(quantity) if quantity is not None else None
            price_f = float(unit_price) if unit_price is not None else None
        except (TypeError, ValueError):
            logger.debug("Skipping cargo with bad quantity/price: %s", cargo_id)
            return None

        value = item.get("value_usd")
        if value is None and qty_f is not None and price_f is not None:
            value = qty_f * price_f

        attributes: dict[str, Any] = {
            "commodity": item.get("commodity"),
            "quantity": qty_f,
            "unit_price_usd": price_f,
            "value_usd": float(value) if value is not None else None,
            "carrier_id": carrier_id,
        }
        attributes = {k: v for k, v in attributes.items() if v is not None}

        return CanonicalEntity(
            id=cargo_id,
            type=CARGO_TYPE,
            timestamp=cls._parse_timestamp(item.get("timestamp"), now),
            status=str(item.get("status") or "in_transit"),
            geometry=None,
            attributes=attributes,
        )
