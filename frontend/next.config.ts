import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // container image and Windows bundle only; local dev/start stay unchanged
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // allow large patch-zip uploads via the /api rewrite proxy (default 10 MB)
  middlewareClientMaxBodySize: "100mb",
  async rewrites() {
    // per-PC Windows bundle (installer/windows/build.ps1): the API listens on 127.0.0.1:18000
    const api = process.env.AXCAD_API_ORIGIN ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    // the per-PC bundle has no nginx in front (deploy/nginx sets these on the server): no framing
    const h = [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      { key: "X-Content-Type-Options", value: "nosniff" },
    ];
    return [{ source: "/:path*", headers: h }];
  },
};

export default nextConfig;
