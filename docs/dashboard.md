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

On screens below `md` (768 px) the sidebar is replaced by a top bar with a hamburger button that opens a full-height slide-in drawer (`app/components/mobile-nav.tsx`). The drawer closes automatically on navigation and locks body scroll while open.

---

## Component Map

```
app/
  layout.tsx                  # Root layout — fonts, ThemeProvider, sidebar + MobileNav shell
  page.tsx                    # Root redirect → /overview
  overview/page.tsx           # Composes Dashboard + CandlestickChart
  settings/page.tsx           # Settings page
  components/
    app-sidebar.tsx           # Sidebar: brand, nav, bot status, live clock, theme toggle
    mobile-nav.tsx            # Mobile top-bar + slide-in drawer (md:hidden)
    dashboard.tsx             # Stats cards, decisions table, positions, assets
    candlestick-chart.tsx     # TradingView candlestick chart (live + historical)
  providers/
    display-prefs-provider.tsx # Global timezone/currency context
hooks/
  use-chart-ws.ts             # WebSocket hook with reconnect backoff
lib/
  chart-utils.ts              # fmtPrice, fmtChange, autoDecimals
  display-prefs.ts            # CURRENCIES, TIMEZONES, exchange rates, localStorage helpers
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

**Auto-sizing.** The chart is created with `autoSize: true`. Lightweight Charts internally observes the container element and updates the canvas width whenever the layout changes. No manual `ResizeObserver` or `width: el.offsetWidth` is needed — the chart always fills its container regardless of viewport width.

**Two data sources, two roles:**
- REST `GET /api/chart/history` — loads the full historical OHLCV dataset on mount and on symbol/timeframe change; `timeScale().fitContent()` is called immediately after to fill all candles across the visible range
- WebSocket `/ws/chart` — streams the current live candle at ~5 Hz, updating the rightmost bar in place

**Price lines.** When a position is open, three horizontal `IPriceLine` objects are drawn on the series:
- Entry (yellow `#eab308`, dotted) — title `'Entry'`
- Stop loss (red `#ef4444`, dashed) — title `'SL'`
- Take profit (green `#22c55e`, dashed) — title `'TP'`; only if `take_profit_price` is non-null

Titles are short labels — the right-axis label shows the exact price. Price lines are cleared and redrawn whenever a `position` WS message arrives or the symbol changes.

**Symbol list** is fetched from `GET /api/bot/health` on mount and populates the symbol tabs. If the current symbol is not in the tracked list, it falls back to the first symbol.

---

## WebSocket Hook (`hooks/use-chart-ws.ts`)

The hook manages a single WebSocket connection per `(symbol, interval)` pair. When either changes, React runs the effect cleanup (closes the socket, sets a local `cancelled` flag) before opening a new connection.

**Reconnect strategy:** exponential backoff — 300 ms, 600 ms, 1.2 s, 2.5 s, 5 s, 10 s.

**Race condition avoidance:** each effect invocation owns its own `cancelled` boolean in the closure. The old socket's `onclose` handler checks its own `cancelled` flag — which is `true` after cleanup — so it never tries to reconnect under the new symbol's connection.

**Handler stability:** handlers (`onCandle`, `onPrice`, `onPosition`) are stored in a `handlersRef` that is updated every render. The WebSocket closure captures the ref, not the handlers themselves, so changing symbol/interval does not require recreating the WS just because handler functions changed identity.

---

## Sidebar (`app-sidebar.tsx`)

Polls `GET /api/bot/health` every 30 seconds to display:
- Paper / Live mode badge
- Online / Offline status (animated green dot or red)
- Model name (stripped `claude-` prefix)
- Tracked symbols as small badges
- Live clock — second-accurate, synced to the next exact second boundary via `setTimeout(1000 - Date.now() % 1000)` then `setInterval(1000)`; displayed in the user's selected timezone using `prefs.timezone` from `DisplayPrefsProvider`

The clock and theme toggle share the same bottom section. The dark/light mode toggle is displayed directly below the clock readout. Theme rendering is guarded by a `mounted` state to avoid SSR hydration mismatch with `next-themes`.

