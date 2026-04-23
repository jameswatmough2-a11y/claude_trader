'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  Time,
  UTCTimestamp,
  createChart,
} from 'lightweight-charts'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { autoDecimals, changeIsPositive, fmtChange, fmtPrice } from '@/lib/chart-utils'
import { useChartWs } from '@/hooks/use-chart-ws'
import type { WsCandle, WsPosition, WsPriceUpdate } from '@/hooks/use-chart-ws'

// ── Constants ─────────────────────────────────────────────────────────────────

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

// ── Lightweight Charts theme ──────────────────────────────────────────────────

function buildChartOptions(el: HTMLElement) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#64748b',
      fontFamily:
        'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: '#1e293b' },
      horzLines: { color: '#1e293b' },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
      horzLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
    },
    rightPriceScale: {
      borderVisible: false,
      textColor: '#64748b',
    },
    timeScale: {
      borderVisible: false,
      timeVisible: true,
      secondsVisible: false,
      tickMarkMaxCharacterLength: 8,
    },
    width: el.offsetWidth,
    height: 380,
  } as const
}

// ── Component ─────────────────────────────────────────────────────────────────

export function CandlestickChart({
  initialSymbols = ['BTCUSDT'],
}: {
  initialSymbols?: string[]
}) {
  const [symbols, setSymbols] = useState<string[]>(initialSymbols)
  const [symbol, setSymbol] = useState(initialSymbols[0] ?? 'BTCUSDT')
  const [timeframe, setTimeframe] = useState<Timeframe>('1m')
  const [loading, setLoading] = useState(true)
  const [wsConnected, setWsConnected] = useState(false)
  const [priceInfo, setPriceInfo] = useState<WsPriceUpdate | null>(null)
  const [position, setPosition] = useState<WsPosition | null>(null)

  // Chart refs — mutated directly, never trigger rerenders
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick', Time> | null>(null)
  const plRef = useRef<{ sl: IPriceLine | null; tp: IPriceLine | null; entry: IPriceLine | null }>({
    sl: null,
    tp: null,
    entry: null,
  })

  // ── Fetch tracked symbols from health endpoint ──────────────────────────────

  useEffect(() => {
    fetch('/api/bot/health')
      .then(r => r.json())
      .then((h: { tracked_symbols: string[] }) => {
        if (h.tracked_symbols?.length) {
          setSymbols(h.tracked_symbols)
          // Only set the initial symbol — never override a user selection
          setSymbol(prev => (h.tracked_symbols.includes(prev) ? prev : h.tracked_symbols[0]))
        }
      })
      .catch(() => {})
  }, [])

  // ── Chart init / destroy ────────────────────────────────────────────────────

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, buildChartOptions(el))
    chartRef.current = chart

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })
    seriesRef.current = series

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.offsetWidth }))
    ro.observe(el)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  // ── Load history on symbol / timeframe change ───────────────────────────────

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return

    setLoading(true)
    fetch(`/api/chart/history?symbol=${symbol}&interval=${timeframe}`)
      .then(r => r.json())
      .then(({ candles }: { candles: Array<{ time: number; open: number; high: number; low: number; close: number }> }) => {
        series.setData(candles.map(c => ({ ...c, time: c.time as UTCTimestamp })))
        chartRef.current?.timeScale().fitContent()
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [symbol, timeframe])

  // ── Price line management ───────────────────────────────────────────────────

  const clearPriceLines = useCallback(() => {
    const s = seriesRef.current
    if (!s) return
    const { sl, tp, entry } = plRef.current
    if (sl) s.removePriceLine(sl)
    if (tp) s.removePriceLine(tp)
    if (entry) s.removePriceLine(entry)
    plRef.current = { sl: null, tp: null, entry: null }
  }, [])

  const drawPriceLines = useCallback((pos: WsPosition) => {
    clearPriceLines()
    const s = seriesRef.current
    if (!s) return
    const d = autoDecimals(pos.entry_price)

    plRef.current.sl = s.createPriceLine({
      price: pos.stop_loss_price,
      color: '#ef4444',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: `SL  ${fmtPrice(pos.stop_loss_price, d)}`,
    })
    plRef.current.entry = s.createPriceLine({
      price: pos.entry_price,
      color: '#94a3b8',
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      axisLabelVisible: true,
      title: `Entry  ${fmtPrice(pos.entry_price, d)}`,
    })
    if (pos.take_profit_price !== null) {
      plRef.current.tp = s.createPriceLine({
        price: pos.take_profit_price,
        color: '#22c55e',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `TP  ${fmtPrice(pos.take_profit_price, d)}`,
      })
    }
  }, [clearPriceLines])

  // Reset on symbol change
  useEffect(() => {
    clearPriceLines()
    setPosition(null)
    setPriceInfo(null)
  }, [symbol, clearPriceLines])

  // ── WebSocket handlers (stable refs — no rerenders on every tick) ───────────

  const handleCandle = useCallback((candle: WsCandle) => {
    seriesRef.current?.update({ ...candle, time: candle.time as UTCTimestamp })
  }, [])

  const handlePrice = useCallback((data: WsPriceUpdate) => {
    setPriceInfo(data)
  }, [])

  const handlePosition = useCallback((pos: WsPosition) => {
    setPosition(pos)
    drawPriceLines(pos)
  }, [drawPriceLines])

  useChartWs(symbol, timeframe, {
    onCandle: handleCandle,
    onPrice: handlePrice,
    onPosition: handlePosition,
    onConnected: () => setWsConnected(true),
    onDisconnected: () => setWsConnected(false),
  })

  // ── Render ────────────────────────────────────────────────────────────────

  const change = fmtChange(priceInfo?.change_24h_pct ?? null)
  const changeUp = changeIsPositive(priceInfo?.change_24h_pct ?? null)
  const decimals = priceInfo ? autoDecimals(priceInfo.price) : 2

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">

          {/* Left: title + symbol tabs */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <CardTitle>Candlestick Chart</CardTitle>
              <span className={cn(
                'size-1.5 rounded-full transition-colors',
                wsConnected ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground',
              )} />
              <span className="text-xs text-muted-foreground">
                {wsConnected ? 'live' : 'connecting…'}
              </span>
            </div>
            <div className="flex flex-wrap gap-1">
              {symbols.map(s => (
                <Button
                  key={s}
                  variant={symbol === s ? 'secondary' : 'ghost'}
                  size="sm"
                  className="h-7 px-2 text-xs font-mono"
                  onClick={() => setSymbol(s)}
                >
                  {s.replace('USDT', '')}
                </Button>
              ))}
            </div>
          </div>

          {/* Right: price + timeframe tabs */}
          <div className="flex flex-col items-end gap-2">
            {priceInfo && (
              <div className="flex items-center gap-2">
                <span className="text-2xl font-mono font-semibold tabular-nums">
                  ${fmtPrice(priceInfo.price, decimals)}
                </span>
                {change && (
                  <Badge variant={changeUp ? 'default' : 'destructive'} className="text-xs">
                    {change}
                  </Badge>
                )}
              </div>
            )}
            <div className="flex gap-1">
              {TIMEFRAMES.map(tf => (
                <Button
                  key={tf}
                  variant={timeframe === tf ? 'secondary' : 'ghost'}
                  size="sm"
                  className="h-6 px-2 text-xs font-mono"
                  onClick={() => setTimeframe(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
          </div>
        </div>

        {/* Position row */}
        {position && (
          <div className="flex flex-wrap gap-4 pt-1 text-xs text-muted-foreground">
            <span>
              Entry{' '}
              <span className="font-mono text-foreground">
                ${fmtPrice(position.entry_price, decimals)}
              </span>
            </span>
            <span className="text-destructive">
              Stop loss{' '}
              <span className="font-mono">
                ${fmtPrice(position.stop_loss_price, decimals)}
              </span>
            </span>
            {position.take_profit_price !== null && (
              <span className="text-green-500">
                Take profit{' '}
                <span className="font-mono">
                  ${fmtPrice(position.take_profit_price, decimals)}
                </span>
              </span>
            )}
            <span>
              Size{' '}
              <span className="font-mono text-foreground">{position.size_pct}%</span>
            </span>
          </div>
        )}
      </CardHeader>

      <CardContent className="relative pb-4 pl-2 pr-4">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-b-xl bg-card/90">
            <Skeleton className="h-[380px] w-full" />
          </div>
        )}
        {/* Chart mounts here — always in the DOM so the chart instance persists */}
        <div ref={containerRef} className="w-full" style={{ height: 380 }} />
      </CardContent>
    </Card>
  )
}
