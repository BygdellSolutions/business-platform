# syntax=docker/dockerfile:1
#
# The production image of the Next.js frontend / BFF (build context: this directory, `frontend/`).
#
#   docker build -t business-platform-frontend frontend
#
# ONE image for every environment. Nothing about the deployment is baked in: the authentication mode, the public
# origin and the backend address are read from the environment when the server STARTS (and the pages that depend on
# them are rendered per request, never prerendered). The build-time ARGs below exist only so the image tests can
# prove that a build environment has no effect; a real build passes none of them.
#
# The final image contains Node 22, the standalone server (`server.js` plus the few traced node_modules it needs),
# `.next/static` and `public`: no sources, tests, dev dependencies, host node_modules or .env files. It runs as the
# non-root `node` user on port 3000. No font service is contacted (the sans font is bundled in app/fonts).

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG TEST_BUILD_MODE
ARG TEST_BUILD_ENVIRONMENT
ARG TEST_BUILD_ORIGIN
ARG TEST_BUILD_BACKEND
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Only the variables a caller passed are set for the build step; none is set by default.
RUN env ${TEST_BUILD_MODE:+AUTH_MODE=$TEST_BUILD_MODE} ${TEST_BUILD_ENVIRONMENT:+APP_ENV=$TEST_BUILD_ENVIRONMENT} ${TEST_BUILD_ORIGIN:+PUBLIC_ORIGIN=$TEST_BUILD_ORIGIN} ${TEST_BUILD_BACKEND:+BACKEND_URL=$TEST_BUILD_BACKEND} npm run build

FROM node:22-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN mkdir -p .next/cache && chown -R node:node .next/cache
USER node
EXPOSE 3000
# LIVENESS only: GET /api/health depends on nothing, so a backend or database outage never marks THIS process unhealthy
# (an orchestrator that restarts unhealthy containers would otherwise restart a perfectly good frontend, uselessly).
# Whether the whole chain can serve is /api/ready, which a deployment gate may ask separately.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
