import {
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  List,
  ListItem,
  Title,
} from '@patternfly/react-core'
import type { SolverResultOut } from '../types/api'

function formatCurrency(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `${currency} ${amount.toLocaleString(undefined, {
      maximumFractionDigits: 0,
    })}`
  }
}

export function SolverSummary({ solver }: { solver: SolverResultOut }) {
  const currency = solver.currency || 'USD'
  const breakdown = solver.value_breakdown ?? []
  const reroutes = solver.recommended_reroutes ?? []

  return (
    <Card isCompact>
      <CardTitle>Solver result</CardTitle>
      <CardBody>
        <DescriptionList isHorizontal isCompact>
          <DescriptionListGroup>
            <DescriptionListTerm>Impact score</DescriptionListTerm>
            <DescriptionListDescription>
              {solver.impact_score.toFixed(3)}
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Value at risk</DescriptionListTerm>
            <DescriptionListDescription>
              {formatCurrency(solver.total_value_at_risk ?? 0, currency)}
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Affected count</DescriptionListTerm>
            <DescriptionListDescription>
              {solver.affected_count}
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Max chain length</DescriptionListTerm>
            <DescriptionListDescription>
              {solver.max_chain_length}
            </DescriptionListDescription>
          </DescriptionListGroup>
        </DescriptionList>
        {solver.explanation ? (
          <p style={{ marginTop: '1rem' }}>{solver.explanation}</p>
        ) : null}
        {breakdown.length > 0 ? (
          <>
            <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
              Value breakdown ({breakdown.length})
            </Title>
            <List isPlain>
              {breakdown.map((row) => (
                <ListItem key={row.entity_id}>
                  <strong>{row.entity_id}</strong> —{' '}
                  {formatCurrency(row.value_usd, currency)}
                </ListItem>
              ))}
            </List>
          </>
        ) : null}
        {solver.response_options.length > 0 ? (
          <>
            <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
              Response options
            </Title>
            <List isPlain>
              {solver.response_options.map((opt) => (
                <ListItem key={opt.rank}>
                  <strong>
                    #{opt.rank} {opt.label}
                  </strong>{' '}
                  — {opt.description}{' '}
                  <em>
                    (Δ impact ≈ {opt.estimated_impact_reduction.toFixed(3)})
                  </em>
                </ListItem>
              ))}
            </List>
          </>
        ) : null}
        {reroutes.length > 0 ? (
          <>
            <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
              Recommended diversions
            </Title>
            <List isPlain>
              {reroutes.map((r) => (
                <ListItem key={`${r.entity_id}-${r.target_id}`}>
                  <strong>{r.entity_id}</strong> → {r.target_label}{' '}
                  <em>({r.target_id})</em>
                  {r.rationale ? <> — {r.rationale}</> : null}
                </ListItem>
              ))}
            </List>
          </>
        ) : null}
      </CardBody>
    </Card>
  )
}
