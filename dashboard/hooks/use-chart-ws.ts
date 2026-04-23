'use client'

import { useEffect, useRef } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface WsCandle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface WsPosition {
  entry_price: number
  stop_loss_price: number | null
  take_profit_price: number | null
  size_pct: number
}

export interface WsPriceUpdate {
  price: number
  change_24h_pct: number | null
}

type ServerMessage =
  | { type: 'candle'; data: WsCandle }
  | { type: 'price'; price: number; change_24h_pct: number | null }
  | { type: 'position'; entry_price: number; stop_loss_price: number | null; take_profit_price: number | null; size_pct: number }
  | { type: 'position_cleared' }

export interface ChartWsHandlers {
  onCandle: (candle: WsCandle) => void
  onPrice: (data: WsPriceUpdate) => void
  onPosition: (pos: WsPosition) => void
  onPositionCleared?: () => void
  onConnected?: () => void
  onDisconnected?: () => void
}

// ── Config ────────────────────────────────────────────────────────────────────

const WS_BASE =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_WS_URL ?? `ws://${window.location.hostname}:8000`)
    : 'ws://localhost:8000'

const BACKOFF = [300, 600, 1200, 2500, 5000, 10000]

// ── Hook ─────────────────────────────────────────────────────────────────────
//
// Each effect invocation owns its own `cancelled` flag and `retries` counter.
// When symbol/interval changes, React runs cleanup (sets cancelled = true, closes
// the socket) before starting the next effect. The old socket's onclose handler
// checks its own local `cancelled` — so it never reconnects after cleanup, even
// if the new effect has already started and reset its own flag.

export function useChartWs(
  symbol: string,
  interval: string,
  handlers: ChartWsHandlers,
) {
  // Always-current handler ref so stale closures don't capture old callbacks
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  useEffect(() => {
    let cancelled = false
    let retries = 0
    let ws: WebSocket | null = null

    function connect() {
      if (cancelled) return

      ws = new WebSocket(`${WS_BASE}/ws/chart`)

      ws.onopen = () => {
        retries = 0
        ws!.send(JSON.stringify({ type: 'subscribe', symbol, interval }))
        handlersRef.current.onConnected?.()
      }

      ws.onmessage = (ev: MessageEvent) => {
        if (cancelled) return
        let msg: ServerMessage
        try {
          msg = JSON.parse(ev.data as string) as ServerMessage
        } catch {
          return
        }
        const h = handlersRef.current
        switch (msg.type) {
          case 'candle':
            h.onCandle(msg.data)
            break
          case 'price':
            h.onPrice({ price: msg.price, change_24h_pct: msg.change_24h_pct })
            break
          case 'position':
            h.onPosition({
              entry_price: msg.entry_price,
              stop_loss_price: msg.stop_loss_price,
              take_profit_price: msg.take_profit_price,
              size_pct: msg.size_pct,
            })
            break
          case 'position_cleared':
            h.onPositionCleared?.()
            break
        }
      }

      ws.onclose = () => {
        handlersRef.current.onDisconnected?.()
        if (cancelled) return  // effect was cleaned up — do not reconnect
        const delay = BACKOFF[Math.min(retries, BACKOFF.length - 1)]
        retries++
        setTimeout(connect, delay)
      }

      ws.onerror = () => ws?.close()
    }

    connect()

    return () => {
      cancelled = true   // stop any pending reconnect timer from firing
      ws?.close()
      ws = null
    }
  }, [symbol, interval]) // new effect = new connection = correct symbol
}
