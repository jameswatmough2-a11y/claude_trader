// Overview — the default landing page after sign-in and the centerpiece of
// v1 UX. Layout:
//   - status strip (bot pill, portfolio value, P&L)
//   - open positions panel
//   - decisions feed (newest first, with AI reasoning)
//
// Data fetched via the REST API; live updates pushed via use-tenant-ws.
"use client";

import { BotStatusPill } from "@/components/bot-status-pill";
import { DecisionsFeed } from "@/components/decisions-feed";
import { PositionsPanel } from "@/components/positions-panel";
import { StatCards } from "@/components/stat-cards";
import { useTenantWS } from "@/hooks/use-tenant-ws";

export default function OverviewPage() {
  // Subscribes to /ws and keeps a rolling event log. Children read via props
  // or a shared context — TBD.
  useTenantWS();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <BotStatusPill />
        <StatCards />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="md:col-span-2">
          <DecisionsFeed />
        </div>
        <div>
          <PositionsPanel />
        </div>
      </div>
    </div>
  );
}
