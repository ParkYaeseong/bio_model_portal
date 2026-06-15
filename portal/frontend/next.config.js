const defaultProxyTarget = process.env.NODE_ENV === "production" ? "http://api:8000" : "http://localhost:8400";
const rawProxyTarget =
  process.env.API_PROXY_TARGET || process.env.NEXT_PUBLIC_API_BASE_URL || defaultProxyTarget;
const API_PROXY_TARGET = rawProxyTarget.replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
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
