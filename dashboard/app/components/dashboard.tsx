'use client'

import { useCallback, useEffect, useState } from 'react'
import { Activity, Layers, RefreshCw, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Health {
  status: string
  paper_trading: boolean
  tracked_symbols: string[]
  model: string
}

interface Asset {
  id: number
  symbol: string
  base_currency: string
  quote_currency: string
}

interface Position {
  id: number
  symbol: string
  snapshot_time: string
  side: string
  size: number
  entry_price: number | null
  wallet_balance: number | null
}

interface Decision {
  id: number
  snapshot_id: number
  symbol: string
  snapshot_time: string
  action: string
  confidence_score: number | null
  reasoning_summary: string | null
}

interface BotData {
  health: Health
  assets: Asset[]
  positions: Position[]
  decisions: Decision[]
}

// ── Data fetching ─────────────────────────────────────────────────────────────

async function fetchAll(): Promise<BotData> {
  const [health, assets, positions, decisions] = await Promise.all([
    fetch('/api/bot/health').then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() }),
    fetch('/api/bot/assets').then(r => r.json()),
    fetch('/api/bot/positions').then(r => r.json()),
    fetch('/api/bot/decisions').then(r => r.json()),
  ])
  return { health, assets, positions, decisions }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded px-2 py-0.5 text-xs font-medium transition-colors',
        active
          ? 'bg-secondary text-secondary-foreground'
          : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

function ActionBadge({ action }: { action: string }) {
  const variant =
    action === 'BUY' ? 'default' :
    action === 'SELL' ? 'destructive' :
    'secondary'
  return <Badge variant={variant}>{action}</Badge>
}

function ConfidenceCell({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted-foreground">—</span>
  const pct = Math.round(value * 100)
  return (
    <div className="flex items-center gap-2">
      <Progress value={pct} className="h-1.5 w-16" />
      <span className={cn('text-xs font-medium tabular-nums', pct < 60 && 'text-muted-foreground')}>
        {pct}%
      </span>
    </div>
  )
}

function SkeletonRows({ rows, cols }: { rows: number; cols: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((_, j) => (
            <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
          ))}
        </TableRow>
      ))}
    </>
  )
}

function EmptyRow({ cols, message }: { cols: number; message: string }) {
  return (
    <TableRow>
      <TableCell colSpan={cols} className="h-16 text-center text-sm text-muted-foreground">
        {message}
      </TableCell>
    </TableRow>
  )
}

