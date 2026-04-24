// Typed client for the v2 API. Thin wrappers over fetch — keeps route
// strings in one place and makes it easy to search for consumers.
//
// Use with useAuthFetch for client components. For server components / route
// handlers, grab the Clerk token via `auth().getToken()` instead.

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// Shape of responses — keep in sync with backend until we generate from OpenAPI.
export type Decision = {
  id: number;
  symbol: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  recommended_size: number;
  reasoning: string;
  decision_time: string;
};

export type OpenPosition = {
  symbol: string;
  entry_price: number;
  size_pct: number;
  current_price: number;
  unrealized_pnl_pct: number;
  stop_loss_price: number | null;
  take_profit_price: number | null;
};

export type Trade = {
  id: number;
  symbol: string;
  status: "open" | "closed";
  entry_price: number;
  exit_price: number | null;
  realized_pnl_usdt: number | null;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
};

// TODO: add functions listDecisions(fetcher, opts), listTrades(...), etc.
