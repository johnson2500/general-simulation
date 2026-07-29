import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  PageSection,
  TextArea,
  TextInput,
  Title,
  Flex,
  FlexItem,
  Spinner,
} from '@patternfly/react-core'
import { Table, Thead, Tr, Th, Tbody, Td, ActionsColumn } from '@patternfly/react-table'
import {
  deleteScenario,
  injectEvent,
  listGraphEvents,
  listScenarios,
} from '../api/admin'
import { ApiError } from '../api/client'
import type { GraphEvent } from '../types/api'

export function ScenariosPage() {
  const [scenarios, setScenarios] = useState<string[]>([])
  const [events, setEvents] = useState<GraphEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [eventId, setEventId] = useState('')
  const [scenarioId, setScenarioId] = useState('')
  const [description, setDescription] = useState('')
  const [affectedIds, setAffectedIds] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, e] = await Promise.all([listScenarios(), listGraphEvents()])
      setScenarios(s)
      setEvents(e)
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Failed to load scenarios',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const onInject = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setSuccess(null)
    setError(null)
    try {
      const affected_entity_ids = affectedIds
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      await injectEvent({
        id: eventId.trim(),
        scenario_id: scenarioId.trim(),
        description: description.trim(),
        affected_entity_ids,
      })
      setSuccess(`Injected event ${eventId.trim()}`)
      setEventId('')
      setDescription('')
      setAffectedIds('')
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Inject failed')
    } finally {
      setSubmitting(false)
    }
  }

  const onDelete = async (sid: string) => {
    if (!window.confirm(`Remove scenario "${sid}" from graph and vector store?`)) {
      return
    }
    setError(null)
    setSuccess(null)
    try {
      await deleteScenario(sid)
      setSuccess(`Removed scenario ${sid}`)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Delete failed')
    }
  }

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Scenarios</Title>
        <p>
          Inject or remove simulation-event overlays. Live entity data is never
          mutated.
        </p>
      </PageSection>
      <PageSection>
        {error ? (
          <Alert variant="danger" title={error} isInline style={{ marginBottom: '1rem' }} />
        ) : null}
        {success ? (
          <Alert variant="success" title={success} isInline style={{ marginBottom: '1rem' }} />
        ) : null}

        <Flex
          direction={{ default: 'column', lg: 'row' }}
          spaceItems={{ default: 'spaceItemsXl' }}
        >
          <FlexItem flex={{ default: 'flex_1' }}>
            <Title headingLevel="h2" size="lg">
              Existing scenarios
            </Title>
            {loading ? (
              <Spinner aria-label="Loading scenarios" />
            ) : scenarios.length === 0 ? (
              <p>No scenarios yet. Inject an event or run seed_demo.py.</p>
            ) : (
              <Table aria-label="Scenarios" variant="compact">
                <Thead>
                  <Tr>
                    <Th>Scenario ID</Th>
                    <Th>Events</Th>
                    <Th>Actions</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {scenarios.map((sid) => {
                    const count = events.filter((ev) => ev.scenario_id === sid)
                      .length
                    return (
                      <Tr key={sid}>
                        <Td dataLabel="Scenario">{sid}</Td>
                        <Td dataLabel="Events">{count}</Td>
                        <Td dataLabel="Actions" isActionCell>
                          <ActionsColumn
                            items={[
                              {
                                title: 'Delete',
                                onClick: () => void onDelete(sid),
                              },
                            ]}
                          />
                        </Td>
                      </Tr>
                    )
                  })}
                </Tbody>
              </Table>
            )}

            <Title headingLevel="h2" size="lg" style={{ marginTop: '1.5rem' }}>
              Events
            </Title>
            <Table aria-label="Events" variant="compact">
              <Thead>
                <Tr>
                  <Th>Event ID</Th>
                  <Th>Scenario</Th>
                  <Th>Description</Th>
                </Tr>
              </Thead>
              <Tbody>
                {events.map((ev) => (
                  <Tr key={ev.id}>
                    <Td dataLabel="Event">{ev.id}</Td>
                    <Td dataLabel="Scenario">{ev.scenario_id}</Td>
                    <Td dataLabel="Description">
                      {String(ev.description ?? '').slice(0, 120)}
                      {String(ev.description ?? '').length > 120 ? '…' : ''}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </FlexItem>

          <FlexItem flex={{ default: 'flex_1' }}>
            <Title headingLevel="h2" size="lg">
              Inject simulation event
            </Title>
            <Form onSubmit={(e) => void onInject(e)}>
              <FormGroup label="Event ID" isRequired fieldId="event-id">
                <TextInput
                  id="event-id"
                  value={eventId}
                  onChange={(_e, v) => setEventId(v)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Scenario ID" isRequired fieldId="scenario-id">
                <TextInput
                  id="scenario-id"
                  value={scenarioId}
                  onChange={(_e, v) => setScenarioId(v)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Description" isRequired fieldId="description">
                <TextArea
                  id="description"
                  value={description}
                  onChange={(_e, v) => setDescription(v)}
                  rows={4}
                  isRequired
                />
              </FormGroup>
              <FormGroup
                label="Affected entity IDs"
                isRequired
                fieldId="affected"
              >
                <TextArea
                  id="affected"
                  value={affectedIds}
                  onChange={(_e, v) => setAffectedIds(v)}
                  rows={3}
                  isRequired
                />
                <FormHelperText>
                  <HelperText>
                    <HelperTextItem>
                      Comma- or whitespace-separated entity IDs that receive
                      AFFECTED_BY edges.
                    </HelperTextItem>
                  </HelperText>
                </FormHelperText>
              </FormGroup>
              <Button type="submit" variant="primary" isLoading={submitting}>
                Inject event
              </Button>
            </Form>
          </FlexItem>
        </Flex>
      </PageSection>
    </>
  )
}
