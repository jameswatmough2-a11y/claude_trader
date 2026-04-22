'use client'

import { useCallback, useEffect, useState } from 'react'
import { Activity, Bot, Layers, RefreshCw, Zap } from 'lucide-react'

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
      <Progress value={pct} className="w-16 h-1.5" />
      <span className={cn(
        'tabular-nums text-xs font-medium',
        pct < 60 && 'text-muted-foreground',
      )}>
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
      <TableCell colSpan={cols} className="h-20 text-center text-sm text-muted-foreground">
        {message}
      </TableCell>
    </TableRow>
  )
}

function StatCard({
  title,
  value,
  sub,
  icon,
  loading,
}: {
  title: string
  value: string | null
  sub?: string
  icon: React.ReactNode
  loading: boolean
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-6 w-3/4" />
        ) : (
          <>
            <p className="text-base font-semibold truncate">{value ?? '—'}</p>
            {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  )
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

  // The latest cycle shares the same snapshot_time as the first decision in the list
  // (decisions come back newest-first from the updated API).
  const latestSnapshotTime = data?.decisions[0]?.snapshot_time ?? null

  const latestDecisions = data?.decisions ?? []
  // Deduplicate to show one position record per symbol from latest cycle only
  const latestPositionTime = data?.positions[0]?.snapshot_time ?? null

  return (
    <div className="flex flex-col gap-6 p-6 max-w-7xl mx-auto min-h-screen">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Bot className="size-5 text-primary" />
          <h1 className="text-xl font-semibold tracking-tight">Claude Trader</h1>
          {!loading && data && (
            <Badge variant={data.health.paper_trading ? 'outline' : 'default'}>
              {data.health.paper_trading ? 'Paper Trading' : 'Live Trading'}
            </Badge>
          )}
          {!loading && (
            <span className={cn(
              'flex items-center gap-1.5 text-xs',
              error ? 'text-destructive' : 'text-muted-foreground',
            )}>
              <span className={cn(
                'inline-block size-1.5 rounded-full',
                error ? 'bg-destructive' : 'bg-green-500',
              )} />
              {error ? 'Offline' : 'Online'}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="text-xs text-muted-foreground hidden sm:block">
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => load(true)}
            disabled={refreshing}
          >
            <RefreshCw data-icon="inline-start" className={cn(refreshing && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {/* ── Error banner ───────────────────────────────────────────────── */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="flex flex-col gap-1 pt-4 pb-4">
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
          value={data ? String(data.decisions.length) : null}
          sub={data ? `${Math.round(data.decisions.length / Math.max(data.health.tracked_symbols.length, 1))} cycles` : undefined}
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

      {/* ── Live price chart (injected from page) ─────────────────────── */}
      {priceChart}

      {/* ── Decisions table ────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle>AI Decisions</CardTitle>
            {latestSnapshotTime && (
              <span className="text-xs text-muted-foreground">
                Latest cycle: {formatTime(latestSnapshotTime)}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Claude&apos;s hourly recommendations, newest first. Highlighted rows are the current cycle.
          </p>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="hidden md:table-cell">Reasoning</TableHead>
                <TableHead className="text-right hidden sm:table-cell">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <SkeletonRows rows={3} cols={5} />
              ) : latestDecisions.length === 0 ? (
                <EmptyRow cols={5} message="No decisions yet — waiting for the first trading cycle to complete." />
              ) : (
                latestDecisions.map(d => (
                  <TableRow
                    key={d.id}
                    className={cn(d.snapshot_time === latestSnapshotTime && 'bg-muted/40')}
                  >
                    <TableCell className="font-mono font-medium text-sm">
                      {d.symbol}
                    </TableCell>
                    <TableCell>
                      <ActionBadge action={d.action} />
                    </TableCell>
                    <TableCell>
                      <ConfidenceCell value={d.confidence_score} />
                    </TableCell>
                    <TableCell className="hidden md:table-cell text-sm text-muted-foreground max-w-xs">
                      <span className="line-clamp-2">{d.reasoning_summary ?? '—'}</span>
                    </TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground tabular-nums hidden sm:table-cell whitespace-nowrap">
                      {formatTime(d.snapshot_time)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* ── Bottom row: Positions + Assets ─────────────────────────────── */}
      <div className="grid gap-4 md:grid-cols-5">

        {/* Positions */}
        <Card className="md:col-span-3">
          <CardHeader className="pb-3">
            <CardTitle>Positions</CardTitle>
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
                ) : (data?.positions ?? []).length === 0 ? (
                  <EmptyRow cols={3} message="No position records yet." />
                ) : (
                  (data?.positions ?? []).map(p => (
                    <TableRow
                      key={p.id}
                      className={cn(p.snapshot_time === latestPositionTime && 'bg-muted/40')}
                    >
                      <TableCell className="font-mono font-medium text-sm">{p.symbol}</TableCell>
                      <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                        {formatTime(p.snapshot_time)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-sm">
                        {p.wallet_balance != null
                          ? `$${p.wallet_balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                          : '—'}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
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
                      <TableCell className="font-mono font-medium text-sm">{a.symbol}</TableCell>
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
      <p className="text-center text-xs text-muted-foreground pb-4">
        Auto-refreshes every 30 seconds · Press <kbd className="font-mono bg-muted px-1 rounded text-xs">d</kbd> to toggle dark mode
      </p>

    </div>
  )
}
