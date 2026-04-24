# Claude Trader — v2 (multi-tenant)

Parallel rebuild of `../crypto_bot/` + `../dashboard/` as a multi-tenant,
Clerk-authenticated platform. v1 remains runnable while v2 is built.

## Layout

```
v2/
  api/   FastAPI backend — shared market plane + per-tenant trading planes
  web/   Next.js 15 (app router) + Clerk auth + shadcn/ui
  db/    SQLAlchemy migration scripts (once schema stabilises)
```

## Run side-by-side with v1

| Service       | v1 port | v2 port |
|---------------|---------|---------|
| FastAPI       | 8000    | 8001    |
| Next.js       | 3000    | 3001    |

## Milestones (see conversation history / parent CLAUDE.md)

1. Auth handshake — Clerk JWT → FastAPI, one protected route
2. Decisions feed live for a single tenant
3. Hard tenant isolation (per-tenant RiskService / ExecutionService)
4. Chart + trade history ported from v1
5. Real-time WS push of decisions / trades

## Exit plan

Once milestone 4 is shipped, delete `crypto_bot/` + `dashboard/` and promote
`v2/api` → `api/`, `v2/web` → `web/`.
