import { useEffect } from 'react'
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'
import type { LatLngBoundsExpression, LatLngExpression } from 'leaflet'
import type { EntityGeoJsonFeature, RecommendedRerouteOut } from '../types/api'

const DEFAULT_CENTER: LatLngExpression = [51.5, -0.5]
const DEFAULT_ZOOM = 6
/** Explicit px height — Leaflet collapses to 0px with height:100% in PF layouts. */
const MAP_HEIGHT_PX = 560

/** PatternFly chart/status greens — diversion markers vs red affected. */
const DIVERSION_STROKE = '#3E8635'
const DIVERSION_FILL = '#6EC664'
const ROUTE_LINE = '#F0AB00'

function pointCoords(
  feature: EntityGeoJsonFeature,
): [number, number] | null {
  const g = feature.geometry
  if (g.type !== 'Point' || !Array.isArray(g.coordinates)) return null
  const [lon, lat] = g.coordinates as number[]
  if (typeof lon !== 'number' || typeof lat !== 'number') return null
  return [lat, lon]
}

function InvalidateSize() {
  const map = useMap()
  useEffect(() => {
    const id = window.setTimeout(() => map.invalidateSize(), 50)
    const onResize = () => map.invalidateSize()
    window.addEventListener('resize', onResize)
    return () => {
      window.clearTimeout(id)
      window.removeEventListener('resize', onResize)
    }
  }, [map])
  return null
}

function FitBounds({
  features,
  highlightedIds,
  diversionMarkers,
}: {
  features: EntityGeoJsonFeature[]
  highlightedIds: string[]
  diversionMarkers: RecommendedRerouteOut[]
}) {
  const map = useMap()

  useEffect(() => {
    const highlighted = new Set(highlightedIds)
    const focus =
      highlighted.size > 0
        ? features.filter((f) => highlighted.has(f.properties.id))
        : features
    const positions: [number, number][] = focus
      .map(pointCoords)
      .filter((p): p is [number, number] => p != null)

    for (const d of diversionMarkers) {
      positions.push([d.latitude, d.longitude])
    }

    if (positions.length === 0) {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM)
      return
    }
    if (positions.length === 1) {
      map.setView(positions[0], 8)
      return
    }
    const bounds: LatLngBoundsExpression = positions
    const tight = highlighted.size > 0 || diversionMarkers.length > 0
    map.fitBounds(bounds, { padding: [48, 48], maxZoom: tight ? 9 : 8 })
    window.setTimeout(() => map.invalidateSize(), 50)
  }, [features, highlightedIds, diversionMarkers, map])

  return null
}

export interface EntityMapProps {
  features: EntityGeoJsonFeature[]
  highlightedIds?: string[]
  /** Solver-recommended diversion / alternate targets to mark on the map. */
  diversionMarkers?: RecommendedRerouteOut[]
  heightPx?: number
}

export function EntityMap({
  features,
  highlightedIds = [],
  diversionMarkers = [],
  heightPx = MAP_HEIGHT_PX,
}: EntityMapProps) {
  const highlighted = new Set(highlightedIds)
  const byId = new Map(features.map((f) => [f.properties.id, f]))

  // Draw non-highlighted first so affected markers paint on top.
  const ordered = [
    ...features.filter((f) => !highlighted.has(f.properties.id)),
    ...features.filter((f) => highlighted.has(f.properties.id)),
  ]

  // One marker per diversion airport (multiple flights may share a target).
  const uniqueTargets = new Map<string, RecommendedRerouteOut>()
  for (const d of diversionMarkers) {
    if (!uniqueTargets.has(d.target_id)) {
      uniqueTargets.set(d.target_id, d)
    }
  }

  return (
    <div
      className="sim-entity-map"
      style={{
        height: heightPx,
        width: '100%',
        minHeight: heightPx,
        zIndex: 0,
      }}
    >
      <MapContainer
        center={DEFAULT_CENTER}
        zoom={DEFAULT_ZOOM}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <InvalidateSize />
        <FitBounds
          features={features}
          highlightedIds={highlightedIds}
          diversionMarkers={diversionMarkers}
        />
        {ordered.map((feature) => {
          const pos = pointCoords(feature)
          if (!pos) return null
          const isHit = highlighted.has(feature.properties.id)
          const callsign =
            typeof feature.properties.attributes.call_sign === 'string'
              ? feature.properties.attributes.call_sign
              : feature.properties.id
          return (
            <CircleMarker
              key={feature.properties.id}
              center={pos}
              radius={isHit ? 14 : 6}
              pathOptions={{
                color: isHit ? '#A30000' : '#0066CC',
                fillColor: isHit ? '#C9190B' : '#8BC1F7',
                fillOpacity: isHit ? 1 : 0.55,
                weight: isHit ? 3 : 1,
              }}
            >
              <Popup>
                <strong>{callsign}</strong>
                <br />
                {feature.properties.id}
                <br />
                Type: {feature.properties.type}
                <br />
                Status: {feature.properties.status ?? '—'}
                {isHit ? (
                  <>
                    <br />
                    <em>Affected by simulation</em>
                  </>
                ) : null}
              </Popup>
            </CircleMarker>
          )
        })}
        {diversionMarkers.map((d) => {
          const from = byId.get(d.entity_id)
          const fromPos = from ? pointCoords(from) : null
          if (!fromPos) return null
          return (
            <Polyline
              key={`route-${d.entity_id}-${d.target_id}`}
              positions={[fromPos, [d.latitude, d.longitude]]}
              pathOptions={{
                color: ROUTE_LINE,
                weight: 2,
                opacity: 0.85,
                dashArray: '8 6',
              }}
            >
              <Popup>
                Recommended diversion
                <br />
                {d.entity_id} → {d.target_label}
                {d.rationale ? (
                  <>
                    <br />
                    <em>{d.rationale}</em>
                  </>
                ) : null}
              </Popup>
            </Polyline>
          )
        })}
        {[...uniqueTargets.values()].map((d) => (
          <CircleMarker
            key={`diversion-${d.target_id}`}
            center={[d.latitude, d.longitude]}
            radius={12}
            pathOptions={{
              color: DIVERSION_STROKE,
              fillColor: DIVERSION_FILL,
              fillOpacity: 1,
              weight: 3,
            }}
          >
            <Popup>
              <strong>{d.target_label}</strong>
              <br />
              Diversion airport ({d.target_id})
              {d.rationale ? (
                <>
                  <br />
                  <em>{d.rationale}</em>
                </>
              ) : null}
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}
