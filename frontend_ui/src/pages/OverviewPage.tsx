import { useEffect, useState } from 'react'
import {
  Alert,
  Card,
  CardBody,
  CardTitle,
  Flex,
  FlexItem,
  Gallery,
  GalleryItem,
  PageSection,
  Spinner,
  Title,
} from '@patternfly/react-core'
import { getStats } from '../api/admin'
import { getHealth } from '../api/health'
import { ApiError } from '../api/client'
import type { AdminStats, HealthResponse } from '../types/api'

const STAT_LABELS: { key: keyof AdminStats; label: string }[] = [
  { key: 'entity_count', label: 'Live entities' },
  { key: 'state_count', label: 'Entity states' },
  { key: 'graph_nodes', label: 'Graph nodes' },
  { key: 'graph_events', label: 'Simulation events' },
  { key: 'scenario_count', label: 'Scenarios' },
]

export function OverviewPage() {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [s, h] = await Promise.all([getStats(), getHealth()])
        if (!cancelled) {
          setStats(s)
          setHealth(h)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : 'Failed to load overview. Is the API running on :8000?',
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Overview</Title>
        <p>
          Live store and dependency-graph summary for the General Simulation
          platform.
        </p>
      </PageSection>
      <PageSection>
        {loading ? (
          <Spinner aria-label="Loading overview" />
        ) : error ? (
          <Alert variant="danger" title="Could not load overview" isInline>
            {error}
          </Alert>
        ) : (
          <Flex direction={{ default: 'column' }} gap={{ default: 'gapMd' }}>
            {health ? (
              <FlexItem>
                <Alert
                  variant={health.status === 'ok' ? 'success' : 'warning'}
                  title={`API status: ${health.status}`}
                  isInline
                >
                  Postgres: {health.db}
                </Alert>
              </FlexItem>
            ) : null}
            <FlexItem>
              <Gallery hasGutter minWidths={{ default: '200px' }}>
                {stats
                  ? STAT_LABELS.map(({ key, label }) => (
                      <GalleryItem key={key}>
                        <Card isCompact>
                          <CardTitle>{label}</CardTitle>
                          <CardBody>
                            <Title headingLevel="h2" size="2xl">
                              {stats[key]}
                            </Title>
                          </CardBody>
                        </Card>
                      </GalleryItem>
                    ))
                  : null}
              </Gallery>
            </FlexItem>
          </Flex>
        )}
      </PageSection>
    </>
  )
}
