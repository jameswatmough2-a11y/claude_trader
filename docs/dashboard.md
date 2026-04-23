# Dashboard

The dashboard is a Next.js 16 application in `dashboard/` that provides a real-time monitoring UI for the trading bot. It communicates with the FastAPI backend via REST (proxied through Next.js rewrites) and a direct WebSocket connection.

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 (App Router, React Server Components) |
| Language | TypeScript |
| Styling | Tailwind CSS v4 |
| Components | shadcn/ui (nova preset) — Card, Badge, Button, Table, Progress, Skeleton, Separator |
| Charts | TradingView Lightweight Charts v5 |
| Legacy chart | Recharts (ComposedChart) — used for the simple line price chart only |
| Icons | lucide-react |

---

## Running

```bash
cd dashboard
npm install
npm run dev      # starts on http://localhost:3000
```

The FastAPI backend must be running on port 8000. The Next.js dev server proxies REST calls and the dashboard connects directly to the WebSocket.

---

## Layout

The app uses a persistent sidebar layout defined in `app/layout.tsx`:

```
┌──────────┬─────────────────────────────────────────────┐
│          │                                             │
│ Sidebar  │           Main content (scrollable)        │
│ 220 px   │                                             │
│          │  Overview page:                             │
│ · Brand  │  · Stats cards (symbols, model, decisions) │
│ · Nav    │  · Candlestick chart (live, TradingView)   │
│ · Status │  · AI Decisions table                      │
│          │  · Positions + Assets tables                │
└──────────┴─────────────────────────────────────────────┘
```

The sidebar is hidden on screens below `md` breakpoint (768 px).

---

## Component Map

```
app/
  layout.tsx                  # Root layout — fonts, ThemeProvider, sidebar shell
  page.tsx                    # Composes Dashboard + CandlestickChart
  components/
    app-sidebar.tsx           # Sidebar: brand, nav, bot status, last cycle
    dashboard.tsx             # Stats cards, decisions table, positions, assets
    candlestick-chart.tsx     # TradingView candlestick chart (live + historical)
hooks/
  use-chart-ws.ts             # WebSocket hook with reconnect backoff
lib/
  chart-utils.ts              # fmtPrice, fmtChange, autoDecimals
  utils.ts                    # shadcn cn() helper
```

---

## API Connectivity

All REST calls go through Next.js rewrites configured in `next.config.mjs`:

```
Browser → /api/bot/*   → http://localhost:8000/*
Browser → /api/chart/* → http://localhost:8000/chart/*
```

The WebSocket connects directly from the browser to the backend (Next.js rewrites do not proxy WebSocket connections):

```
Browser → ws://localhost:8000/ws/chart
```

The WS host is derived at runtime as `ws://<same-hostname>:8000`. Override with the `NEXT_PUBLIC_WS_URL` environment variable for deployments:

```env
NEXT_PUBLIC_WS_URL=wss://your-server.com
```

---

## Candlestick Chart (`candlestick-chart.tsx`)

The chart component is the most complex piece. Key design decisions:

**No rerender per tick.** The TradingView chart instance is stored in a `useRef`, not state. WebSocket candle updates call `series.update()` directly on the chart ref — React never rerenders the component on price ticks.

**Two data sources, two roles:**
- REST `GET /api/chart/history` — loads the full historical OHLCV dataset on mount and on symbol/timeframe change
- WebSocket `/ws/chart` — streams the current live candle at ~5 Hz, updating the rightmost bar in place

**Price lines.** When a position is open, three horizontal `IPriceLine` objects are drawn on the series:
- Entry (dotted, slate)
- Stop loss (dashed, red)
- Take profit (dashed, green) — only if `take_profit_price` is non-null

Price lines are cleared and redrawn whenever a `position` WS message arrives or the symbol changes.

**Symbol list** is fetched from `GET /api/bot/health` on mount and populates the symbol tabs. If the current symbol is not in the tracked list, it falls back to the first symbol.

---

## WebSocket Hook (`hooks/use-chart-ws.ts`)

The hook manages a single WebSocket connection per `(symbol, interval)` pair. When either changes, React runs the effect cleanup (closes the socket, sets a local `cancelled` flag) before opening a new connection.

**Reconnect strategy:** exponential backoff — 300 ms, 600 ms, 1.2 s, 2.5 s, 5 s, 10 s.

**Race condition avoidance:** each effect invocation owns its own `cancelled` boolean in the closure. The old socket's `onclose` handler checks its own `cancelled` flag — which is `true` after cleanup — so it never tries to reconnect under the new symbol's connection.

**Handler stability:** handlers (`onCandle`, `onPrice`, `onPosition`) are stored in a `handlersRef` that is updated every render. The WebSocket closure captures the ref, not the handlers themselves, so changing symbol/interval does not require recreating the WS just because handler functions changed identity.

---

## Sidebar (`app-sidebar.tsx`)

Polls `GET /api/bot/health` and `GET /api/bot/decisions?limit=1` every 30 seconds to display:
- Paper / Live mode badge
- Online / Offline status (green dot or red)
- Model name (stripped `claude-` prefix)
- Tracked symbols as small badges
- Last trading cycle timestamp

No interaction — display only.

---

## Theming

The app is wrapped in `ThemeProvider` (next-themes). Dark mode is toggled by adding the `dark` class to `<html>`.

TradingView chart colors are hardcoded (SVG attributes do not support CSS variables):
- Grid lines: `#1e293b` (slate-800)
- Text: `#64748b` (slate-500)
- Up candles: `#22c55e` (green-500)
- Down candles: `#ef4444` (red-500)
- Stop loss line: `#ef4444`
- Take profit line: `#22c55e`
- Entry line: `#94a3b8` (slate-400)

All other UI uses shadcn semantic tokens (`bg-background`, `text-muted-foreground`, etc.) and adapts automatically to dark/light mode.

---

## Adding a New Page

1. Create `app/your-page/page.tsx`
2. Add a nav item in `app-sidebar.tsx` using `<Link href="/your-page">` wrapped in the same `div` style as the existing Overview item
3. Mark the active item using `usePathname()` from `next/navigation`

```tsx
// app-sidebar.tsx
import { usePathname } from 'next/navigation'
import Link from 'next/link'

const pathname = usePathname()

<Link href="/your-page">
  <div className={cn(
    'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium',
    pathname === '/your-page'
      ? 'bg-secondary text-secondary-foreground'
      : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
  )}>
    <YourIcon className="size-4" />
    Your Page
  </div>
</Link>
```
