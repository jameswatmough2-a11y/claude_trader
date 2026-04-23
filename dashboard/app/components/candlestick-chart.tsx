'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  LineStyle,
  SeriesMarker,
  Time,
  UTCTimestamp,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { autoDecimals, changeIsPositive, fmtChange, fmtPrice } from '@/lib/chart-utils'
import { useChartWs } from '@/hooks/use-chart-ws'
import type { WsCandle, WsPosition, WsPriceUpdate } from '@/hooks/use-chart-ws'
import { TIMEZONES } from '@/lib/display-prefs'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

// ── Constants ─────────────────────────────────────────────────────────────────

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

type LineKey = 'entry' | 'sl' | 'tp'
type LineFlags = Record<LineKey, boolean>

const LINE_META: { key: LineKey; label: string; color: string }[] = [
  { key: 'entry', label: 'Entry',       color: '#eab308' },
  { key: 'sl',    label: 'Stop loss',   color: '#ef4444' },
  { key: 'tp',    label: 'Take profit', color: '#22c55e' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeTzFormatter(iana: string) {
  return (time: number) => {
    const d = new Date(time * 1000)
    return d.toLocaleTimeString('en', {
      timeZone: iana,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }
}

function buildChartOptions(tzFormatter: (t: number) => string) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#64748b',
      fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
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
    localization: {
      timeFormatter: tzFormatter,
    },
    autoSize: true,
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
  const configLoaded = useRef(false)
  const [loading, setLoading] = useState(true)
  const [wsConnected, setWsConnected] = useState(false)
  const [priceInfo, setPriceInfo] = useState<WsPriceUpdate | null>(null)
  const [position, setPosition] = useState<WsPosition | null>(null)

  // Display preferences from global context
  const { prefs, cvtPrice, currencySymbol } = useDisplayPrefs()

  // Line toggles
  const [show, setShow] = useState<LineFlags>({ entry: true, sl: true, tp: true })
  const showRef = useRef<LineFlags>({ entry: true, sl: true, tp: true })

  // Chart refs
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick', Time> | null>(null)
  const plRef = useRef<Record<LineKey, IPriceLine | null>>({ entry: null, sl: null, tp: null })
  const positionRef = useRef<WsPosition | null>(null)

  // Trade markers
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const tradeRangesRef = useRef<Array<{
    trade_id: number; entry_time: number; exit_time: number | null;
    pnl_pct: number | null; status: string
  }>>([])
  const highlightDivsRef = useRef<HTMLDivElement[]>([])

  // Update chart localization when timezone changes
  useEffect(() => {
    chartRef.current?.applyOptions({
      localization: { timeFormatter: makeTzFormatter(prefs.timezone) },
    })
  }, [prefs.timezone])

  // ── Toggle ──────────────────────────────────────────────────────────────────

  function toggleLine(key: LineKey) {
    const next = !showRef.current[key]
    showRef.current[key] = next
    setShow(prev => ({ ...prev, [key]: next }))
  }

  // ── Fetch tracked symbols + default chart interval ──────────────────────────

  useEffect(() => {
    fetch('/api/bot/health')
      .then(r => r.json())
      .then((h: { tracked_symbols: string[] }) => {
        if (h.tracked_symbols?.length) {
          setSymbols(h.tracked_symbols)
          setSymbol(prev => (h.tracked_symbols.includes(prev) ? prev : h.tracked_symbols[0]))
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/bot/config')
      .then(r => r.json())
      .then((cfg: { chart_interval?: string }) => {
        if (!configLoaded.current && cfg.chart_interval && (TIMEFRAMES as readonly string[]).includes(cfg.chart_interval)) {
          setTimeframe(cfg.chart_interval as Timeframe)
          configLoaded.current = true
        }
      })
      .catch(() => {})
  }, [])

  // ── Chart init / destroy ────────────────────────────────────────────────────

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const tzFormatter = makeTzFormatter(prefs.timezone)
    const chart = createChart(el, buildChartOptions(tzFormatter))
    chartRef.current = chart

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#089981',
      downColor: '#f23645',
      borderVisible: false,
      wickUpColor: '#089981',
      wickDownColor: '#f23645',
      // Expand auto-scale to always include SL and TP price levels
      autoscaleInfoProvider: (original) => {
        const res = original()
        const pos = positionRef.current
        if (!pos) return res

        const prices: number[] = [pos.entry_price]
        if (pos.stop_loss_price != null && pos.stop_loss_price > 0) prices.push(pos.stop_loss_price)
        if (pos.take_profit_price != null && pos.take_profit_price > 0) prices.push(pos.take_profit_price)

        const posMin = Math.min(...prices)
        const posMax = Math.max(...prices)

        if (res?.priceRange) {
          return {
            priceRange: {
              minValue: Math.min(res.priceRange.minValue, posMin),
              maxValue: Math.max(res.priceRange.maxValue, posMax),
            },
            margins: res.margins,
          }
        }
        return { priceRange: { minValue: posMin, maxValue: posMax } }
      },
    })
    seriesRef.current = series
    markersPluginRef.current = createSeriesMarkers(series, [])

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      markersPluginRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Draw trade range highlights ──────────────────────────────────────────────

  const drawTradeHighlights = useCallback(() => {
    const chart = chartRef.current
    const container = containerRef.current
    if (!chart || !container) return

    // Remove old overlays
    highlightDivsRef.current.forEach(d => d.remove())
    highlightDivsRef.current = []

    const ts = chart.timeScale()
    tradeRangesRef.current.forEach(range => {
      const x1 = ts.timeToCoordinate(range.entry_time as UTCTimestamp)
      const x2 = range.exit_time
        ? ts.timeToCoordinate(range.exit_time as UTCTimestamp)
        : container.clientWidth

      if (x1 === null || x1 === undefined) return

      const isWin = range.pnl_pct !== null ? range.pnl_pct > 0 : null
      const color = isWin === null
        ? 'rgba(148, 163, 184, 0.05)'
        : isWin
          ? 'rgba(8, 153, 129, 0.07)'
          : 'rgba(242, 54, 69, 0.07)'

      const div = document.createElement('div')
      div.style.cssText = `
        position: absolute;
        top: 0; bottom: 0;
        left: ${x1}px;
        width: ${Math.max(2, (x2 ?? container.clientWidth) - x1)}px;
        background: ${color};
        pointer-events: none;
        z-index: 0;
      `
      container.style.position = 'relative'
      container.appendChild(div)
      highlightDivsRef.current.push(div)
    })
  }, [])

  // ── Load history on symbol / timeframe change ───────────────────────────────

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return

    setLoading(true)

    // Clear old markers and highlights on symbol/timeframe change
    markersPluginRef.current?.setMarkers([])
    highlightDivsRef.current.forEach(d => d.remove())
    highlightDivsRef.current = []
    tradeRangesRef.current = []

    fetch(`/api/chart/history?symbol=${symbol}&interval=${timeframe}`)
      .then(r => r.json())
      .then(({ candles }: { candles: Array<{ time: number; open: number; high: number; low: number; close: number }> }) => {
        series.setData(candles.map(c => ({ ...c, time: c.time as UTCTimestamp })))
        chartRef.current?.timeScale().fitContent()
        setLoading(false)

        // Fetch trade markers for the current session
        return fetch(`/api/sessions/current/markers?symbol=${symbol}&timeframe=${timeframe}`)
      })
      .then(r => (r && r.ok) ? r.json() : null)
      .then((data: {
        markers: Array<{
          time: number; position: string; color: string; shape: string;
          text: string; action: string; trade_id: number | null
        }>;
        trade_ranges: Array<{
          trade_id: number; entry_time: number; exit_time: number | null;
          pnl_pct: number | null; status: string
        }>;
      } | null) => {
        if (!data || !markersPluginRef.current) return
        markersPluginRef.current.setMarkers(
          data.markers.map(m => ({
            time: m.time as UTCTimestamp,
            position: m.position as 'aboveBar' | 'belowBar',
            color: m.color,
            shape: m.shape as 'arrowUp' | 'arrowDown' | 'circle',
            text: m.text,
          })) as SeriesMarker<Time>[]
        )
        tradeRangesRef.current = data.trade_ranges
        drawTradeHighlights()
      })
      .catch(() => setLoading(false))
  }, [symbol, timeframe, drawTradeHighlights])

  // Redraw highlights when the chart is scrolled/zoomed
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawTradeHighlights)
    return () => {
      try {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawTradeHighlights)
      } catch {
        // chart may already be disposed on unmount
      }
    }
  }, [drawTradeHighlights])

  // ── Price line management ───────────────────────────────────────────────────

  const removeLine = useCallback((key: LineKey) => {
    const s = seriesRef.current
    const line = plRef.current[key]
    if (s && line) s.removePriceLine(line)
    plRef.current[key] = null
  }, [])

  const clearPositionLines = useCallback(() => {
    removeLine('entry')
    removeLine('sl')
    removeLine('tp')
  }, [removeLine])

  const drawPositionLines = useCallback((pos: WsPosition) => {
    clearPositionLines()
    const s = seriesRef.current
    if (!s) return
    const flags = showRef.current
    const d = autoDecimals(pos.entry_price)

    if (flags.entry) {
      plRef.current.entry = s.createPriceLine({
        price: pos.entry_price,
        color: '#eab308',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Entry',
      })
    }
    if (flags.sl && pos.stop_loss_price != null && pos.stop_loss_price > 0) {
      plRef.current.sl = s.createPriceLine({
        price: pos.stop_loss_price,
        color: '#ef4444',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'SL',
      })
    }
    if (flags.tp && pos.take_profit_price != null) {
      plRef.current.tp = s.createPriceLine({
        price: pos.take_profit_price,
        color: '#22c55e',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'TP',
      })
    }
  }, [clearPositionLines])

  // Redraw when toggles change
  useEffect(() => {
    const pos = positionRef.current
    if (pos) drawPositionLines(pos)
  }, [show, drawPositionLines])

  // Reset on symbol change
  useEffect(() => {
    clearPositionLines()
    setPosition(null)
    setPriceInfo(null)
    positionRef.current = null
  }, [symbol, clearPositionLines])

  // ── WebSocket handlers ───────────────────────────────────────────────────────

  const handleCandle = useCallback((candle: WsCandle) => {
    seriesRef.current?.update({ ...candle, time: candle.time as UTCTimestamp })
  }, [])

  const handlePrice = useCallback((data: WsPriceUpdate) => {
    setPriceInfo(data)
  }, [])

  const handlePosition = useCallback((pos: WsPosition) => {
    setPosition(pos)
    positionRef.current = pos
    drawPositionLines(pos)
  }, [drawPositionLines])

  const handlePositionCleared = useCallback(() => {
    setPosition(null)
    positionRef.current = null
    clearPositionLines()
  }, [clearPositionLines])

  useChartWs(symbol, timeframe, {
    onCandle: handleCandle,
    onPrice: handlePrice,
    onPosition: handlePosition,
    onPositionCleared: handlePositionCleared,
    onConnected: () => setWsConnected(true),
    onDisconnected: () => setWsConnected(false),
  })

  // ── Derived display values ────────────────────────────────────────────────

  const change = fmtChange(priceInfo?.change_24h_pct ?? null)
  const changeUp = changeIsPositive(priceInfo?.change_24h_pct ?? null)
  const decimals = priceInfo ? autoDecimals(priceInfo.price) : 2

  function dp(usd: number) { return fmtPrice(cvtPrice(usd), decimals) }

  const slPct = position && position.stop_loss_price != null && position.stop_loss_price > 0
    ? ((position.entry_price - position.stop_loss_price) / position.entry_price * 100)
    : null
  const tpPct = position?.take_profit_price != null
    ? ((position.take_profit_price - position.entry_price) / position.entry_price * 100)
    : null

  // Risk:reward ratio normalised to 1 unit of risk
  const rrRatio = slPct && tpPct ? (tpPct / slPct).toFixed(2) : null

  const tzLabel = TIMEZONES.find(t => t.iana === prefs.timezone)?.label ?? 'UTC'

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Card>
      <CardHeader className="pb-2">
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
              <span className="text-xs text-muted-foreground/60">
                {tzLabel} · {prefs.currency}
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
                  {currencySymbol}{dp(priceInfo.price)}
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
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
            <span>
              Entry{' '}
              <span className="font-mono text-foreground">
                {currencySymbol}{dp(position.entry_price)}
              </span>
            </span>
            {position.stop_loss_price != null && position.stop_loss_price > 0 && (
              <span className="text-red-400">
                SL{' '}
                <span className="font-mono">{currencySymbol}{dp(position.stop_loss_price)}</span>
                {slPct != null && <span className="ml-1 opacity-60">−{slPct.toFixed(2)}%</span>}
              </span>
            )}
            {position.take_profit_price != null && (
              <span className="text-green-400">
                TP{' '}
                <span className="font-mono">{currencySymbol}{dp(position.take_profit_price)}</span>
                {tpPct != null && <span className="ml-1 opacity-60">+{tpPct.toFixed(2)}%</span>}
              </span>
            )}
            {rrRatio && (
              <span className="text-muted-foreground">
                R/R{' '}
                <span className="font-mono text-foreground">
                  −{slPct!.toFixed(2)}% / +{tpPct!.toFixed(2)}%
                  <span className="ml-1 opacity-50">1:{rrRatio}</span>
                </span>
              </span>
            )}
          </div>
        )}

        {/* Line visibility toggles */}
        <div className="flex flex-wrap gap-1.5 pt-2">
          {LINE_META.map(({ key, label, color }) => (
            <button
              key={key}
              onClick={() => toggleLine(key)}
              className={cn(
                'h-6 rounded px-2.5 text-xs font-medium transition-all',
                show[key] ? 'opacity-100' : 'opacity-35',
              )}
              style={{
                color,
                background: `${color}18`,
                border: `1px solid ${color}35`,
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </CardHeader>

      <CardContent className="relative pb-4 pl-2 pr-4">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-b-xl bg-card/90">
            <Skeleton className="h-[380px] w-full" />
          </div>
        )}
        <div ref={containerRef} className="w-full" style={{ height: 380 }} />
      </CardContent>
    </Card>
  )
}
