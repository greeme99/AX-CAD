import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // container image and Windows bundle only; local dev/start stay unchanged
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // the /api rewrite proxy buffers request bodies and cuts them at 10 MB by default; patches and
  // drawings are larger. Must sit under `experimental` (a top-level key is silently ignored).
  experimental: { middlewareClientMaxBodySize: "1gb" },
  async rewrites() {
    // per-PC Windows bundle (installer/windows/build.ps1): the API listens on 127.0.0.1:18000
    const api = process.env.AXCAD_API_ORIGIN ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    // the per-PC bundle has no nginx in front, so these mirror deploy/nginx/locations.inc. Pages:
    // scripts may only fetch/connect to this origin (closes the usual exfiltration paths for an
    // injected script; the PC token sits in localStorage). 'unsafe-inline': Next.js bootstraps with
    // inline scripts. 'unsafe-eval' only in `next dev` (React Refresh needs it).
    const dev = process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";
    const page =
      `default-src 'self'; script-src 'self' 'unsafe-inline'${dev}; style-src 'self' 'unsafe-inline'; ` +
      "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; " +
      "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'";
    const h = [
      { key: "Content-Security-Policy", value: page },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "no-referrer" },
    ];
    // not applied to the /api rewrite below (Next.js skips external rewrites): the API sets its own
    // (backend/api/main.py API_HEADERS)
    return [{ source: "/:path*", headers: h }];
  },
};

export default nextConfig;
