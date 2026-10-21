import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // container image and Windows bundle only; local dev/start stay unchanged
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async rewrites() {
    // per-PC Windows bundle (installer/windows/build.ps1): the API listens on 127.0.0.1:18000
    const api = process.env.AXCAD_API_ORIGIN ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
