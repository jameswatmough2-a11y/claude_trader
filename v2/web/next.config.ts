import type { NextConfig } from "next";

// Dev-only proxy: browser hits /api/* on :3001, Next rewrites to FastAPI
// on :8001. Keeps the browser origin consistent and avoids CORS preflights.
// Swap for direct calls with CORS in production.
const config: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"}/api/:path*`,
      },
    ];
  },
};

export default config;
