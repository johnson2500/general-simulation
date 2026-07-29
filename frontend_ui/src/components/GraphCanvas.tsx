import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import type { GraphEdge, GraphEvent, GraphNode } from '../types/api'

export interface GraphCanvasProps {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events?: GraphEvent[]
  highlightedIds?: string[]
  onNodeSelect?: (nodeId: string | null, data: Record<string, unknown> | null) => void
  height?: string
}

function toElements(
  nodes: GraphNode[],
  edges: GraphEdge[],
  events: GraphEvent[],
  highlighted: Set<string>,
): ElementDefinition[] {
  const elements: ElementDefinition[] = []

  for (const n of nodes) {
    if (!n.id) continue
    const label =
      (typeof n.callsign === 'string' && n.callsign) ||
      (typeof n.type === 'string' && n.type) ||
      n.id
    elements.push({
      data: {
        ...n,
        id: n.id,
        label: String(label),
        kind: 'entity',
      },
      classes: highlighted.has(n.id) ? 'highlighted' : '',
    })
  }

  for (const e of events) {
    if (!e.id) continue
    elements.push({
      data: {
        ...e,
        id: e.id,
        label: e.scenario_id || e.id,
        kind: 'event',
      },
      classes: 'event',
    })
  }

  const nodeIds = new Set(elements.map((el) => el.data?.id).filter(Boolean))

  for (const edge of edges) {
    if (!nodeIds.has(edge.from_id) || !nodeIds.has(edge.to_id)) continue
    elements.push({
      data: {
        id: `${edge.from_id}-${edge.edge_type}-${edge.to_id}`,
        source: edge.from_id,
        target: edge.to_id,
        label: edge.edge_type,
      },
      classes: edge.edge_type === 'AFFECTED_BY' ? 'affected' : 'dependency',
    })
  }

  return elements
}

export function GraphCanvas({
  nodes,
  edges,
  events = [],
  highlightedIds = [],
  onNodeSelect,
  height = '520px',
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const onSelectRef = useRef(onNodeSelect)
  onSelectRef.current = onNodeSelect

  useEffect(() => {
    if (!containerRef.current) return

    const highlighted = new Set(highlightedIds)
    const cy = cytoscape({
      container: containerRef.current,
      elements: toElements(nodes, edges, events, highlighted),
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': '10px',
            color: '#151515',
            'background-color': '#8BC1F7',
            'border-width': 1,
            'border-color': '#0066CC',
            width: 36,
            height: 36,
            'text-wrap': 'ellipsis',
            'text-max-width': '72px',
          },
        },
        {
          selector: 'node.event',
          style: {
            'background-color': '#F4C145',
            'border-color': '#C58C00',
            shape: 'round-rectangle',
            width: 48,
            height: 32,
          },
        },
        {
          selector: 'node.highlighted',
          style: {
            'background-color': '#C9190B',
            'border-color': '#A30000',
            'border-width': 3,
            color: '#fff',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'curve-style': 'bezier',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.8,
            label: 'data(label)',
            'font-size': '8px',
            color: '#6A6E73',
            'text-rotation': 'autorotate',
            'line-color': '#8A8D90',
            'target-arrow-color': '#8A8D90',
          },
        },
        {
          selector: 'edge.affected',
          style: {
            'line-color': '#C9190B',
            'target-arrow-color': '#C9190B',
            'line-style': 'dashed',
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 3,
            'border-color': '#151515',
          },
        },
      ] as cytoscape.StylesheetStyle[],
      layout: {
        name: 'cose',
        animate: false,
        padding: 24,
        nodeRepulsion: () => 6000,
      },
    })

    cy.on('tap', 'node', (evt) => {
      const data = evt.target.data() as Record<string, unknown>
      onSelectRef.current?.(String(data.id), data)
    })
    cy.on('tap', (evt) => {
      if (evt.target === cy) onSelectRef.current?.(null, null)
    })

    cyRef.current = cy
    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [nodes, edges, events, highlightedIds])

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height,
        border: '1px solid var(--pf-t--global--border--color--default)',
        borderRadius: 'var(--pf-t--global--border--radius--small)',
        background: 'var(--pf-t--global--background--color--primary--default)',
      }}
    />
  )
}
