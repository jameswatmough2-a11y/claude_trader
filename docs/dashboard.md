# Dashboard

The dashboard is a Next.js 16 application in `dashboard/` that provides a real-time monitoring UI for the trading bot. It communicates with the FastAPI backend via REST (proxied through Next.js rewrites) and a direct WebSocket connection for the chart.

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 (App Router, React Server Components) |
| Language | TypeScript |
| Styling | Tailwind CSS v4 |
| Components | shadcn/ui — Card, Badge, Button, Table, Progress, Skeleton, Separator, Select |
| Charts | TradingView Lightweight Charts v5 |
| Icons | lucide-react |

---

## Running

```bash
cd dashboard
npm install
npm run dev      # starts on http://localhost:3000
```

The FastAPI backend must be running on port 8000.

---

## Layout

The app uses a persistent sidebar layout defined in `app/layout.tsx`:

```
┌──────────┬──────────────────────────────────────────────────┐
│          │                                                  │
│ Sidebar  │           Main content (scrollable)             │
│ 220 px   │                                                  │
│          │  Overview:   Bot controls, stats, chart, tables │
│ · Brand  │  Settings:   Config editor                      │
│ · Nav    │  Logs:       Filtered event log viewer          │
│ · Status │                                                  │
│          │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

On screens below `md` (768 px) the sidebar is replaced by a top bar with a hamburger button that opens a full-height slide-in drawer (`app/components/mobile-nav.tsx`).

---

## Component Map

```
app/
  layout.tsx                  # Root layout — fonts, ThemeProvider, sidebar + MobileNav shell
  page.tsx                    # Root redirect → /overview
  overview/page.tsx           # Composes Dashboard + CandlestickChart
  settings/page.tsx           # Settings page (config editor)
  logs/page.tsx               # Logs page (event log viewer)
  components/
    app-sidebar.tsx           # Sidebar: brand, nav (Overview/Settings/Logs), bot status, clock
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

All REST calls go through Next.js rewrites in `next.config.mjs`:
```
Browser → /api/bot/*   → http://localhost:8000/*
Browser → /api/chart/* → http://localhost:8000/chart/*
```

The WebSocket connects directly from the browser to the backend:
```
Browser → ws://localhost:8000/ws/chart
```

Override with `NEXT_PUBLIC_WS_URL` for remote deployments.

---

## Pages

### Overview (`/`)

Bot controls (Start / Stop / Reset DB), stat cards, live candlestick chart, AI decisions table, positions and assets tables.

### Settings (`/settings`)

Flat two-column layout:

```
┌────────────────────┐  ┌────────────────────────────────────┐
│  AI MODEL          │  │  TRADING                           │
│  Model             │  │  Tracked Symbols                   │
│  Reanalysis Interval│  │  Max Position Size (%)            │
│  OHLCV Interval    │  │  Max Total Exposure (%)            │
│  Min Confidence    │  │  Stop Loss (%)                     │
│                    │  │  Take Profit (%)                   │
│                    │  │  Paper Balance (USDT)              │
│                    │  │  Taker Fee Rate                    │
└────────────────────┘  └────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DISPLAY                                                    │
│  Chart Interval          Currency         Timezone          │
└─────────────────────────────────────────────────────────────┘

[Unsaved changes]                          [Revert]  [Save]
```

**OHLCV Interval** — toggle button group: `1m` `5m` `15m` `1h` `4h` `1d`. Controls which candle timeframe is fetched per cycle and labelled in the AI prompt. Locked while the bot is running.

**Taker Fee Rate** — number input (default `0.001` = 0.1%). Controls paper trade fee deductions. Locked while the bot is running.

**Lock behaviour:** When the bot is running, AI Model and Trading fields are disabled (amber lock banner shown). Only the three Display fields remain editable. Save merges the display values onto the last saved trading config.

### Logs (`/logs`)

Structured event log viewer backed by `GET /logs` from the backend.

```
┌──────────────────────────────────────────────────────────────┐
│  Filters:  Level ▾   Component ▾   Event Type ▾   Symbol    │
│            [Apply]  [Refresh]                                │
├────┬───────────────────┬─────────┬───────────────┬──────────┤
│ ▶  │ Timestamp         │ Level   │ Component     │ Message  │
├────┼───────────────────┼─────────┼───────────────┼──────────┤
│ ▶  │ 2026-04-23 14:00  │ INFO    │ trading_cycle │ Cycle... │
│ ▼  │ 2026-04-23 14:00  │ ERROR   │ ai_service    │ API err  │
│    │ details: { "error": "529 overloaded" }                  │
└────┴───────────────────┴─────────┴───────────────┴──────────┘
                              Page 1 of 4   ← Prev  Next →
```

