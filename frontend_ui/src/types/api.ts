/** Mirrors FastAPI response shapes from /health, /admin/*, and /query. */

export interface HealthResponse {
  status: 'ok' | 'degraded' | string
  db: 'reachable' | 'unreachable' | string
}

export interface AdminStats {
  entity_count: number
  state_count: number
  graph_nodes: number
  graph_events: number
  scenario_count: number
}

export interface EntitySummary {
  id: string
  type: string
  attributes: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface EntityState {
  status: string
  recorded_at: string
  attributes: Record<string, unknown>
}

export interface EntityDetail extends EntitySummary {
  states: EntityState[]
}

export interface EntityListResponse {
  total: number
  limit: number
  offset: number
  items: EntitySummary[]
}

export interface GraphNode {
  id: string
  type?: string
  [key: string]: unknown
}

export interface GraphEdge {
  from_id: string
  edge_type: string
  to_id: string
}

export interface GraphEvent {
  id: string
  scenario_id: string
  description?: string
  [key: string]: unknown
}

export interface InjectEventRequest {
  id: string
  scenario_id: string
  description: string
  affected_entity_ids: string[]
  attributes?: Record<string, unknown>
}

export interface ResponseOptionOut {
  rank: number
  label: string
  description: string
  estimated_impact_reduction: number
}

export interface EntityValueOut {
  entity_id: string
  value_usd: number
}

export interface RecommendedRerouteOut {
  entity_id: string
  target_id: string
  target_label: string
  latitude: number
  longitude: number
  rationale: string
}

export interface SolverResultOut {
  affected_count: number
  max_chain_length: number
  impact_score: number
  total_value_at_risk: number
  currency: string
  value_breakdown: EntityValueOut[]
  response_options: ResponseOptionOut[]
  recommended_reroutes?: RecommendedRerouteOut[]
  explanation: string
}

export interface ToolCallRecord {
  tool_name: string
  arguments: Record<string, unknown>
  output: Record<string, unknown>
}

export interface QueryRequest {
  question: string
  scenario_id: string
}

export interface QueryResponse {
  question: string
  scenario_id: string
  answer: string
  affected_entities: string[]
  solver: SolverResultOut
  tool_call_trace: ToolCallRecord[]
}

export interface GeoJsonGeometry {
  type: string
  coordinates: number[] | number[][] | number[][][]
}

export interface EntityGeoJsonProperties {
  id: string
  type: string
  status: string | null
  attributes: Record<string, unknown>
  updated_at: string
}

export interface EntityGeoJsonFeature {
  type: 'Feature'
  id: string
  geometry: GeoJsonGeometry
  properties: EntityGeoJsonProperties
}

export interface EntityFeatureCollection {
  type: 'FeatureCollection'
  features: EntityGeoJsonFeature[]
}
