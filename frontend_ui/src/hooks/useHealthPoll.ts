import { useEffect, useState } from 'react'
import { getHealth } from '../api/health'
import type { HealthResponse } from '../types/api'

const POLL_MS = 15_000

export function useHealthPoll(): HealthResponse | null {
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    let cancelled = false

    const tick = async () => {
      try {
        const h = await getHealth()
        if (!cancelled) setHealth(h)
      } catch {
        if (!cancelled) setHealth({ status: 'error', db: 'unreachable' })
      }
    }

    void tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  return health
}