function StatCard({
  title, value, sub, icon, loading,
}: {
  title: string; value: string | null; sub?: string; icon: React.ReactNode; loading: boolean
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-6 w-3/4" />
        ) : (
          <>
            <p className="truncate text-base font-semibold">{value ?? '—'}</p>
            {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Divider() {
  return <div className="h-3 w-px bg-border" />
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── Main component ────────────────────────────────────────────────────────────

export function Dashboard({ priceChart }: { priceChart?: React.ReactNode }) {
  const [data, setData] = useState<BotData | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  // Decisions filters
  const [decisionCycle, setDecisionCycle] = useState<'latest' | 'all'>('latest')
  const [decisionSymbol, setDecisionSymbol] = useState<string>('ALL')
  const [decisionAction, setDecisionAction] = useState<string>('ALL')

  // Positions filter
  const [positionView, setPositionView] = useState<'latest' | 'all'>('latest')

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    try {
      const result = await fetchAll()
      setData(result)
      setLastUpdated(new Date())
      setError(null)
    } catch {
      setError('Cannot reach bot API — is the server running on localhost:8000?')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  // ── Derived data ────────────────────────────────────────────────────────────

  const allDecisions = data?.decisions ?? []
  const latestSnapshotTime = allDecisions[0]?.snapshot_time ?? null

  // Unique symbols and actions present in decisions (for dynamic filter pills)
  const decisionSymbols = [...new Set(allDecisions.map(d => d.symbol))]
  const availableActions = ['BUY', 'SELL', 'HOLD'] as const

  // Apply decisions filters
  let filteredDecisions = allDecisions
  if (decisionCycle === 'latest' && latestSnapshotTime) {
    filteredDecisions = filteredDecisions.filter(d => d.snapshot_time === latestSnapshotTime)
  }
  if (decisionSymbol !== 'ALL') {
    filteredDecisions = filteredDecisions.filter(d => d.symbol === decisionSymbol)
  }
  if (decisionAction !== 'ALL') {
    filteredDecisions = filteredDecisions.filter(d => d.action === decisionAction)
  }

  // Positions: latest = one row per symbol (newest first, deduplicated)
  const allPositions = data?.positions ?? []
  const latestPositionTime = allPositions[0]?.snapshot_time ?? null
  const latestPositions = (() => {
    const seen = new Set<string>()
    return allPositions.filter(p => {
      if (seen.has(p.symbol)) return false
      seen.add(p.symbol)
      return true
    })
  })()
  const shownPositions = positionView === 'latest' ? latestPositions : allPositions

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 p-6">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold">Overview</h1>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="hidden text-xs text-muted-foreground sm:block">
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={() => load(true)} disabled={refreshing}>
            <RefreshCw data-icon="inline-start" className={cn(refreshing && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {/* ── Error banner ───────────────────────────────────────────────── */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="flex flex-col gap-1 pb-4 pt-4">
            <p className="text-sm font-medium text-destructive">{error}</p>
            <p className="text-xs text-muted-foreground">
              Start the server: <code className="font-mono">cd crypto_bot && uvicorn app.main:app --reload</code>
            </p>
          </CardContent>
        </Card>
      )}

      {/* ── Stats row ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          title="Symbols"
          value={data?.health.tracked_symbols.join(' · ') ?? null}
          sub={`${data?.health.tracked_symbols.length ?? 0} tracked`}
          icon={<Layers className="size-4 text-muted-foreground" />}
          loading={loading}
        />
        <StatCard
          title="AI Model"
          value={data?.health.model.replace('claude-', '') ?? null}
          sub="Anthropic Claude"
          icon={<Zap className="size-4 text-muted-foreground" />}
          loading={loading}
        />
        <StatCard
          title="Total Decisions"
          value={data ? String(allDecisions.length) : null}
          sub={data ? `${Math.round(allDecisions.length / Math.max(data.health.tracked_symbols.length, 1))} cycles` : undefined}
          icon={<Activity className="size-4 text-muted-foreground" />}
          loading={loading}
        />
        <StatCard
          title="Last Cycle"
          value={latestSnapshotTime ? formatTime(latestSnapshotTime) : null}
          sub="Auto-refresh every 30s"
          icon={<RefreshCw className="size-4 text-muted-foreground" />}
          loading={loading}
        />
      </div>

      {/* ── Live price chart ───────────────────────────────────────────── */}
      {priceChart}

      {/* ── Decisions table ────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <CardTitle>AI Decisions</CardTitle>
            {latestSnapshotTime && (
              <span className="text-xs text-muted-foreground">
                Latest cycle: {formatTime(latestSnapshotTime)}
              </span>
            )}
          </div>

          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            {/* Cycle scope */}
            <div className="flex items-center gap-1">
              <FilterPill active={decisionCycle === 'latest'} onClick={() => setDecisionCycle('latest')}>Latest</FilterPill>
              <FilterPill active={decisionCycle === 'all'} onClick={() => setDecisionCycle('all')}>All cycles</FilterPill>
            </div>

            {decisionSymbols.length > 0 && (
              <>
                <Divider />
                {/* Symbol filter */}
                <div className="flex items-center gap-1">
                  <FilterPill active={decisionSymbol === 'ALL'} onClick={() => setDecisionSymbol('ALL')}>All</FilterPill>
                  {decisionSymbols.map(s => (
                    <FilterPill key={s} active={decisionSymbol === s} onClick={() => setDecisionSymbol(s)}>
                      {s.replace('USDT', '')}
                    </FilterPill>
                  ))}
                </div>

                <Divider />

                {/* Action filter */}
                <div className="flex items-center gap-1">
                  <FilterPill active={decisionAction === 'ALL'} onClick={() => setDecisionAction('ALL')}>All</FilterPill>
                  {availableActions.map(a => (
                    <FilterPill key={a} active={decisionAction === a} onClick={() => setDecisionAction(a)}>
                      {a}
                    </FilterPill>
                  ))}
                </div>
              </>
            )}
          </div>
        </CardHeader>

        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="hidden md:table-cell">Reasoning</TableHead>
                <TableHead className="hidden text-right sm:table-cell">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <SkeletonRows rows={3} cols={5} />
              ) : filteredDecisions.length === 0 ? (
                <EmptyRow
                  cols={5}
                  message={
                    allDecisions.length === 0
                      ? 'No decisions yet — waiting for the first trading cycle.'
                      : 'No decisions match the current filters.'
                  }
                />
              ) : (
                filteredDecisions.map(d => (
                  <TableRow
                    key={d.id}
                    className={cn(
                      decisionCycle === 'all' && d.snapshot_time === latestSnapshotTime && 'bg-muted/40',
                    )}
                  >
                    <TableCell className="font-mono text-sm font-medium">
                      {d.symbol.replace('USDT', '')}
                      <span className="text-muted-foreground">/USDT</span>
                    </TableCell>
                    <TableCell><ActionBadge action={d.action} /></TableCell>
                    <TableCell><ConfidenceCell value={d.confidence_score} /></TableCell>
                    <TableCell className="hidden max-w-sm text-sm text-muted-foreground md:table-cell">
                      <span className="line-clamp-1">{d.reasoning_summary ?? '—'}</span>
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap text-right text-xs text-muted-foreground tabular-nums sm:table-cell">
                      {formatTime(d.snapshot_time)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>

          {/* Row count footer */}
          {!loading && allDecisions.length > 0 && (
            <div className="border-t px-4 py-2 text-xs text-muted-foreground">
              Showing {filteredDecisions.length} of {allDecisions.length} decisions
              {decisionCycle === 'latest' && allDecisions.length > filteredDecisions.length && (
                <button
                  className="ml-2 font-medium text-foreground hover:underline"
                  onClick={() => setDecisionCycle('all')}
                >
                  Show all
                </button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Bottom row: Positions + Assets ─────────────────────────────── */}
      <div className="grid gap-4 md:grid-cols-5">

        {/* Positions */}
        <Card className="md:col-span-3">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle>Positions</CardTitle>
              <div className="flex items-center gap-1">
                <FilterPill active={positionView === 'latest'} onClick={() => setPositionView('latest')}>Latest</FilterPill>
                <FilterPill active={positionView === 'all'} onClick={() => setPositionView('all')}>History</FilterPill>
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              Wallet balance snapshots per cycle. Live position tracking is in-memory only.
            </p>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Time</TableHead>
                  <TableHead className="text-right">Wallet Balance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <SkeletonRows rows={3} cols={3} />
                ) : shownPositions.length === 0 ? (
                  <EmptyRow cols={3} message="No position records yet." />
                ) : (
                  shownPositions.map(p => (
                    <TableRow
                      key={p.id}
                      className={cn(
                        positionView === 'all' && p.snapshot_time === latestPositionTime && 'bg-muted/40',
                      )}
                    >
                      <TableCell className="font-mono text-sm font-medium">
                        {p.symbol.replace('USDT', '')}
                        <span className="text-muted-foreground">/USDT</span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatTime(p.snapshot_time)}
                      </TableCell>
                      <TableCell className="text-right text-sm tabular-nums">
                        {p.wallet_balance != null
                          ? `$${p.wallet_balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                          : '—'}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>

            {/* Row count footer */}
            {!loading && allPositions.length > 0 && positionView === 'latest' && allPositions.length > latestPositions.length && (
              <div className="border-t px-4 py-2 text-xs text-muted-foreground">
                Showing {latestPositions.length} of {allPositions.length} records
                <button
                  className="ml-2 font-medium text-foreground hover:underline"
                  onClick={() => setPositionView('all')}
                >
                  Show history
                </button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Assets */}
        <Card className="md:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle>Assets</CardTitle>
            <p className="text-sm text-muted-foreground">Registered trading pairs.</p>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Base</TableHead>
                  <TableHead>Quote</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <SkeletonRows rows={3} cols={3} />
                ) : (data?.assets ?? []).length === 0 ? (
                  <EmptyRow cols={3} message="No assets registered yet." />
                ) : (
                  (data?.assets ?? []).map(a => (
                    <TableRow key={a.id}>
                      <TableCell className="font-mono text-sm font-medium">{a.symbol}</TableCell>
                      <TableCell className="font-mono text-sm text-muted-foreground">{a.base_currency}</TableCell>
                      <TableCell className="font-mono text-sm text-muted-foreground">{a.quote_currency}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

      </div>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <p className="pb-4 text-center text-xs text-muted-foreground">
        Auto-refreshes every 30 seconds · Press{' '}
        <kbd className="rounded bg-muted px-1 font-mono text-xs">d</kbd> to toggle dark mode
      </p>

    </div>
  )
}
