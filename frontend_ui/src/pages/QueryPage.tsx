import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Label,
  LabelGroup,
  PageSection,
  Spinner,
  TextArea,
  TextInput,
  Title,
  Flex,
  FlexItem,
} from '@patternfly/react-core'
import { listScenarios } from '../api/admin'
import { runQuery } from '../api/query'
import { ApiError } from '../api/client'
import { SolverSummary } from '../components/SolverSummary'
import { ToolTrace } from '../components/ToolTrace'
import type { QueryResponse } from '../types/api'

export function QueryPage() {
  const [scenarios, setScenarios] = useState<string[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [customScenario, setCustomScenario] = useState(false)
  const [question, setQuestion] = useState(
    'Which entities are affected and what response options should we consider?',
  )
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResponse | null>(null)

  useEffect(() => {
    void listScenarios()
      .then((s) => {
        setScenarios(s)
        if (s.length > 0) setScenarioId(s[0])
      })
      .catch(() => setScenarios([]))
  }, [])

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await runQuery({
        scenario_id: scenarioId.trim(),
        question: question.trim(),
      })
      setResult(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Query failed')
    } finally {
      setLoading(false)
    }
  }

  const graphLink =
    result && result.affected_entities.length > 0
      ? `/graph?scenario=${encodeURIComponent(result.scenario_id)}&highlight=${encodeURIComponent(result.affected_entities.join(','))}`
      : result
        ? `/graph?scenario=${encodeURIComponent(result.scenario_id)}`
        : '/graph'

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Impact query</Title>
        <p>
          Ask a natural-language question about a scenario. The ReAct agent
          traverses the graph, runs the solver, and returns an auditable answer.
        </p>
      </PageSection>
      <PageSection>
        <Form onSubmit={(e) => void onSubmit(e)} style={{ maxWidth: 720 }}>
          <FormGroup label="Scenario" isRequired fieldId="scenario">
            {customScenario ? (
              <TextInput
                id="scenario"
                value={scenarioId}
                onChange={(_e, v) => setScenarioId(v)}
                isRequired
              />
            ) : (
              <FormSelect
                id="scenario"
                value={scenarioId}
                onChange={(_e, v) => {
                  if (v === '__custom__') {
                    setCustomScenario(true)
                    setScenarioId('')
                  } else {
                    setScenarioId(v)
                  }
                }}
                aria-label="Scenario"
              >
                {scenarios.length === 0 ? (
                  <FormSelectOption value="" label="No scenarios — type custom" />
                ) : null}
                {scenarios.map((s) => (
                  <FormSelectOption key={s} value={s} label={s} />
                ))}
                <FormSelectOption value="__custom__" label="Custom scenario ID…" />
              </FormSelect>
            )}
            {customScenario ? (
              <Button
                variant="link"
                isInline
                onClick={() => setCustomScenario(false)}
                style={{ marginTop: '0.25rem' }}
              >
                Pick from list
              </Button>
            ) : null}
          </FormGroup>
          <FormGroup label="Question" isRequired fieldId="question">
            <TextArea
              id="question"
              value={question}
              onChange={(_e, v) => setQuestion(v)}
              rows={4}
              isRequired
            />
          </FormGroup>
          <Button type="submit" variant="primary" isLoading={loading}>
            Run query
          </Button>
        </Form>

        {error ? (
          <Alert
            variant="danger"
            title={error}
            isInline
            style={{ marginTop: '1rem' }}
          />
        ) : null}

        {loading ? (
          <div style={{ marginTop: '1.5rem' }}>
            <Spinner aria-label="Running query" />{' '}
            Agent is investigating the scenario…
          </div>
        ) : null}

        {result ? (
          <Flex
            direction={{ default: 'column' }}
            spaceItems={{ default: 'spaceItemsMd' }}
            style={{ marginTop: '1.5rem' }}
          >
            <FlexItem>
              <Card>
                <CardTitle>Answer</CardTitle>
                <CardBody>
                  <p style={{ whiteSpace: 'pre-wrap' }}>{result.answer}</p>
                </CardBody>
              </Card>
            </FlexItem>
            <FlexItem>
              <SolverSummary solver={result.solver} />
            </FlexItem>
            <FlexItem>
              <Card isCompact>
                <CardTitle>
                  Affected entities ({result.affected_entities.length}){' '}
                  <Link to={graphLink}>View on graph</Link>
                </CardTitle>
                <CardBody>
                  {result.affected_entities.length === 0 ? (
                    <p>None reported.</p>
                  ) : (
                    <LabelGroup numLabels={20}>
                      {result.affected_entities.map((id) => (
                        <Label key={id} color="red">
                          {id}
                        </Label>
                      ))}
                    </LabelGroup>
                  )}
                </CardBody>
              </Card>
            </FlexItem>
            <FlexItem>
              <Card isCompact>
                <CardTitle>
                  Tool call trace ({result.tool_call_trace.length})
                </CardTitle>
                <CardBody>
                  <ToolTrace trace={result.tool_call_trace} />
                </CardBody>
              </Card>
            </FlexItem>
          </Flex>
        ) : null}
      </PageSection>
    </>
  )
}
