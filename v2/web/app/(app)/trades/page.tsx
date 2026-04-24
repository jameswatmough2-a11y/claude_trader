// Trades history. Closed trades, sortable/filterable. Where users build
// trust in the bot — make P&L and exit_reason prominent.
"use client";

export default function TradesPage() {
  // TODO: fetch /api/trades?status=closed, render table:
  //       symbol | entry | exit | duration | P&L | exit_reason
  return (
    <div>
      <h1 className="text-xl font-semibold">Trades</h1>
      {/* TODO: TradesTable */}
    </div>
  );
}
