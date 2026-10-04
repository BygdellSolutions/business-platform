import type { NextConfig } from "next";

/**
 * The pages and responses that carry or accept authentication secrets must not be cached or leak through the
 * Referer header: the login and setup pages (the setup link secret is in the URL fragment, which browsers do not
 * send in a request or a Referer, but the page must still send no referrer and never be stored) and the auth API.
 */
const NO_STORE = { key: "Cache-Control", value: "no-store" };
const NO_REFERRER = { key: "Referrer-Policy", value: "no-referrer" };

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/login", headers: [NO_STORE, NO_REFERRER] },
      { source: "/setup", headers: [NO_STORE, NO_REFERRER] },
      { source: "/api/auth/:path*", headers: [NO_STORE, NO_REFERRER] },
    ];
  },
};

export default nextConfig;
