const defaultProxyTarget = process.env.NODE_ENV === "production" ? "http://api:8000" : "http://localhost:8400";
const rawProxyTarget =
  process.env.API_PROXY_TARGET || process.env.NEXT_PUBLIC_API_BASE_URL || defaultProxyTarget;
const API_PROXY_TARGET = rawProxyTarget.replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // The chat endpoint (/api/chat) drives the self-hosted EXAONE LLM, whose
    // tool-calling turns routinely take 30-90s on a GPU shared with the folding
    // workers. Next's rewrite proxy defaults to a 30s upstream timeout and
    // resets the connection ("socket hang up" ECONNRESET) past that, surfacing
    // to users as an Internal Server Error. Raise it to match the backend's own
    // 300s EXAONE read timeout (providers.py `_LOCAL_TIMEOUT`).
    proxyTimeout: 300_000,
  },
  // `output: standalone` is for minimal Docker images and breaks `next start`
  // rewrites; this deployment runs `next start` under systemd, so omit it.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
