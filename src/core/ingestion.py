"""Domain-agnostic ingestion abstractions.

Two things live here:
  - CanonicalEntity   — the single, domain-agnostic schema for a live-store row
  - IngestionAdapter  — the Protocol every data-source adapter must satisfy

Domain specifics (field mappings, source URLs, auth) belong in adapter
implementations under ``domain/<name>/adapters/``.  Nothing here may contain
domain-specific entity names (no Port, no Machine, no Flight).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class CanonicalEntity:
    """One entity record, ready to be upserted into the PostGIS live store.

    Intentionally flat and domain-agnostic.  All domain-specific columns live
    in the JSONB ``attributes`` field.

    Geometry follows the GeoJSON convention: a dict with ``type`` and
    ``coordinates`` keys, or ``None`` when the entity has no spatial
    representation.  The runner converts this to a PostGIS geometry at upsert
    time using ``ST_GeomFromGeoJSON``.

    Example::

        CanonicalEntity(
            id="eq-ci123456",
            type="moving_entity",
            geometry={"type": "Point", "coordinates": [-120.5, 37.2]},
            timestamp=datetime.utcnow(),
            status="reviewed",
            attributes={"magnitude": 2.5, "depth_km": 5.0},
        )
    """

    id: str
    type: str
    timestamp: datetime
    status: str
    geometry: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class IngestionAdapter(Protocol):
    """Protocol every data-source adapter must implement.

    Separation of concerns:
      ``fetch``      — I/O only; returns raw data in whatever shape the source
                       produces.  Must be idempotent (safe to retry).
      ``normalize``  — Pure transformation; converts raw data to a list of
                       CanonicalEntity.  No I/O.

    This two-step design makes adapters trivially testable: patch ``fetch``
    with a recorded fixture and call ``normalize`` directly.
    """

    @property
    def adapter_id(self) -> str:
        """Stable identifier for this adapter (used in logs and tool calls)."""
        ...

    async def fetch(self) -> Any:
        """Retrieve raw data from the source.  No normalisation."""
        ...

    def normalize(self, raw: Any) -> list[CanonicalEntity]:
        """Convert raw data into domain-agnostic CanonicalEntity records."""
        ...
