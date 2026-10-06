import type { NextConfig } from "next";

/**
 * The pages and responses that carry or accept authentication secrets must not be cached or leak through the
 * Referer header: the login and setup pages (the setup link secret is in the URL fragment, which browsers do not
 * send in a request or a Referer, but the page must still send no referrer and never be stored) and the auth API.
 */
const NO_STORE = { key: "Cache-Control", value: "no-store" };
const NO_REFERRER = { key: "Referrer-Policy", value: "no-referrer" };

/**
 * The first-deployment security headers for EVERY response (not a Content-Security-Policy: that is tracked separately,
 * report-only first). Later entries win for the same header, so the secret-bearing pages below can tighten the Referrer-Policy.
 *   nosniff            the browser must not guess a content type
 *   Referrer-Policy    a safe global default: same-origin requests get the URL, cross-origin ones only the origin
 *   X-Frame-Options    the product is never embedded in another page
 *   Permissions-Policy powerful browser features the product never uses are switched off
 * HSTS is not here: it depends on the runtime configuration and is emitted by `proxy.ts`.
 */
export const BASELINE_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), bluetooth=(), serial=(), midi=(), magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()" },
];

const nextConfig: NextConfig = {
  // A self-contained server (`node server.js`) with only the files it needs: the production image copies this, not
  // the source tree or node_modules.
  output: "standalone",
  // No "X-Powered-By: Next.js" header.
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: BASELINE_HEADERS },
      { source: "/login", headers: [NO_STORE, NO_REFERRER] },
      { source: "/setup", headers: [NO_STORE, NO_REFERRER] },
      { source: "/invite", headers: [NO_STORE, NO_REFERRER] },
      { source: "/api/invite/:path*", headers: [NO_STORE, NO_REFERRER] },
      { source: "/api/auth/:path*", headers: [NO_STORE, NO_REFERRER] },
    ];
  },
};

export default nextConfig;
