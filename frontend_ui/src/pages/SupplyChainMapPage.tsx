import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Flex,
  FlexItem,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Grid,
  GridItem,
  PageSection,
  Spinner,
  TextArea,
  Title,
} from '@patternfly/react-core'
import { getEntitiesGeoJson, listScenarios } from '../api/admin'
import { runQuery } from '../api/query'
import { ApiError } from '../api/client'
import { AffectedFlightsTable } from '../components/AffectedFlightsTable'
import { EntityMap } from '../components/EntityMap'
import { SolverSummary } from '../components/SolverSummary'
import type { EntityFeatureCollection, QueryResponse } from '../types/api'
import ReactMarkdown from 'react-markdown'

export function SupplyChainMapPage() {
  const [collection, setCollection] = useState<EntityFeatureCollection | null>(
    null,
  )
  const [scenarios, setScenarios] = useState<string[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [question, setQuestion] = useState(
    'UK airspace is closed due to a NATS GPS failure. Which aircraft are affected, what diversions should be issued, and what is the estimated cost of impact?',
  )
  const [loadingMap, setLoadingMap] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResponse | null>(null)
  const [highlighted, setHighlighted] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoadingMap(true)
      try {
        // Europe-ish bbox keeps the demo / UK scenario visible even when
        // OpenSky has filled Postgres with worldwide aircraft.
        const [geo, sc] = await Promise.all([
          getEntitiesGeoJson({
            bbox: '-15,35,40,62',
            limit: 3000,
          }),
          listScenarios(),
        ])
        if (cancelled) return
        setCollection(geo)
        setScenarios(sc)
        if (sc.length > 0) setScenarioId(sc[0])
        setError(null)
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : 'Failed to load map entities. Re-run seed_demo.py?',
          )
        }
      } finally {
        if (!cancelled) setLoadingMap(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const onRun = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!scenarioId.trim()) return
    setRunning(true)
    setError(null)
    try {
      const res = await runQuery({
        scenario_id: scenarioId.trim(),
        question: question.trim(),
      })
      setResult(res)
      setHighlighted(res.affected_entities)

      // Ensure affected entities are present on the map (OpenSky worldwide
      // loads can push demo/scenario IDs out of the default geojson page).
      if (res.affected_entities.length > 0) {
        const overlay = await getEntitiesGeoJson({
          ids: res.affected_entities,
          limit: res.affected_entities.length,
        })
        setCollection((prev) => {
          const byId = new Map(
            (prev?.features ?? []).map((f) => [f.properties.id, f]),
          )
          for (const f of overlay.features) {
            byId.set(f.properties.id, f)
          }
          return {
            type: 'FeatureCollection',
            features: [...byId.values()],
          }
        })
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Simulation query failed')
    } finally {
      setRunning(false)
    }
  }

  const clearOverlay = () => {
    setHighlighted([])
    setResult(null)
  }

  const graphLink =
    result && result.affected_entities.length > 0
      ? `/graph?scenario=${encodeURIComponent(result.scenario_id)}&highlight=${encodeURIComponent(result.affected_entities.join(','))}`
      : result
        ? `/graph?scenario=${encodeURIComponent(result.scenario_id)}`
        : '/graph'

  const features = collection?.features ?? []

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Supply chain map</Title>
        <p>
          Live entity positions from PostGIS. Run a simulation to highlight
          affected assets (red) and recommended diversion airports (green)
          with dashed diversion routes.
        </p>
      </PageSection>
      <PageSection>
        {error ? (
          <Alert
            variant="danger"
            title={error}
            isInline
            style={{ marginBottom: '1rem' }}
          />
        ) : null}
        <Grid hasGutter>
          <GridItem span={12} md={8}>
            {loadingMap ? (
              <Spinner aria-label="Loading map" />
            ) : (
              <EntityMap
                features={features}
                highlightedIds={highlighted}
                diversionMarkers={result?.solver.recommended_reroutes ?? []}
              />
            )}
            <p
              style={{
                marginTop: '0.5rem',
                color: 'var(--pf-t--global--text--color--subtle)',
              }}
            >
              {features.length} entities on map
              {highlighted.length > 0
                ? ` · ${highlighted.length} affected`
                : ''}
              {(result?.solver.recommended_reroutes?.length ?? 0) > 0
                ? ` · ${result?.solver.recommended_reroutes?.length} diversion${(result?.solver.recommended_reroutes?.length ?? 0) === 1 ? '' : 's'}`
                : ''}
            </p>
            <AffectedFlightsTable
              highlightedIds={highlighted}
              features={features}
            />
          </GridItem>
          <GridItem span={12} md={4}>
            <Title headingLevel="h2" size="lg">
              Run simulation
            </Title>
            <Form onSubmit={(e) => void onRun(e)}>
              <FormGroup label="Scenario" isRequired fieldId="map-scenario">
                <FormSelect
                  id="map-scenario"
                  value={scenarioId}
                  onChange={(_e, v) => setScenarioId(v)}
                  aria-label="Scenario"
                >
                  {scenarios.length === 0 ? (
                    <FormSelectOption value="" label="No scenarios" />
                  ) : null}
                  {scenarios.map((s) => (
                    <FormSelectOption key={s} value={s} label={s} />
                  ))}
                </FormSelect>
              </FormGroup>
              <FormGroup label="Question" isRequired fieldId="map-question">
                <TextArea
                  id="map-question"
                  value={question}
                  onChange={(_e, v) => setQuestion(v)}
                  rows={5}
                  isRequired
                />
              </FormGroup>
              <Flex spaceItems={{ default: 'spaceItemsSm' }}>
                <FlexItem>
                  <Button type="submit" variant="primary" isLoading={running}>
                    Run simulation
                  </Button>
                </FlexItem>
                <FlexItem>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={clearOverlay}
                    isDisabled={highlighted.length === 0 && !result}
                  >
                    Clear overlay
                  </Button>
                </FlexItem>
              </Flex>
            </Form>

            {result ? (
              <Flex
                direction={{ default: 'column' }}
                spaceItems={{ default: 'spaceItemsMd' }}
                style={{ marginTop: '1rem' }}
              >
                <FlexItem>
                  <Card isCompact>
                    <CardTitle>
                      Answer <Link to={graphLink}>View on graph</Link>
                    </CardTitle>
                    <CardBody>
                      <ReactMarkdown
                        components={{
                          // Apply PatternFly styles to markdown elements
                          h1: ({ node, ...props }) => <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold' }} {...props} />,
                          h2: ({ node, ...props }) => <h2 style={{ fontSize: '1.3rem', fontWeight: 'bold' }} {...props} />,
                          h3: ({ node, ...props }) => <h3 style={{ fontSize: '1.2rem', fontWeight: 'bold' }} {...props} />,
                          h4: ({ node, ...props }) => <h4 style={{ fontSize: '1.1rem', fontWeight: 'bold' }} {...props} />,
                          h5: ({ node, ...props }) => <h5 style={{ fontSize: '1rem', fontWeight: 'bold' }} {...props} />,
                          h6: ({ node, ...props }) => <h6 style={{ fontSize: '0.9rem', fontWeight: 'bold' }} {...props} />,
                          p: ({ node, ...props }) => <p style={{ marginBottom: '0.5rem' }} {...props} />,
                          ul: ({ node, ...props }) => <ul style={{ paddingLeft: '1.5rem', marginBottom: '0.5rem' }} {...props} />,
                          ol: ({ node, ...props }) => <ol style={{ paddingLeft: '1.5rem', marginBottom: '0.5rem' }} {...props} />,
                          li: ({ node, ...props }) => <li style={{ marginBottom: '0.25rem' }} {...props} />,
                          strong: ({ node, ...props }) => <strong style={{ fontWeight: 'bold' }} {...props} />,
                          em: ({ node, ...props }) => <em style={{ fontStyle: 'italic' }} {...props} />,
                          code: ({ node, ...props }) => <code style={{ fontFamily: 'monospace', backgroundColor: '#f0f0f0', padding: '2px 4px', borderRadius: '3px' }} {...props} />,
                          pre: ({ node, ...props }) => <pre style={{ fontFamily: 'monospace', backgroundColor: '#f0f0f0', padding: '8px', borderRadius: '3px', overflowX: 'auto' }} {...props} />,
                          blockquote: ({ node, ...props }) => <blockquote style={{ borderLeft: '4px solid #0066cc', paddingLeft: '1rem', margin: '0.5rem 0' }} {...props} />,
                        }}
                      >
                        {result.answer}
                      </ReactMarkdown>
                    </CardBody>
                  </Card>
                </FlexItem>
                <FlexItem>
                  <SolverSummary solver={result.solver} />
                </FlexItem>
              </Flex>
            ) : null}
          </GridItem>
        </Grid>
      </PageSection>
    </>
  )
}
