import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Alert,
  CodeBlock,
  CodeBlockCode,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
  PageSection,
  Spinner,
  Title,
} from '@patternfly/react-core'
import { GraphCanvas } from '../components/GraphCanvas'
import { loadGraphBundle } from '../services/graphService'
import { ApiError } from '../api/client'
import type { GraphEdge, GraphEvent, GraphNode } from '../types/api'

export function GraphPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const scenarioParam = searchParams.get('scenario') ?? ''
  const highlightParam = searchParams.get('highlight') ?? ''

  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [events, setEvents] = useState<GraphEvent[]>([])
  const [scenarios, setScenarios] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null)

  const highlightedIds = useMemo(
    () => (highlightParam ? highlightParam.split(',').filter(Boolean) : []),
    [highlightParam],
  )

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const bundle = await loadGraphBundle(scenarioParam || undefined)
        if (cancelled) return
        setNodes(bundle.nodes)
        setEdges(bundle.edges)
        setEvents(bundle.events)
        setScenarios(bundle.scenarios)
        setError(null)
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : 'Failed to load graph',
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [scenarioParam])

  const filteredEdges = useMemo(() => {
    if (!scenarioParam) return edges
    const eventIds = new Set(events.map((e) => e.id))
    return edges.filter((e) => {
      if (e.edge_type !== 'AFFECTED_BY') return true
      return eventIds.has(e.to_id) || eventIds.has(e.from_id)
    })
  }, [edges, events, scenarioParam])

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Dependency graph</Title>
        <p>
          Neo4j entities, dependency edges, and simulation-event overlays.
        </p>
      </PageSection>
      <PageSection>
        <Flex
          spaceItems={{ default: 'spaceItemsMd' }}
          style={{ marginBottom: '1rem' }}
        >
          <FlexItem>
            <FormSelect
              id="scenario-filter"
              value={scenarioParam}
              aria-label="Filter by scenario"
              onChange={(_e, v) => {
                const next = new URLSearchParams(searchParams)
                if (v) next.set('scenario', v)
                else next.delete('scenario')
                setSearchParams(next)
              }}
              style={{ minWidth: 260 }}
            >
              <FormSelectOption value="" label="All scenarios" />
              {scenarios.map((s) => (
                <FormSelectOption key={s} value={s} label={s} />
              ))}
            </FormSelect>
          </FlexItem>
          {highlightedIds.length > 0 ? (
            <FlexItem>
              <Alert
                variant="info"
                isInline
                isPlain
                title={`${highlightedIds.length} entity(ies) highlighted from query`}
              />
            </FlexItem>
          ) : null}
        </Flex>

        {error ? (
          <Alert variant="danger" title={error} isInline />
        ) : loading ? (
          <Spinner aria-label="Loading graph" />
        ) : (
          <Flex>
            <FlexItem flex={{ default: 'flex_1' }}>
              <GraphCanvas
                nodes={nodes}
                edges={filteredEdges}
                events={events}
                highlightedIds={highlightedIds}
                onNodeSelect={(_id, data) => setSelected(data)}
              />
            </FlexItem>
            <FlexItem style={{ width: 320, marginLeft: '1rem' }}>
              <Title headingLevel="h2" size="md">
                Selection
              </Title>
              {selected ? (
                <CodeBlock>
                  <CodeBlockCode>
                    {JSON.stringify(selected, null, 2)}
                  </CodeBlockCode>
                </CodeBlock>
              ) : (
                <p>Click a node to inspect properties.</p>
              )}
            </FlexItem>
          </Flex>
        )}
      </PageSection>
    </>
  )
}
