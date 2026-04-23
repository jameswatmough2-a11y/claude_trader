'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, RefreshCw, X } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LogEntry {
  id: number
  created_at: string
  level: 'INFO' | 'WARNING' | 'ERROR'
  component: string
  event_type: string
  symbol: string | null
  cycle_id: string | null
  message: string
  details_json: string | null
}

interface LogsResponse {
  total: number
  page: number
  page_size: number
  pages: number
  items: LogEntry[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const LEVEL_STYLES: Record<string, string> = {
  INFO: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  WARNING: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  ERROR: 'bg-red-500/10 text-red-400 border-red-500/20',
}

function LevelBadge({ level }: { level: string }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium',
      LEVEL_STYLES[level] ?? 'bg-muted text-muted-foreground',
    )}>
      {level}
    </span>
  )
}

function buildQuery(params: Record<string, string | number>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v !== undefined) p.set(k, String(v))
  }
  return p.toString()
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function LogsPage() {
  const { fmtTime } = useDisplayPrefs()

  const [data, setData] = useState<LogsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  // Filters
  const [level, setLevel] = useState('')
  const [component, setComponent] = useState('')
  const [symbol, setSymbol] = useState('')
  const [eventType, setEventType] = useState('')
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  // Available filter options
  const [components, setComponents] = useState<string[]>([])
  const [eventTypes, setEventTypes] = useState<string[]>([])

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const q = buildQuery({
        page,
        page_size: PAGE_SIZE,
        ...(level && { level }),
        ...(component && { component }),
        ...(symbol && { symbol }),
        ...(eventType && { event_type: eventType }),
      })
      const res = await fetch(`/api/logs?${q}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch logs')
    } finally {
      setLoading(false)
    }
  }, [page, level, component, symbol, eventType])

  useEffect(() => { fetchLogs() }, [fetchLogs])

  // Load filter options once
  useEffect(() => {
    fetch('/api/logs/components').then(r => r.ok ? r.json() : []).then(setComponents).catch(() => {})
    fetch('/api/logs/event-types').then(r => r.ok ? r.json() : []).then(setEventTypes).catch(() => {})
  }, [])

  function toggleExpand(id: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function clearFilters() {
    setLevel('')
    setComponent('')
    setSymbol('')
    setEventType('')
    setPage(1)
  }

  const hasFilters = level || component || symbol || eventType

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">System Logs</h1>
          {data && (
            <p className="text-sm text-muted-foreground mt-0.5">
              {data.total.toLocaleString()} entries
            </p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={fetchLogs} disabled={loading}>
          <RefreshCw className={cn('size-3.5 mr-1.5', loading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* ── Filters ── */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border bg-card p-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">Level</label>
          <select
            value={level}
            onChange={e => { setLevel(e.target.value); setPage(1) }}
            className="rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {['INFO', 'WARNING', 'ERROR'].map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">Component</label>
          <select
            value={component}
            onChange={e => { setComponent(e.target.value); setPage(1) }}
            className="rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {components.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">Symbol</label>
          <input
            type="text"
            placeholder="e.g. BTCUSDT"
            value={symbol}
            onChange={e => { setSymbol(e.target.value.toUpperCase()); setPage(1) }}
            className="w-28 rounded border bg-background px-2 py-1 text-sm font-mono"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">Event Type</label>
          <select
            value={eventType}
            onChange={e => { setEventType(e.target.value); setPage(1) }}
            className="rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {eventTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        {hasFilters && (
          <Button variant="ghost" size="sm" onClick={clearFilters} className="gap-1 text-muted-foreground">
            <X className="size-3.5" />
            Clear
          </Button>
        )}
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ── Table ── */}
      <div className="rounded-lg border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-xs font-medium text-muted-foreground">
                <th className="px-3 py-2 text-left w-4" />
                <th className="px-3 py-2 text-left whitespace-nowrap">Timestamp</th>
                <th className="px-3 py-2 text-left">Level</th>
                <th className="px-3 py-2 text-left">Component</th>
                <th className="px-3 py-2 text-left whitespace-nowrap">Event Type</th>
                <th className="px-3 py-2 text-left">Symbol</th>
                <th className="px-3 py-2 text-left">Message</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b">
                    {Array.from({ length: 7 }).map((_, j) => (
                      <td key={j} className="px-3 py-2">
                        <Skeleton className="h-4 w-full" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : data?.items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">
                    No log entries match the current filters.
                  </td>
                </tr>
              ) : (
                data?.items.map(entry => {
                  const isExpanded = expanded.has(entry.id)
                  const hasDetails = !!entry.details_json

                  return [
                    <tr
                      key={entry.id}
                      className={cn(
                        'border-b transition-colors',
                        hasDetails && 'cursor-pointer hover:bg-muted/30',
                        isExpanded && 'bg-muted/20',
                      )}
                      onClick={() => hasDetails && toggleExpand(entry.id)}
                    >
                      <td className="px-3 py-2 text-muted-foreground">
                        {hasDetails ? (
                          isExpanded
                            ? <ChevronDown className="size-3.5" />
                            : <ChevronRight className="size-3.5" />
                        ) : null}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap font-mono text-xs text-muted-foreground">
                        {fmtTime(entry.created_at)}
                      </td>
                      <td className="px-3 py-2">
                        <LevelBadge level={entry.level} />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{entry.component}</td>
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                        {entry.event_type}
                      </td>
                      <td className="px-3 py-2">
                        {entry.symbol && (
                          <Badge variant="secondary" className="px-1.5 py-0 text-xs font-mono">
                            {entry.symbol.replace('USDT', '')}
                          </Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 max-w-sm truncate" title={entry.message}>
                        {entry.message}
                      </td>
                    </tr>,
                    isExpanded && entry.details_json ? (
                      <tr key={`${entry.id}-detail`} className="border-b bg-muted/10">
                        <td colSpan={7} className="px-6 py-3">
                          <pre className="text-xs text-muted-foreground overflow-x-auto whitespace-pre-wrap">
                            {(() => {
                              try {
                                return JSON.stringify(JSON.parse(entry.details_json!), null, 2)
                              } catch {
                                return entry.details_json
                              }
                            })()}
                          </pre>
                        </td>
                      </tr>
                    ) : null,
                  ]
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Pagination ── */}
      {data && data.pages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Page {data.page} of {data.pages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
            >
              Previous
            </Button>
            <Button
              variant="outline" size="sm"
              onClick={() => setPage(p => Math.min(data.pages, p + 1))}
              disabled={page >= data.pages || loading}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
