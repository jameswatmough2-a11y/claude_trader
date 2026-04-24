// Four-stat strip: portfolio value, unrealized P&L, realized P&L today,
// win rate. All derived from /api/trades + /api/positions + paper balance.
"use client";

export function StatCards() {
  // TODO: fetch summary from backend (new endpoint probably: /api/stats)
  return (
    <div className="grid grid-cols-4 gap-3">
      {["Portfolio", "Unrealized P&L", "Realized today", "Win rate"].map((label) => (
        <div key={label} className="rounded border p-3 text-sm">
          <div className="text-muted-foreground">{label}</div>
          <div className="text-lg font-semibold">—</div>
        </div>
      ))}
    </div>
  );
}
