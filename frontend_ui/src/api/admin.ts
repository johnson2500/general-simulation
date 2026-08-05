import { apiFetch } from './client'
import type {
  AdminStats,
  EntityDetail,
  EntityFeatureCollection,
  EntityListResponse,
  GraphEdge,
  GraphEvent,
  GraphNode,
  InjectEventRequest,
} from '../types/api'

export function getStats(): Promise<AdminStats> {
  return apiFetch<AdminStats>('/admin/stats')
}

export function getEntityTypes(): Promise<string[]> {
  return apiFetch<string[]>('/admin/entity-types')
}

export function listEntities(params: {
  type?: string
  search?: string
  limit?: number
  offset?: number
}): Promise<EntityListResponse> {
  const query = new URLSearchParams()
  if (params.type) query.set('type', params.type)
  if (params.search) query.set('search', params.search)
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.offset != null) query.set('offset', String(params.offset))
  const qs = query.toString()
  return apiFetch<EntityListResponse>(`/admin/entities${qs ? `?${qs}` : ''}`)
}

export function getEntity(
  entityId: string,
  statesLimit = 20,
): Promise<EntityDetail> {
  return apiFetch<EntityDetail>(
    `/admin/entities/${encodeURIComponent(entityId)}?states_limit=${statesLimit}`,
  )
}

export function getEntitiesGeoJson(params?: {
  type?: string
  bbox?: string
  ids?: string[]
  limit?: number
}): Promise<EntityFeatureCollection> {
  const query = new URLSearchParams()
  if (params?.type) query.set('type', params.type)
  if (params?.bbox) query.set('bbox', params.bbox)
  if (params?.ids?.length) query.set('ids', params.ids.join(','))
  if (params?.limit != null) query.set('limit', String(params.limit))
  const qs = query.toString()
  return apiFetch<EntityFeatureCollection>(
    `/admin/entities/geojson${qs ? `?${qs}` : ''}`,
  )
}

export function listGraphNodes(limit = 100): Promise<GraphNode[]> {
  return apiFetch<GraphNode[]>(`/admin/graph/nodes?limit=${limit}`)
}

export function listScenarios(): Promise<string[]> {
  return apiFetch<string[]>('/admin/graph/scenarios')
}

export function listGraphEvents(scenarioId?: string): Promise<GraphEvent[]> {
  const qs = scenarioId
    ? `?scenario_id=${encodeURIComponent(scenarioId)}`
    : ''
  return apiFetch<GraphEvent[]>(`/admin/graph/events${qs}`)
}

export function listGraphEdges(limit = 200): Promise<GraphEdge[]> {
  return apiFetch<GraphEdge[]>(`/admin/graph/edges?limit=${limit}`)
}

export function injectEvent(
  body: InjectEventRequest,
): Promise<{ status: string; event_id: string }> {
  return apiFetch('/admin/graph/events', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function deleteScenario(
  scenarioId: string,
): Promise<{ status: string; scenario_id: string }> {
  return apiFetch(`/admin/graph/scenarios/${encodeURIComponent(scenarioId)}`, {
    method: 'DELETE',
  })
}
