import {
  listGraphEdges,
  listGraphEvents,
  listGraphNodes,
  listScenarios,
} from '../api/admin'
import type { GraphEdge, GraphEvent, GraphNode } from '../types/api'

export interface GraphBundle {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: GraphEvent[]
  scenarios: string[]
}

export async function loadGraphBundle(
  scenarioId?: string,
): Promise<GraphBundle> {
  const [nodes, edges, events, scenarios] = await Promise.all([
    listGraphNodes(500),
    listGraphEdges(1000),
    listGraphEvents(scenarioId),
    listScenarios(),
  ])
  return { nodes, edges, events, scenarios }
}
