import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/health'
import type { HealthResponse } from '../types/api'

interface UseHealthResult {
  health: HealthResponse | null
  loading: boolean
  error: string | null
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    fetchHealth()
      .then((result) => {
        if (!cancelled) setHealth(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unknown error')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  return { health, loading, error }
}
