// The centerpiece. Initial page load: GET /api/decisions?limit=50.
// After mount: append new decisions pushed via use-tenant-ws so the feed
// feels live.
//
// Each card: action badge (BUY green / SELL red / HOLD amber), symbol,
// confidence, one-line reasoning (click to expand), relative timestamp.
"use client";

export function DecisionsFeed({ symbol }: { symbol?: string } = {}) {
  // TODO: useState<Decision[]>([])
  // TODO: initial fetch + WS append
  // TODO: filter by `symbol` prop when viewing the per-symbol page
  return (
    <div className="rounded border p-4">
      <h2 className="text-sm font-semibold mb-3">Decisions</h2>
      {/* TODO: cards */}
    </div>
  );
}