**Filters:** level (select), component (select, populated from `GET /logs/components`), event_type (select, populated from `GET /logs/event-types`), symbol (text input).

**Table columns:** expand toggle, timestamp (timezone-aware via `fmtTime`), level badge (blue INFO / amber WARNING / red ERROR), component, event_type, symbol, message.

**Expandable rows:** clicking the expand toggle reveals `details_json` formatted as pretty-printed JSON.

**Pagination:** page / total pages display with previous/next buttons. Page size fixed at 50.

**Refresh button:** re-fetches current filter results.

---

## Candlestick Chart (`candlestick-chart.tsx`)

Built on TradingView Lightweight Charts v5.

**No rerender per tick.** Chart instance stored in `useRef`, not state. WebSocket updates call `series.update()` directly.

**Two data sources:**
- REST `GET /api/chart/history` — historical OHLCV on mount and symbol/interval change
- WebSocket `ws://localhost:8000/ws/chart` — live candle stream at ~5 Hz

**Price lines when a position is open:**

| Line | Colour | Style |
|---|---|---|
| Live price | Yellow `#eab308` | Solid |
| Entry | Slate `#94a3b8` | Dotted |
| Stop loss | Red `#ef4444` | Dashed |
| Take profit | Green `#22c55e` | Dashed |

**Toggle pattern:** `showRef` (ref) updated synchronously in `toggleLine()` so stable `useCallback` handlers read the current value immediately; `show` (state) triggers re-render + `useEffect` that redraws position lines.

**Position row** below the chart shows: entry price, SL price + `−X.XX%` from entry, TP price + `+X.XX%` from entry, position size %.

---

## WebSocket Hook (`hooks/use-chart-ws.ts`)

Manages one WebSocket per `(symbol, interval)` pair. Reconnect strategy: exponential backoff — 300 ms → 600 ms → 1.2 s → 2.5 s → 5 s → 10 s.

Each effect invocation owns a `cancelled` boolean in its closure to prevent the old socket's `onclose` handler from scheduling a reconnect under the new symbol's connection.

---

## Sidebar (`app-sidebar.tsx`)

Polls `GET /api/bot/health` every 30 seconds. Displays:
- Paper / Live mode badge
- Online / Offline status (animated green dot or red)
- Model name (stripped `claude-` prefix)
- Tracked symbols as `Badge` components
- Live clock — second-accurate, synced to next exact second boundary; displayed in the user's selected timezone

Navigation links: **Overview**, **Settings**, **Logs** (with `ScrollText` icon).

On mobile (`md:hidden`) the sidebar is replaced by `MobileNav` with the same three nav links in a slide-in drawer.

---

## Bot Controls

On the Overview page:

- **Start** — fires one cycle immediately, then runs on the configured interval
- **Stop** — pauses scheduler; positions stay open, stop-loss continues on restart
- **Reset DB** — drops and recreates all tables; disabled while the bot is running

---

## Timezone Handling

All timestamps from the backend are naive UTC ISO strings (no `Z` or `+offset`). JavaScript's `Date` constructor interprets these as local time, which is wrong on non-UTC machines.

`fmtTime` in `DisplayPrefsProvider` fixes this by appending `Z` before constructing the `Date`:

```tsx
const utc = /[Z+]/.test(iso) ? iso : iso + 'Z'
new Date(utc).toLocaleString('en', { timeZone: prefs.timezone, ... })
```

Every component that renders a timestamp — decisions table, logs table, positions table — calls `fmtTime` from `useDisplayPrefs()`.

---

## Theming

Wrapped in `next-themes` `ThemeProvider`. Dark mode toggles the `dark` class on `<html>`.

TradingView chart colors are hardcoded (SVG attributes do not support CSS variables). All other UI uses shadcn semantic tokens (`bg-background`, `text-muted-foreground`, etc.) and adapts automatically.

---

## Adding a New Page

1. Create `app/your-page/page.tsx`
2. Add a `NavLink` in `app-sidebar.tsx`:
   ```tsx
   <NavLink href="/your-page" icon={<YourIcon className="size-4" />}>
     Your Page
   </NavLink>
   ```
3. Add the same entry to the `links` array in `app/components/mobile-nav.tsx`
