// Symbol detail — candlestick chart + per-symbol decisions list.
// Port v1's candlestick-chart.tsx here; adapt the WS hook to the new endpoint.
"use client";

import { use } from "react";

export default function SymbolPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = use(params);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{symbol}</h1>
      {/* TODO: <CandlestickChart symbol={symbol} /> (ported from v1) */}
      {/* TODO: <DecisionsFeed symbol={symbol} /> */}
    </div>
  );
}
