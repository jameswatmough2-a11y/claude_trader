'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowRight, TrendingDown, TrendingUp } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

interface SessionSummary {
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
  win_rate: number
  total_fees_usdt: number
}

function fmt(seconds: number | null): string {
  if (!seconds) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function PnlCell({ val, pct }: { val: number | null; pct: number | null }) {
  if (val === null) return <span className="text-muted-foreground">—</span>
  const pos = val >= 0
  return (
    <span className={cn('flex items-center gap-1 font-mono text-sm', pos ? 'text-green-500' : 'text-red-500')}>
      {pos ? <TrendingUp className="size-3.5" /> : <TrendingDown className="size-3.5" />}
      {pos ? '+' : ''}{val.toFixed(2)} USDT
      {pct !== null && (
        <span className="text-xs opacity-70">({pos ? '+' : ''}{pct.toFixed(2)}%)</span>
      )}
    </span>
  )
}

export default function SessionsPage() {
  const { fmtTime } = useDisplayPrefs()
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/sessions')
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(data => { setSessions(data); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [])

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Sessions</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Each run of the bot is a separate session. Click a row to view its trades.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
      )}

      <div className="rounded-lg border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-xs font-medium text-muted-foreground">
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left whitespace-nowrap">Started</th>
                <th className="px-3 py-2 text-left">Duration</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">P&amp;L</th>
                <th className="px-3 py-2 text-left">Trades</th>
                <th className="px-3 py-2 text-left">Win %</th>
                <th className="px-3 py-2 text-left">Fees</th>
                <th className="px-3 py-2 text-left" />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b">
                    {Array.from({ length: 8 }).map((_, j) => (
                      <td key={j} className="px-3 py-2"><Skeleton className="h-4 w-full" /></td>
                    ))}
                  </tr>
                ))
              ) : sessions?.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-muted-foreground">
                    No sessions yet. Start the bot to create one.
                  </td>
                </tr>
              ) : (
                sessions?.map(sess => (
                  <tr key={sess.id} className="border-b hover:bg-muted/20 transition-colors">
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{sess.id}</td>
                    <td className="px-3 py-2 font-mono text-xs whitespace-nowrap text-muted-foreground">
                      {fmtTime(sess.started_at)}
                    </td>
                    <td className="px-3 py-2 text-xs">{fmt(sess.duration_seconds)}</td>
                    <td className="px-3 py-2">
                      <Badge
                        variant="outline"
                        className={cn(
                          'text-xs',
                          sess.status === 'active' && 'border-green-500/50 text-green-500',
                          sess.status === 'stopped' && 'text-muted-foreground',
                          sess.status === 'crashed' && 'border-red-500/50 text-red-500',
                        )}
                      >
                        {sess.status}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      <PnlCell val={sess.pnl_usdt} pct={sess.pnl_pct} />
                    </td>
                    <td className="px-3 py-2 text-xs">{sess.total_trades}</td>
                    <td className="px-3 py-2 text-xs">
                      {sess.total_trades > 0 ? `${sess.win_rate.toFixed(0)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                      {sess.total_fees_usdt > 0 ? `${sess.total_fees_usdt.toFixed(4)}` : '—'}
                    </td>
                    <td className="px-3 py-2">
                      <Link href={`/sessions/${sess.id}`}>
                        <Button variant="ghost" size="sm" className="gap-1 text-xs">
                          View <ArrowRight className="size-3" />
                        </Button>
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
