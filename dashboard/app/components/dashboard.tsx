'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import {
  ArrowRight, ChevronDown, ChevronRight,
  PlayCircle, RefreshCw, RotateCcw, Square,
  TrendingDown, TrendingUp,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Health {
  paper_trading: boolean
  tracked_symbols: string[]
  model: string
}

interface BotStatus {
  running: boolean
  paper_trading: boolean
}

interface CurrentSession {
  id: number
  started_at: string
  ended_at: string | null
  status: string
  duration_seconds: number | null
  starting_balance_usdt: number
  live_balance_usdt: number
  live_pnl_usdt: number
  live_pnl_pct: number
  total_trades: number
  open_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_fees_usdt: number
}

interface Trade {
  id: number
  symbol: string
  entry_price: number | null
  exit_price: number | null
  entry_qty: number | null
  size_pct: number | null
  stop_loss_price: number | null
  take_profit_price: number | null
  realized_pnl_pct: number | null
  realized_pnl_usdt: number | null
  exit_reason: string | null
  opened_at: string
  closed_at: string | null
  status: string
}

interface Decision {
  id: number
  symbol: string | null
  snapshot_time: string | null
  action: string
  confidence_score: number | null
  reasoning_summary: string | null
  execution_price: number | null
  trade_id: number | null
}

interface BotData {
  health: Health
  status: BotStatus
  session: CurrentSession | null
  decisions: Decision[]
  trades: Trade[]
}

// ── Data fetching ─────────────────────────────────────────────────────────────

