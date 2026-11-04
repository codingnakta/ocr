import type { NextConfig } from "next";

// The browser only ever talks to Next.js (same origin). /api/* is proxied to the
// FastAPI backend, so the Anthropic API key never reaches the browser and auth
// cookies stay first-party. BACKEND_URL is a server-side variable (no NEXT_PUBLIC_).
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

// STATIC_EXPORT=1 builds plain HTML/JS files (frontend/out) that the FastAPI server
// serves itself — used by the Windows desktop app (one program, no Node.js needed).
const staticExport = process.env.STATIC_EXPORT === "1";

const nextConfig: NextConfig = staticExport
  ? { output: "export", trailingSlash: true, poweredByHeader: false, images: { unoptimized: true } }
  : {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }] },
    ];
  },
  };

export default nextConfig;
