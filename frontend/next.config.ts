import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // container image only (deploy/web.Dockerfile); local dev/start stay unchanged
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }];
  },
};

export default nextConfig;
