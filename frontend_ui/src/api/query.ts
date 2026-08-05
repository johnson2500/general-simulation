import { apiFetch } from './client'
import type { QueryRequest, QueryResponse } from '../types/api'

export function runQuery(body: QueryRequest): Promise<QueryResponse> {
  return apiFetch<QueryResponse>('/query', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