async function fetchAll(): Promise<BotData> {
  const [health, status, sessionRes, decisionsRes] = await Promise.all([
    fetch('/api/bot/health').then(r => r.json()),
    fetch('/api/bot/status').then(r => r.json()),
    fetch('/api/sessions/current'),
    fetch('/api/bot/decisions?limit=100').then(r => r.json()).catch(() => []),
  ])

  const session: CurrentSession | null = sessionRes.ok ? await sessionRes.json() : null

  let trades: Trade[] = []
  if (session?.id) {
    const tr = await fetch(`/api/trades?session_id=${session.id}&limit=100`)
    if (tr.ok) trades = await tr.json()
  }

  return { health, status, session, decisions: decisionsRes, trades }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(s: number | null): string | null {
  if (!s || s < 60) return null
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function fmtMoney(cvtPrice: (n: number) => number, sym: string, val: number, dp = 2): string {
  return `${sym}${cvtPrice(val).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`
}

function pnlCls(val: number | null) {
  if (val === null) return 'text-muted-foreground'
  return val > 0 ? 'text-green-500' : val < 0 ? 'text-red-500' : 'text-muted-foreground'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ActionBadge({ action }: { action: string }) {
  if (action === 'BUY')
    return <Badge variant="outline" className="border-green-500/40 bg-green-500/10 text-green-500">BUY</Badge>
  if (action === 'SELL')
    return <Badge variant="outline" className="border-red-500/40 bg-red-500/10 text-red-500">SELL</Badge>
  return <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-500">HOLD</Badge>
}

function StatCard({ title, value, sub }: { title: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card>
      <CardContent className="py-4 px-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</p>
        <div className="mt-1 text-xl font-semibold leading-none tabular-nums">{value}</div>
        {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  )
}

// ── Active Trade Card ─────────────────────────────────────────────────────────

function ActiveTradeCard({
  trade, cvtPrice, currencySymbol,
}: {
  trade: Trade
  cvtPrice: (n: number) => number
  currencySymbol: string
}) {
  const entry = trade.entry_price ?? 0
  const sl = trade.stop_loss_price
  const tp = trade.take_profit_price
  const slPct = entry > 0 && sl ? ((entry - sl) / entry * 100) : null
  const tpPct = entry > 0 && tp ? ((tp - entry) / entry * 100) : null

  return (
    <Card className="border-blue-500/30 bg-blue-500/5">
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 py-3 px-4">
        <div className="flex items-center gap-2">
          <div className="size-2 rounded-full bg-blue-500 animate-pulse" />
          <span className="font-semibold">{trade.symbol.replace('USDT', '')}/USDT</span>
          <Badge variant="outline" className="border-blue-500/30 text-blue-400 text-xs">
            LONG {trade.size_pct?.toFixed(0)}%
          </Badge>
        </div>
        <div className="flex items-center gap-1.5 text-sm">
          <span className="text-muted-foreground text-xs">Entry</span>
          <span className="font-mono font-medium">{fmtMoney(cvtPrice, currencySymbol, entry)}</span>
        </div>
        {sl !== null && slPct !== null && (
          <div className="flex items-center gap-1.5 text-sm text-red-400">
            <span className="text-muted-foreground text-xs">SL</span>
            <span className="font-mono">{fmtMoney(cvtPrice, currencySymbol, sl)}</span>
            <span className="text-xs opacity-70">−{slPct.toFixed(2)}%</span>
          </div>
        )}
        {tp !== null && tpPct !== null && (
          <div className="flex items-center gap-1.5 text-sm text-green-400">
            <span className="text-muted-foreground text-xs">TP</span>
            <span className="font-mono">{fmtMoney(cvtPrice, currencySymbol, tp)}</span>
            <span className="text-xs opacity-70">+{tpPct.toFixed(2)}%</span>
          </div>
        )}
        {!sl && !tp && (
          <span className="text-xs text-muted-foreground">No SL/TP set</span>
        )}
      </CardContent>
    </Card>
  )
}

// ── Session Timeline ──────────────────────────────────────────────────────────

function SessionTimeline({
  session, trades, fmtTime, cvtPrice, currencySymbol,
}: {
  session: CurrentSession
  trades: Trade[]
  fmtTime: (t: string) => string
  cvtPrice: (n: number) => number
  currencySymbol: string
}) {
  const sorted = [...trades].sort(
    (a, b) => new Date(a.opened_at).getTime() - new Date(b.opened_at).getTime(),
  )

  type TlEvent =
    | { kind: 'start'; time: string; balance: number }
    | { kind: 'buy'; trade: Trade }
    | { kind: 'sell'; trade: Trade }

  const events: TlEvent[] = [
    { kind: 'start', time: session.started_at, balance: session.starting_balance_usdt },
  ]
  for (const t of sorted) {
    events.push({ kind: 'buy', trade: t })
    if (t.closed_at) events.push({ kind: 'sell', trade: t })
  }

  return (
    <div className="relative space-y-0 pl-6">
      <div className="absolute left-2.5 top-2 bottom-2 w-px bg-border" />

      {events.map((ev, i) => {
        if (ev.kind === 'start') return (
          <div key="start" className="relative flex items-start gap-3 pb-5">
            <div className="absolute -left-3.5 mt-1.5 size-2 rounded-full bg-blue-500 ring-2 ring-background" />
            <div>
              <p className="text-xs text-muted-foreground font-mono">{fmtTime(ev.time)}</p>
              <p className="text-sm">
                <span className="font-medium">Session #{session.id} started</span>
                <span className="ml-2 text-muted-foreground text-xs">
                  Balance: {fmtMoney(cvtPrice, currencySymbol, ev.balance)}
                </span>
              </p>
            </div>
          </div>
        )

        if (ev.kind === 'buy') {
          const t = ev.trade
          return (
            <div key={`buy-${t.id}`} className="relative flex items-start gap-3 pb-5">
              <div className="absolute -left-3.5 mt-1.5 size-2 rounded-full bg-green-500 ring-2 ring-background" />
              <div>
                <p className="text-xs text-muted-foreground font-mono">{fmtTime(t.opened_at)}</p>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <ActionBadge action="BUY" />
                  <span className="font-mono font-medium">{t.symbol.replace('USDT', '')}</span>
                  {t.entry_price !== null && (
                    <span className="text-muted-foreground">
                      @ {fmtMoney(cvtPrice, currencySymbol, t.entry_price)}
                    </span>
                  )}
                  {t.size_pct !== null && (
                    <span className="text-xs text-muted-foreground">· {t.size_pct.toFixed(0)}% of portfolio</span>
                  )}
                  {t.status === 'open' && (
                    <Badge variant="outline" className="border-blue-500/30 text-blue-400 text-xs">open</Badge>
                  )}
                </div>
                {t.stop_loss_price !== null && t.stop_loss_price > 0 && t.entry_price !== null && (
                  <p className="text-xs text-muted-foreground mt-0.5">
                    SL {fmtMoney(cvtPrice, currencySymbol, t.stop_loss_price)}
                    {t.take_profit_price !== null && (
                      <> · TP {fmtMoney(cvtPrice, currencySymbol, t.take_profit_price)}</>
                    )}
                  </p>
                )}
              </div>
            </div>
          )
        }

        if (ev.kind === 'sell') {
          const t = ev.trade
          const isWin = (t.realized_pnl_usdt ?? 0) > 0
          const reason = t.exit_reason?.replace(/_/g, ' ') ?? null
          return (
            <div key={`sell-${t.id}`} className="relative flex items-start gap-3 pb-5">
              <div className={cn(
                'absolute -left-3.5 mt-1.5 size-2 rounded-full ring-2 ring-background',
                isWin ? 'bg-green-500' : 'bg-red-500',
              )} />
              <div>
                <p className="text-xs text-muted-foreground font-mono">{fmtTime(t.closed_at!)}</p>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <ActionBadge action="SELL" />
                  <span className="font-mono font-medium">{t.symbol.replace('USDT', '')}</span>
                  {t.exit_price !== null && (
                    <span className="text-muted-foreground">
                      @ {fmtMoney(cvtPrice, currencySymbol, t.exit_price)}
                    </span>
                  )}
                  {t.realized_pnl_usdt !== null && (
                    <span className={cn('font-mono font-medium', pnlCls(t.realized_pnl_usdt))}>
                      {isWin ? '+' : ''}{fmtMoney(cvtPrice, currencySymbol, t.realized_pnl_usdt, 4)}
                    </span>
                  )}
                  {t.realized_pnl_pct !== null && (
                    <span className={cn('text-xs', pnlCls(t.realized_pnl_pct))}>
                      ({isWin ? '+' : ''}{t.realized_pnl_pct.toFixed(2)}%)
                    </span>
                  )}
                </div>
                {reason && (
                  <p className="text-xs text-muted-foreground mt-0.5">{reason}</p>
                )}
              </div>
            </div>
          )
        }

        return null
      })}

      {trades.length === 0 && (
        <div className="relative flex items-start gap-3 pb-2">
          <div className="absolute -left-3.5 mt-1.5 size-2 rounded-full bg-muted-foreground/30 ring-2 ring-background" />
          <p className="text-sm text-muted-foreground">No trades this session yet.</p>
        </div>
      )}
    </div>
  )
}

// ── AI Decisions Table ────────────────────────────────────────────────────────

function DecisionsTable({
  decisions, loading, fmtTime, cvtPrice, currencySymbol,
}: {
  decisions: Decision[]
  loading: boolean
  fmtTime: (t: string) => string
  cvtPrice: (n: number) => number
  currencySymbol: string
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [actionFilter, setActionFilter] = useState('ALL')
  const [symbolFilter, setSymbolFilter] = useState('ALL')

  const symbols = [...new Set(decisions.map(d => d.symbol).filter(Boolean))] as string[]

  const filtered = decisions.filter(d => {
    if (actionFilter !== 'ALL' && d.action !== actionFilter) return false
    if (symbolFilter !== 'ALL' && d.symbol !== symbolFilter) return false
    return true
  })

  function toggle(id: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function FilterPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
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
        {label}
      </button>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>AI Decisions</CardTitle>
          <div className="flex flex-wrap items-center gap-1">
            {['ALL', 'BUY', 'SELL', 'HOLD'].map(a => (
              <FilterPill key={a} label={a} active={actionFilter === a} onClick={() => setActionFilter(a)} />
            ))}
            {symbols.length > 1 && (
              <>
                <span className="h-3 w-px bg-border mx-1" />
                {['ALL', ...symbols].map(s => (
                  <FilterPill
                    key={s}
                    label={s === 'ALL' ? 'All' : s.replace('USDT', '')}
                    active={symbolFilter === s}
                    onClick={() => setSymbolFilter(s)}
                  />
                ))}
              </>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Symbol</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Price</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead className="hidden sm:table-cell">Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 6 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-16 text-center text-sm text-muted-foreground">
                  {decisions.length === 0
                    ? 'No decisions yet — waiting for first trading cycle.'
                    : 'No decisions match the current filters.'}
                </TableCell>
              </TableRow>
            ) : (
              filtered.flatMap(d => {
                const isOpen = expanded.has(d.id)
                return [
                  <TableRow
                    key={d.id}
                    className={cn('cursor-pointer select-none', isOpen && 'bg-muted/20')}
                    onClick={() => toggle(d.id)}
                  >
                    <TableCell className="pr-0 pl-4">
                      {isOpen
                        ? <ChevronDown className="size-3.5 text-muted-foreground" />
                        : <ChevronRight className="size-3.5 text-muted-foreground" />}
                    </TableCell>
                    <TableCell className="font-mono text-sm font-medium">
                      {d.symbol?.replace('USDT', '') ?? '—'}
                      <span className="text-muted-foreground">/USDT</span>
                    </TableCell>
                    <TableCell><ActionBadge action={d.action} /></TableCell>
                    <TableCell className="font-mono text-sm">
                      {d.execution_price !== null
                        ? fmtMoney(cvtPrice, currencySymbol, d.execution_price)
                        : <span className="text-muted-foreground text-xs">—</span>}
                    </TableCell>
                    <TableCell>
                      {d.confidence_score !== null ? (
                        <div className="flex items-center gap-2">
                          <Progress value={Math.round(d.confidence_score * 100)} className="h-1.5 w-16" />
                          <span className="text-xs tabular-nums">{Math.round(d.confidence_score * 100)}%</span>
                        </div>
                      ) : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground tabular-nums sm:table-cell">
                      {d.snapshot_time ? fmtTime(d.snapshot_time) : '—'}
                    </TableCell>
                  </TableRow>,
                  isOpen ? (
                    <TableRow key={`${d.id}-r`} className="bg-muted/10 hover:bg-muted/10">
                      <TableCell colSpan={6} className="pt-1 pb-3 pl-10 pr-4">
                        <p className="text-sm text-muted-foreground leading-relaxed">
                          {d.reasoning_summary ?? 'No reasoning recorded.'}
                        </p>
                      </TableCell>
                    </TableRow>
                  ) : null,
                ].filter(Boolean)
              })
            )}
          </TableBody>
        </Table>

        {!loading && decisions.length > 0 && (
          <div className="border-t px-4 py-2 text-xs text-muted-foreground">
            {filtered.length} of {decisions.length} decisions
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function Dashboard({ priceChart }: { priceChart?: React.ReactNode }) {
  const { cvtPrice, currencySymbol, fmtTime } = useDisplayPrefs()
  const [data, setData] = useState<BotData | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [botAction, setBotAction] = useState<string | null>(null)

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

  const botControl = useCallback(async (action: 'start' | 'stop' | 'reset') => {
    if (action === 'reset' && !window.confirm('Clear all trading history from the database?')) return
    setBotAction(action)
    try {
      await fetch(`/api/bot/${action}`, { method: 'POST' })
      await load()
    } finally {
      setBotAction(null)
    }
  }, [load])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const isRunning = data?.status.running ?? false
  const session = data?.session ?? null
  const openTrades = (data?.trades ?? []).filter(t => t.status === 'open')
  const dur = fmtDuration(session?.duration_seconds ?? null)

  return (
    <div className="flex min-h-screen flex-col gap-4 p-4 md:gap-6 md:p-6">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold">Overview</h1>
        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="hidden text-xs text-muted-foreground sm:block">
              Updated {fmtTime(lastUpdated.toISOString())}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={() => load(true)} disabled={refreshing}>
            <RefreshCw className={cn('size-3.5 mr-1.5', refreshing && 'animate-spin')} />
            <span className="hidden sm:inline">Refresh</span>
          </Button>
        </div>
      </div>

      {/* ── Bot control card ────────────────────────────────────────────── */}
      <Card className={cn('border', isRunning ? 'border-green-500/40 bg-green-500/5' : 'border-muted')}>
        <CardContent className="py-3 px-4">
          <div className="flex flex-wrap items-center justify-between gap-3">

            {/* Status + session info */}
            <div className="flex flex-col gap-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={cn(
                  'size-2 shrink-0 rounded-full',
                  isRunning ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground/40',
                )} />
                <span className="text-sm font-medium">
                  {loading ? 'Loading…' : isRunning ? 'Bot Running' : 'Bot Stopped'}
                </span>
                {data?.health.paper_trading && (
                  <Badge variant="secondary" className="text-xs">Paper</Badge>
                )}
              </div>

              {session ? (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                  <Link
                    href={`/sessions/${session.id}`}
                    className="flex items-center gap-1 hover:text-foreground transition-colors"
                    onClick={e => e.stopPropagation()}
                  >
                    Session #{session.id} <ArrowRight className="size-3" />
                  </Link>
                  <span>Started {fmtTime(session.started_at)}</span>
                  {dur && <span>· {dur} running</span>}
                  <span className={cn('font-medium', pnlCls(session.live_pnl_usdt))}>
                    {session.live_pnl_usdt >= 0 ? '+' : ''}
                    {fmtMoney(cvtPrice, currencySymbol, session.live_pnl_usdt)}
                    {' '}({session.live_pnl_pct >= 0 ? '+' : ''}{session.live_pnl_pct.toFixed(2)}%)
                  </span>
                </div>
              ) : !loading && (
                <p className="text-xs text-muted-foreground">No active session — press Start to begin</p>
              )}
            </div>

            {/* Buttons */}
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm"
                onClick={() => botControl('start')}
                disabled={loading || isRunning || botAction !== null}
              >
                <PlayCircle className="size-3.5 mr-1.5" /> Start
              </Button>
              <Button
                variant="outline" size="sm"
                onClick={() => botControl('stop')}
                disabled={loading || !isRunning || botAction !== null}
              >
                <Square className="size-3.5 mr-1.5" /> Stop
              </Button>
              <Button
                variant="destructive" size="sm"
                onClick={() => botControl('reset')}
                disabled={loading || isRunning || botAction !== null}
              >
                <RotateCcw className="size-3.5 mr-1.5" /> Reset DB
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="flex flex-col gap-1 py-4 px-4">
            <p className="text-sm font-medium text-destructive">{error}</p>
            <p className="text-xs text-muted-foreground">
              Start with: <code className="font-mono">cd crypto_bot && uvicorn app.main:app --reload</code>
            </p>
          </CardContent>
        </Card>
      )}

      {/* ── Open positions ───────────────────────────────────────────────── */}
      {openTrades.length > 0 && (
        <div className="flex flex-col gap-2">
          {openTrades.map(t => (
            <ActiveTradeCard key={t.id} trade={t} cvtPrice={cvtPrice} currencySymbol={currencySymbol} />
          ))}
        </div>
      )}

      {/* ── Session stats ─────────────────────────────────────────────────── */}
      {(session || loading) && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard
            title="Balance"
            value={loading ? <Skeleton className="h-5 w-24" /> :
              session ? fmtMoney(cvtPrice, currencySymbol, session.live_balance_usdt) : '—'}
            sub="Live paper balance"
          />
          <StatCard
            title="Session P&L"
            value={loading ? <Skeleton className="h-5 w-24" /> : session ? (
              <span className={cn('flex items-center gap-1', pnlCls(session.live_pnl_usdt))}>
                {session.live_pnl_usdt >= 0
                  ? <TrendingUp className="size-4" />
                  : <TrendingDown className="size-4" />}
                {session.live_pnl_usdt >= 0 ? '+' : ''}
                {fmtMoney(cvtPrice, currencySymbol, session.live_pnl_usdt)}
              </span>
            ) : '—'}
            sub={session ? `${session.live_pnl_pct >= 0 ? '+' : ''}${session.live_pnl_pct.toFixed(2)}% since start` : undefined}
          />
          <StatCard
            title="Trades"
            value={loading ? <Skeleton className="h-5 w-12" /> : session ? session.total_trades : '—'}
            sub={session
              ? session.open_trades > 0
                ? `${session.open_trades} open`
                : session.total_trades > 0 ? 'all closed' : 'no trades yet'
              : undefined}
          />
          <StatCard
            title="Win Rate"
            value={loading ? <Skeleton className="h-5 w-12" /> :
              session && session.total_trades > 0
                ? `${session.win_rate.toFixed(0)}%`
                : '—'}
            sub={session && session.total_trades > 0
              ? `${session.winning_trades}W · ${session.losing_trades}L`
              : session ? 'no closed trades' : undefined}
          />
        </div>
      )}

      {/* ── Chart ─────────────────────────────────────────────────────────── */}
      {priceChart}

      {/* ── Session timeline ──────────────────────────────────────────────── */}
      {session && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle>Session Timeline</CardTitle>
              <Link href={`/sessions/${session.id}`}>
                <Button variant="ghost" size="sm" className="gap-1 text-xs h-7">
                  Full details <ArrowRight className="size-3" />
                </Button>
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            <SessionTimeline
              session={session}
              trades={data?.trades ?? []}
              fmtTime={fmtTime}
              cvtPrice={cvtPrice}
              currencySymbol={currencySymbol}
            />
          </CardContent>
        </Card>
      )}

      {/* ── AI Decisions ──────────────────────────────────────────────────── */}
      <DecisionsTable
        decisions={data?.decisions ?? []}
        loading={loading}
        fmtTime={fmtTime}
        cvtPrice={cvtPrice}
        currencySymbol={currencySymbol}
      />

      <p className="pb-2 text-center text-xs text-muted-foreground">Auto-refreshes every 30s</p>
    </div>
  )
}
