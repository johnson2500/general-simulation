import { useEffect, useMemo, useState } from 'react'
import {
  Pagination,
  SearchInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core'
import {
  Table,
  Thead,
  Tr,
  Th,
  Tbody,
  Td,
  type ThProps,
} from '@patternfly/react-table'
import type { EntityGeoJsonFeature } from '../types/api'

const PAGE_SIZE = 25

type SortableColumn =
  | 'id'
  | 'callsign'
  | 'origin'
  | 'route'
  | 'status'
  | 'revenue'
  | 'position'
  | 'updatedAt'

interface FlightRow {
  id: string
  callsign: string
  origin: string
  route: string
  status: string
  revenue: string
  revenueUsd: number
  position: string
  updatedAt: string
  updatedAtMs: number
}

const COLUMN_INDEX: SortableColumn[] = [
  'id',
  'callsign',
  'origin',
  'route',
  'status',
  'revenue',
  'position',
  'updatedAt',
]

export interface AffectedFlightsTableProps {
  highlightedIds: string[]
  features: EntityGeoJsonFeature[]
}

function attrString(
  attrs: Record<string, unknown>,
  ...keys: string[]
): string {
  for (const key of keys) {
    const value = attrs[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return '—'
}

function attrNumber(
  attrs: Record<string, unknown>,
  ...keys: string[]
): number | null {
  for (const key of keys) {
    const value = attrs[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim()) {
      const n = Number(value)
      if (Number.isFinite(n)) return n
    }
  }
  return null
}

function formatRevenue(amount: number | null): string {
  if (amount === null) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `$${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
  }
}

function coordinatesOf(feature: EntityGeoJsonFeature | undefined): string {
  if (!feature?.geometry || feature.geometry.type !== 'Point') return '—'
  const coords = feature.geometry.coordinates
  if (!Array.isArray(coords) || coords.length < 2) return '—'
  const [lon, lat] = coords as number[]
  if (typeof lon !== 'number' || typeof lat !== 'number') return '—'
  return `${lat.toFixed(3)}, ${lon.toFixed(3)}`
}

function compareRows(
  a: FlightRow,
  b: FlightRow,
  column: SortableColumn,
  direction: 'asc' | 'desc',
): number {
  let result = 0
  if (column === 'updatedAt') {
    result = a.updatedAtMs - b.updatedAtMs
  } else if (column === 'revenue') {
    result = a.revenueUsd - b.revenueUsd
  } else {
    result = a[column].localeCompare(b[column], undefined, {
      sensitivity: 'base',
      numeric: true,
    })
  }
  return direction === 'asc' ? result : -result
}

function rowMatchesFilter(row: FlightRow, filter: string): boolean {
  if (!filter) return true
  const q = filter.toLowerCase()
  return (
    row.id.toLowerCase().includes(q) ||
    row.callsign.toLowerCase().includes(q) ||
    row.origin.toLowerCase().includes(q) ||
    row.route.toLowerCase().includes(q) ||
    row.status.toLowerCase().includes(q) ||
    row.revenue.toLowerCase().includes(q) ||
    row.position.toLowerCase().includes(q)
  )
}

export function AffectedFlightsTable({
  highlightedIds,
  features,
}: AffectedFlightsTableProps) {
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('')
  const [activeSortIndex, setActiveSortIndex] = useState<number>(0)
  const [activeSortDirection, setActiveSortDirection] = useState<
    'asc' | 'desc'
  >('asc')

  useEffect(() => {
    setPage(1)
    setFilter('')
    setActiveSortIndex(0)
    setActiveSortDirection('asc')
  }, [highlightedIds])

  useEffect(() => {
    setPage(1)
  }, [filter, activeSortIndex, activeSortDirection])

  const byId = useMemo(() => {
    const map = new Map<string, EntityGeoJsonFeature>()
    for (const f of features) {
      map.set(f.properties.id, f)
    }
    return map
  }, [features])

  const rows = useMemo((): FlightRow[] => {
    return highlightedIds
      .map((id) => {
        const feature = byId.get(id)
        // Cargo has no map geometry; keep this table flight-focused.
        if (feature && feature.properties.type === 'cargo_item') return null
        if (!feature && id.startsWith('cargo-')) return null
        const attrs = feature?.properties.attributes ?? {}
        const revenueUsd = attrNumber(attrs, 'revenue_usd', 'value_usd')
        const updatedRaw = feature?.properties.updated_at
        const updatedMs = updatedRaw ? Date.parse(updatedRaw) : 0
        return {
          id,
          callsign: attrString(attrs, 'call_sign', 'callsign'),
          origin: attrString(attrs, 'origin_country', 'origin'),
          route: attrString(attrs, 'route'),
          status: feature?.properties.status ?? '—',
          revenue: formatRevenue(revenueUsd),
          revenueUsd: revenueUsd ?? -1,
          position: coordinatesOf(feature),
          updatedAt: updatedRaw ? new Date(updatedRaw).toLocaleString() : '—',
          updatedAtMs: Number.isFinite(updatedMs) ? updatedMs : 0,
        }
      })
      .filter((row): row is FlightRow => row !== null)
  }, [highlightedIds, byId])

  const visibleRows = useMemo(() => {
    const column = COLUMN_INDEX[activeSortIndex] ?? 'id'
    return rows
      .filter((row) => rowMatchesFilter(row, filter.trim()))
      .sort((a, b) => compareRows(a, b, column, activeSortDirection))
  }, [rows, filter, activeSortIndex, activeSortDirection])

  if (rows.length === 0) return null

  const pageSafe = Math.min(
    page,
    Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE) || 1),
  )
  const start = (pageSafe - 1) * PAGE_SIZE
  const pageRows = visibleRows.slice(start, start + PAGE_SIZE)

  const getSortParams = (columnIndex: number): ThProps['sort'] => ({
    sortBy: {
      index: activeSortIndex,
      direction: activeSortDirection,
    },
    onSort: (_event, index, direction) => {
      setActiveSortIndex(index)
      setActiveSortDirection(direction)
    },
    columnIndex,
  })

  return (
    <div style={{ marginTop: '1.5rem' }}>
      <Title headingLevel="h2" size="lg">
        Affected flights ({visibleRows.length}
        {filter.trim() ? ` of ${rows.length}` : ''})
      </Title>
      <Toolbar id="affected-flights-toolbar">
        <ToolbarContent>
          <ToolbarItem>
            <SearchInput
              placeholder="Filter by ID, callsign, origin, route, status…"
              value={filter}
              onChange={(_e, value) => setFilter(value)}
              onClear={() => setFilter('')}
              aria-label="Filter affected flights"
            />
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>
      <Table aria-label="Affected flights" variant="compact">
        <Thead>
          <Tr>
            <Th sort={getSortParams(0)}>Entity ID</Th>
            <Th sort={getSortParams(1)}>Callsign</Th>
            <Th sort={getSortParams(2)}>Origin</Th>
            <Th sort={getSortParams(3)}>Route</Th>
            <Th sort={getSortParams(4)}>Status</Th>
            <Th sort={getSortParams(5)}>Revenue</Th>
            <Th sort={getSortParams(6)}>Position (lat, lon)</Th>
            <Th sort={getSortParams(7)}>Updated</Th>
          </Tr>
        </Thead>
        <Tbody>
          {pageRows.length === 0 ? (
            <Tr>
              <Td colSpan={8}>No flights match the current filter.</Td>
            </Tr>
          ) : (
            pageRows.map((row) => (
              <Tr key={row.id}>
                <Td dataLabel="Entity ID">{row.id}</Td>
                <Td dataLabel="Callsign">{row.callsign}</Td>
                <Td dataLabel="Origin">{row.origin}</Td>
                <Td dataLabel="Route">{row.route}</Td>
                <Td dataLabel="Status">{row.status}</Td>
                <Td dataLabel="Revenue">{row.revenue}</Td>
                <Td dataLabel="Position">{row.position}</Td>
                <Td dataLabel="Updated">{row.updatedAt}</Td>
              </Tr>
            ))
          )}
        </Tbody>
      </Table>
      <Pagination
        itemCount={visibleRows.length}
        perPage={PAGE_SIZE}
        page={pageSafe}
        onSetPage={(_e, p) => setPage(p)}
        perPageOptions={[{ title: String(PAGE_SIZE), value: PAGE_SIZE }]}
        isCompact
        widgetId="affected-flights-pagination"
        style={{ marginTop: '0.5rem' }}
      />
    </div>
  )
}
