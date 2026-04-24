// Open positions sidebar on the overview page.
// Each row: symbol, entry price, live price, unrealised P&L %, distance-to-SL.
"use client";

export function PositionsPanel() {
  // TODO: fetch /api/positions; subscribe to price_tick events for live P&L
  return (
    <div className="rounded border p-4">
      <h2 className="text-sm font-semibold mb-3">Open positions</h2>
      {/* TODO: rows */}
    </div>
  );
}