On mobile (`md:hidden`) the sidebar is replaced by `MobileNav`, which provides a top bar with the brand name and a hamburger button. Tapping the hamburger slides in a full-height drawer from the left with the same nav links.

---

## Settings Page (`app/settings/page.tsx`)

The settings page uses a flat, card-free layout split into three sections:

```
┌────────────────────┐  ┌────────────────────────────────────┐
│  AI MODEL          │  │  TRADING                           │
│  ─────────────     │  │  ──────────                        │
│  Model             │  │  Tracked Symbols                   │
│  Reanalysis Interval│  │  Max Position Size (%)             │
│  Min Confidence    │  │  Max Total Exposure (%)            │
│                    │  │  Stop Loss (%)                     │
│                    │  │  Take Profit (%)                   │
│                    │  │  Paper Balance (USDT)              │
└────────────────────┘  └────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DISPLAY                                                    │
│  Chart Interval          Currency         Timezone          │
└─────────────────────────────────────────────────────────────┘

[Unsaved changes]                          [Revert]  [Save]
```

The two upper columns (`AI Model` and `Trading`) use `grid-cols-2` on `md+` and collapse to a single column on mobile. The `Display` row uses `grid-cols-3` on `sm+`.

**Single SaveBar.** There is one save button at the bottom covering all three sections.

**Lock behaviour:**
- **Bot not running** — all fields editable. Save sends the full config object. Revert restores all fields.
- **Bot running** — only the three Display fields (`chart_interval`, `timezone`, `display_currency`) are editable; AI Model and Trading fields are disabled. A lock icon appears next to the `AI MODEL` and `TRADING` section headings and an amber banner explains the lock. Save sends `{ ...savedTrading, chart_interval, timezone, display_currency }`, preserving trading values from the last saved state. Revert restores only the Display fields.

`isDirty` is computed differently depending on lock state — when locked it only checks the three display keys; when unlocked it checks all keys. This ensures the Save/Revert buttons remain inactive when the user cannot change anything meaningful.

Display preferences (`timezone`, `display_currency`) are applied globally via `DisplayPrefsProvider` immediately after a successful save.

---

## Bot Controls

The Overview page (`app/overview/page.tsx`) includes three bot control buttons rendered by `dashboard.tsx`:

- **Start** — starts the hourly trading cycle. APScheduler fires one cycle immediately, then continues on the configured interval.
- **Stop** — pauses the scheduler. Open positions are not closed; stop-loss and take-profit still resume on the next start.
- **Reset DB** — drops and recreates all database tables, clearing all trade history and resetting the paper balance. **This button is disabled while the bot is running** — `status.running` must be `false`. This prevents resetting the database mid-cycle and losing in-flight execution state.

---

## Timezone Handling

All timestamps stored in the database are produced by Python's `datetime.utcnow()`. SQLite has no native datetime type — it persists these as ISO 8601 strings **without a timezone suffix** (e.g. `"2026-04-23T01:34:00"`).

**The problem:** JavaScript's `Date` constructor interprets bare ISO strings (no `Z` or `+offset`) as *local time*, not UTC. On a machine in BST (UTC+1), `new Date("2026-04-23T01:34:00")` → 01:34 BST = 00:34 UTC. Displaying it in any timezone will be one hour off.

**The fix:** `fmtTime` in `DisplayPrefsProvider` detects the missing designator and appends `Z` before constructing the `Date`:

```tsx
const utc = /[Z+]/.test(iso) ? iso : iso + 'Z'
new Date(utc).toLocaleString('en', { timeZone: prefs.timezone, ... })
```

`Z` forces UTC interpretation; `toLocaleString` with the IANA timezone string then converts to whatever the user has selected. Every component that renders a timestamp — the decisions table, positions table, any future views — calls `fmtTime` from `useDisplayPrefs()`. The fix is in one place and propagates everywhere.

The backend itself never changes: Python always logs UTC, SQLite always stores the naive string, and the API always returns the naive string. The timezone setting has no effect on the backend — it is purely a display preference applied in the browser.

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
