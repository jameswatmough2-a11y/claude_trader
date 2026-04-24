// Status pill + Start/Stop button. Polls /api/bot/status every 5s; triggers
// WS push updates too so the pill flips within a second of the user clicking.
"use client";

export function BotStatusPill() {
  // TODO: fetch /api/bot/status
  // TODO: POST /api/bot/start, /api/bot/stop
  // TODO: show "Running" green / "Stopped" grey
  return <div className="h-8 px-3 flex items-center rounded-full border text-sm">Bot status</div>;
}
