import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Drawer,
  DrawerContent,
  DrawerContentBody,
  DrawerPanelBody,
  DrawerPanelContent,
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
  PageSection,
  Pagination,
  SearchInput,
  Spinner,
  Title,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core'
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table'
import { getEntity, getEntityTypes, listEntities } from '../api/admin'
import { ApiError } from '../api/client'
import type { EntityDetail, EntitySummary } from '../types/api'

const PAGE_SIZE = 25

export function EntitiesPage() {
  const [types, setTypes] = useState<string[]>([])
  const [typeFilter, setTypeFilter] = useState('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [items, setItems] = useState<EntitySummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<EntityDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    void getEntityTypes()
      .then(setTypes)
      .catch(() => setTypes([]))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listEntities({
        type: typeFilter || undefined,
        search: debouncedSearch || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      })
      setItems(res.items)
      setTotal(res.total)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load entities')
    } finally {
      setLoading(false)
    }
  }, [typeFilter, debouncedSearch, page])

  useEffect(() => {
    void load()
  }, [load])

  const openDetail = async (id: string) => {
    setDetailLoading(true)
    try {
      const detail = await getEntity(id)
      setSelected(detail)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load entity')
    } finally {
      setDetailLoading(false)
    }
  }

  const panel = (
    <DrawerPanelContent>
      <DrawerPanelBody>
        {detailLoading ? (
          <Spinner aria-label="Loading entity" />
        ) : selected ? (
          <>
            <Title headingLevel="h2" size="lg">
              {selected.id}
            </Title>
            <DescriptionList isCompact style={{ marginTop: '1rem' }}>
              <DescriptionListGroup>
                <DescriptionListTerm>Type</DescriptionListTerm>
                <DescriptionListDescription>
                  {selected.type}
                </DescriptionListDescription>
              </DescriptionListGroup>
              <DescriptionListGroup>
                <DescriptionListTerm>Updated</DescriptionListTerm>
                <DescriptionListDescription>
                  {selected.updated_at}
                </DescriptionListDescription>
              </DescriptionListGroup>
            </DescriptionList>
            <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
              Attributes
            </Title>
            <CodeBlock>
              <CodeBlockCode>
                {JSON.stringify(selected.attributes, null, 2)}
              </CodeBlockCode>
            </CodeBlock>
            <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
              Recent states ({selected.states.length})
            </Title>
            {selected.states.length === 0 ? (
              <EmptyState titleText="No states" headingLevel="h4" variant="sm">
                <EmptyStateBody>No entity_state rows yet.</EmptyStateBody>
              </EmptyState>
            ) : (
              <Table aria-label="Entity states" variant="compact">
                <Thead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Recorded</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {selected.states.map((s, i) => (
                    <Tr key={`${s.recorded_at}-${i}`}>
                      <Td>{s.status}</Td>
                      <Td>{s.recorded_at}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            )}
          </>
        ) : (
          <EmptyState titleText="Select an entity" headingLevel="h4" variant="sm">
            <EmptyStateBody>
              Click a row to inspect attributes and state history.
            </EmptyStateBody>
          </EmptyState>
        )}
      </DrawerPanelBody>
    </DrawerPanelContent>
  )

  return (
    <>
      <PageSection>
        <Title headingLevel="h1">Entities</Title>
        <p>Live ground-truth entities from Postgres (PostGIS store).</p>
      </PageSection>
      <PageSection>
        <Flex
          spaceItems={{ default: 'spaceItemsMd' }}
          alignItems={{ default: 'alignItemsFlexEnd' }}
          style={{ marginBottom: '1rem' }}
        >
          <FlexItem>
            <FormSelect
              id="entity-type"
              value={typeFilter}
              onChange={(_e, v) => {
                setPage(1)
                setTypeFilter(v)
              }}
              aria-label="Filter by type"
              style={{ minWidth: 180 }}
            >
              <FormSelectOption value="" label="All types" />
              {types.map((t) => (
                <FormSelectOption key={t} value={t} label={t} />
              ))}
            </FormSelect>
          </FlexItem>
          <FlexItem flex={{ default: 'flex_1' }}>
            <SearchInput
              placeholder="Search by entity id"
              value={search}
              onChange={(_e, v) => {
                setPage(1)
                setSearch(v)
              }}
              onClear={() => {
                setPage(1)
                setSearch('')
              }}
            />
          </FlexItem>
        </Flex>

        {error ? (
          <Alert variant="danger" title={error} isInline style={{ marginBottom: '1rem' }} />
        ) : null}

        <Drawer isExpanded>
          <DrawerContent panelContent={panel}>
            <DrawerContentBody>
              {loading ? (
                <Spinner aria-label="Loading entities" />
              ) : (
                <>
                  <Table aria-label="Entities" variant="compact">
                    <Thead>
                      <Tr>
                        <Th>ID</Th>
                        <Th>Type</Th>
                        <Th>Updated</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {items.map((item) => (
                        <Tr
                          key={item.id}
                          isClickable
                          isRowSelected={selected?.id === item.id}
                          onRowClick={() => void openDetail(item.id)}
                        >
                          <Td dataLabel="ID">{item.id}</Td>
                          <Td dataLabel="Type">{item.type}</Td>
                          <Td dataLabel="Updated">{item.updated_at}</Td>
                        </Tr>
                      ))}
                    </Tbody>
                  </Table>
                  <Pagination
                    itemCount={total}
                    perPage={PAGE_SIZE}
                    page={page}
                    onSetPage={(_e, p) => setPage(p)}
                    widgetId="entities-pagination"
                    isCompact
                  />
                </>
              )}
            </DrawerContentBody>
          </DrawerContent>
        </Drawer>
      </PageSection>
    </>
  )
}
