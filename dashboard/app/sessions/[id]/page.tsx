'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, TrendingDown, TrendingUp } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

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
  entry_fee_usdt: number | null
  exit_fee_usdt: number | null
  exit_reason: string | null
  opened_at: string
  closed_at: string | null
  status: string
}

interface SessionDetail {
  id: number
  started_at: string
  ended_at: string | null
  status: string
  duration_seconds: number | null
  starting_balance_usdt: number
  ending_balance_usdt: number | null
  pnl_usdt: number | null
  pnl_pct: number | null
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl_usdt: number
  total_fees_usdt: number
  trades: Trade[]
}

function StatCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1 pt-3 px-4">
        <CardTitle className="text-xs font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent className="pb-3 px-4">
        <div className="text-lg font-semibold leading-none">{value}</div>
        {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  )
}

function pnlColor(val: number | null) {
  if (val === null) return ''
  return val >= 0 ? 'text-green-500' : 'text-red-500'
}

function fmtDuration(s: number | null) {
  if (!s) return '—'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { fmtTime } = useDisplayPrefs()
  const [data, setData] = useState<SessionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`/api/sessions/${id}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [id])

  if (loading) return (
    <div className="p-6 flex flex-col gap-4">
      {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  )

  if (error || !data) return (
    <div className="p-6">
      <p className="text-destructive">{error ?? 'Session not found'}</p>
      <Link href="/sessions"><Button variant="outline" className="mt-4 gap-2"><ArrowLeft className="size-4" />Back</Button></Link>
    </div>
  )

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6">
      <div className="flex items-center gap-3">
        <Link href="/sessions">
          <Button variant="ghost" size="sm" className="gap-1.5">
            <ArrowLeft className="size-4" />
            Sessions
          </Button>
        </Link>
        <h1 className="text-xl font-semibold">Session #{data.id}</h1>
        <Badge variant="outline" className={cn(
          data.status === 'active' && 'border-green-500/50 text-green-500',
          data.status === 'stopped' && 'text-muted-foreground',
        )}>
          {data.status}
        </Badge>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        <StatCard label="Started" value={<span className="text-sm font-mono">{fmtTime(data.started_at)}</span>} />
        <StatCard label="Duration" value={fmtDuration(data.duration_seconds)} />
        <StatCard
          label="P&L"
          value={
            <span className={cn('flex items-center gap-1', pnlColor(data.pnl_usdt))}>
              {data.pnl_usdt !== null ? (
                <>{data.pnl_usdt >= 0 ? <TrendingUp className="size-4" /> : <TrendingDown className="size-4" />}
                {data.pnl_usdt >= 0 ? '+' : ''}{data.pnl_usdt.toFixed(2)}</>
              ) : '—'}
            </span>
          }
          sub={data.pnl_pct !== null ? `${data.pnl_pct >= 0 ? '+' : ''}${data.pnl_pct.toFixed(2)}%` : undefined}
        />
        <StatCard label="Trades" value={data.total_trades} />
        <StatCard label="Wins" value={data.winning_trades} sub={`Losses: ${data.losing_trades}`} />
        <StatCard label="Win Rate" value={data.total_trades > 0 ? `${data.win_rate.toFixed(0)}%` : '—'} />
        <StatCard label="Fees" value={<span className="font-mono">{data.total_fees_usdt.toFixed(4)}</span>} sub="USDT" />
      </div>

      {/* Trades table */}
      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Trades</h2>
        <div className="rounded-lg border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50 text-xs font-medium text-muted-foreground">
                  <th className="px-3 py-2 text-left">Symbol</th>
                  <th className="px-3 py-2 text-left">Entry</th>
                  <th className="px-3 py-2 text-left">Exit</th>
                  <th className="px-3 py-2 text-left">Qty</th>
                  <th className="px-3 py-2 text-left">SL</th>
                  <th className="px-3 py-2 text-left">TP</th>
                  <th className="px-3 py-2 text-left">P&amp;L</th>
                  <th className="px-3 py-2 text-left">Exit Reason</th>
                  <th className="px-3 py-2 text-left">Opened</th>
                  <th className="px-3 py-2 text-left">Closed</th>
                  <th className="px-3 py-2 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="px-3 py-6 text-center text-muted-foreground text-xs">
                      No trades in this session.
                    </td>
                  </tr>
                ) : data.trades.map(trade => {
                  const isWin = (trade.realized_pnl_usdt ?? 0) > 0
                  return (
                    <tr key={trade.id} className={cn(
                      'border-b text-xs transition-colors',
                      trade.status === 'closed' && isWin && 'bg-green-500/5',
                      trade.status === 'closed' && !isWin && trade.realized_pnl_usdt !== null && 'bg-red-500/5',
                    )}>
                      <td className="px-3 py-2 font-mono font-medium">{trade.symbol.replace('USDT', '')}</td>
                      <td className="px-3 py-2 font-mono">{trade.entry_price?.toFixed(2) ?? '—'}</td>
                      <td className="px-3 py-2 font-mono">{trade.exit_price?.toFixed(2) ?? '—'}</td>
                      <td className="px-3 py-2 font-mono">{trade.entry_qty?.toFixed(6) ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-red-400">{trade.stop_loss_price?.toFixed(2) ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-green-400">{trade.take_profit_price?.toFixed(2) ?? '—'}</td>
                      <td className={cn('px-3 py-2 font-mono', pnlColor(trade.realized_pnl_usdt))}>
                        {trade.realized_pnl_usdt !== null ? (
                          <>{trade.realized_pnl_usdt >= 0 ? '+' : ''}{trade.realized_pnl_usdt.toFixed(4)}
                          <span className="opacity-60 ml-1">
                            ({trade.realized_pnl_pct !== null ? `${trade.realized_pnl_pct >= 0 ? '+' : ''}${trade.realized_pnl_pct.toFixed(2)}%` : ''})
                          </span></>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2">
                        {trade.exit_reason ? (
                          <Badge variant="secondary" className="text-xs">{trade.exit_reason.replace('_', ' ')}</Badge>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-muted-foreground whitespace-nowrap">{fmtTime(trade.opened_at)}</td>
                      <td className="px-3 py-2 font-mono text-muted-foreground whitespace-nowrap">
                        {trade.closed_at ? fmtTime(trade.closed_at) : '—'}
                      </td>
                      <td className="px-3 py-2">
                        <Badge variant={trade.status === 'open' ? 'default' : 'secondary'} className="text-xs">
                          {trade.status}
                        </Badge>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
