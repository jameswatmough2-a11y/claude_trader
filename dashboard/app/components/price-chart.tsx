'use client'

import { useEffect, useRef, useState } from 'react'
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  XAxis,
  YAxis,
} from 'recharts'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { cn } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PositionInfo {
  entry_price: number
  size_pct: number
  stop_loss_price: number
  take_profit_price: number | null
}

interface MarketSymbol {
  symbol: string
  price: number | null
  bid: number | null
  ask: number | null
  change_24h_pct: number | null
  high_24h: number | null
  low_24h: number | null
  updated_at: string
  position: PositionInfo | null
}

interface Tick {
  t: number   // ms timestamp
  price: number
}

interface SymbolState {
  ticks: Tick[]
  position: PositionInfo | null
  latestInfo: Omit<MarketSymbol, 'position'>
}

const MAX_TICKS = 300  // 5 minutes at 1 Hz

const chartConfig: ChartConfig = {
  price: { label: 'Price', color: 'hsl(var(--chart-1))' },
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPrice(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

function fmtChange(pct: number | null) {
  if (pct === null) return null
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

// ── Component ─────────────────────────────────────────────────────────────────

export function PriceChart() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [selected, setSelected] = useState<string>('')
  const [data, setData] = useState<Record<string, SymbolState>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  // Use a ref so the interval closure always reads the latest data
  const dataRef = useRef(data)
  dataRef.current = data

  useEffect(() => {
    let dead = false

    async function poll() {
      try {
        const res = await fetch('/api/bot/market')
        if (!res.ok) throw new Error()
        const json: Record<string, MarketSymbol> = await res.json()
        const now = Date.now()

        setData(prev => {
          const next: Record<string, SymbolState> = { ...prev }
          for (const [sym, info] of Object.entries(json)) {
            if (info.price === null) continue
            const existing = prev[sym]
            const prevTicks = existing?.ticks ?? []
            // Skip if price unchanged (avoids cluttering chart with flat lines)
            const last = prevTicks[prevTicks.length - 1]
            if (last && last.price === info.price) {
              // Still update position/info but don't add a duplicate tick
              next[sym] = {
                ...existing,
                position: info.position,
                latestInfo: info,
              }
              continue
            }
            next[sym] = {
              ticks: [...prevTicks.slice(-(MAX_TICKS - 1)), { t: now, price: info.price }],
              position: info.position,
              latestInfo: info,
            }
          }
          return next
        })

        // Set symbol list and default selection once
        const syms = Object.keys(json).filter(s => json[s].price !== null)
        if (syms.length > 0) {
          setSymbols(syms)
          setSelected(prev => prev || syms[0])
          setLoading(false)
          setError(false)
        }
      } catch {
        if (!dead) setError(true)
      }
    }

    poll()
    const id = setInterval(poll, 1000)
    return () => { dead = true; clearInterval(id) }
  }, [])

  const symState = selected ? data[selected] : undefined
  const ticks = symState?.ticks ?? []
  const pos = symState?.position ?? null
  const info = symState?.latestInfo

  // Compute Y-axis domain to fit price + all reference lines
  const refValues = [
    pos?.stop_loss_price,
    pos?.take_profit_price,
    pos?.entry_price,
  ].filter((v): v is number => typeof v === 'number')
  const allY = ticks.map(t => t.price).concat(refValues)
  const yMin = allY.length > 0 ? Math.min(...allY) * 0.9994 : 0
  const yMax = allY.length > 0 ? Math.max(...allY) * 1.0006 : 1

  // X-axis tick: show 1 label per minute (every 60 ticks) — hides dense labels
  const xTickInterval = Math.max(1, Math.floor(ticks.length / 5) - 1)

  const change = info ? fmtChange(info.change_24h_pct) : null
  const changePositive = (info?.change_24h_pct ?? 0) >= 0

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          {/* Title + symbol selector */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <CardTitle>Live Price</CardTitle>
              <span className="size-1.5 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs text-muted-foreground">1s stream</span>
            </div>
            {symbols.length > 0 && (
              <div className="flex gap-1">
                {symbols.map(sym => (
                  <Button
                    key={sym}
                    variant={selected === sym ? 'secondary' : 'ghost'}
                    size="sm"
                    className="h-7 px-2 text-xs font-mono"
                    onClick={() => setSelected(sym)}
                  >
                    {sym.replace('USDT', '')}
                  </Button>
                ))}
              </div>
            )}
          </div>

          {/* Current price + stats */}
          {info && info.price !== null && (
            <div className="flex flex-col items-end gap-1">
              <span className="text-2xl font-mono font-semibold tabular-nums">
                ${fmtPrice(info.price)}
              </span>
              <div className="flex items-center gap-2">
                {change && (
                  <Badge variant={changePositive ? 'default' : 'destructive'} className="text-xs">
                    {change}
                  </Badge>
                )}
                {info.high_24h && info.low_24h && (
                  <span className="text-xs text-muted-foreground tabular-nums">
                    H ${fmtPrice(info.high_24h)} · L ${fmtPrice(info.low_24h)}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Position summary row */}
        {pos && (
          <div className="flex flex-wrap gap-3 pt-1 text-xs text-muted-foreground">
            <span>
              Entry&nbsp;
              <span className="font-mono text-foreground">${fmtPrice(pos.entry_price)}</span>
            </span>
            <span>
              Stop loss&nbsp;
              <span className="font-mono text-destructive">${fmtPrice(pos.stop_loss_price)}</span>
              <span className="ml-1 text-muted-foreground/60">
                ({(((pos.stop_loss_price - pos.entry_price) / pos.entry_price) * 100).toFixed(1)}%)
              </span>
            </span>
            {pos.take_profit_price && (
              <span>
                Take profit&nbsp;
                <span className="font-mono" style={{ color: 'hsl(var(--chart-2))' }}>
                  ${fmtPrice(pos.take_profit_price)}
                </span>
              </span>
            )}
            <span>
              Size&nbsp;
              <span className="font-mono text-foreground">{pos.size_pct}%</span>
            </span>
          </div>
        )}
      </CardHeader>

      <CardContent className="pb-4 pl-2 pr-4">
        {loading ? (
          <Skeleton className="h-[280px] w-full" />
        ) : error ? (
          <div className="flex h-[280px] items-center justify-center text-sm text-muted-foreground">
            No market data — waiting for WebSocket connection
          </div>
        ) : ticks.length < 2 ? (
          <div className="flex h-[280px] items-center justify-center text-sm text-muted-foreground">
            Collecting price data…
          </div>
        ) : (
          <ChartContainer config={chartConfig} className="h-[280px] w-full">
            <ComposedChart
              data={ticks}
              margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                vertical={false}
                className="stroke-border/40"
              />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                interval={xTickInterval}
                tickFormatter={fmtTime}
                tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                domain={[yMin, yMax]}
                tickFormatter={v => `$${fmtPrice(v)}`}
                tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                width={90}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value) => [`$${fmtPrice(Number(value))}`, 'Price']}
                    labelFormatter={(label) => fmtTime(Number(label))}
                  />
                }
              />

              {/* Live price line */}
              <Line
                dataKey="price"
                type="monotone"
                stroke="hsl(var(--chart-1))"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />

              {/* Entry price */}
              {pos && (
                <ReferenceLine
                  y={pos.entry_price}
                  stroke="hsl(var(--muted-foreground))"
                  strokeDasharray="6 3"
                  strokeWidth={1}
                  label={{
                    value: `Entry $${fmtPrice(pos.entry_price)}`,
                    position: 'insideBottomRight',
                    fontSize: 10,
                    fill: 'hsl(var(--muted-foreground))',
                  }}
                />
              )}

              {/* Stop-loss line */}
              {pos && (
                <ReferenceLine
                  y={pos.stop_loss_price}
                  stroke="hsl(var(--destructive))"
                  strokeDasharray="4 2"
                  strokeWidth={1.5}
                  label={{
                    value: `Stop loss $${fmtPrice(pos.stop_loss_price)}`,
                    position: 'insideTopRight',
                    fontSize: 10,
                    fill: 'hsl(var(--destructive))',
                  }}
                />
              )}

              {/* Take-profit line */}
              {pos?.take_profit_price && (
                <ReferenceLine
                  y={pos.take_profit_price}
                  stroke="hsl(var(--chart-2))"
                  strokeDasharray="4 2"
                  strokeWidth={1.5}
                  label={{
                    value: `Take profit $${fmtPrice(pos.take_profit_price)}`,
                    position: 'insideBottomRight',
                    fontSize: 10,
                    fill: 'hsl(var(--chart-2))',
                  }}
                />
              )}
            </ComposedChart>
          </ChartContainer>
        )}

        {!loading && !error && ticks.length >= 2 && (
          <p className="mt-2 text-center text-xs text-muted-foreground">
            {ticks.length} ticks · {Math.floor(ticks.length / 60)}m {ticks.length % 60}s of history
            {!pos && ' · No open position — stop-loss line appears when a position is held'}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
