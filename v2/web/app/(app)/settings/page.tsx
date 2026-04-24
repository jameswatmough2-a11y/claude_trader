// Settings — the tenant's BotConfig editor.
// Same lock-while-running pattern as v1: trading-critical fields disable
// when status.running === true; chart_interval stays editable.
"use client";

export default function SettingsPage() {
  // TODO: fetch /api/bot/config + /api/bot/status
  // TODO: form for: interval_minutes, tracked_symbols, min_confidence,
  //       max_position_pct, max_total_exposure_pct, stop_loss_pct,
  //       take_profit_pct, paper_balance_usdt, chart_interval, model_name
  // TODO: PUT /api/bot/config on save
  return (
    <div>
      <h1 className="text-xl font-semibold">Settings</h1>
      {/* TODO: SettingsForm */}
    </div>
  );
}
