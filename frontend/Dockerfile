# Frontend image. The build context is frontend/ alone:  docker build -t ctviz-frontend frontend/
# The build reads nothing outside that folder, takes no secret and needs no build argument. The only
# setting, BACKEND_URL, is read by the proxy route when a request arrives, so one image fits any backend.

# Node 24 is the current LTS line; 26.10.0 also builds and runs (checked with --build-arg).
ARG NODE_VERSION=24.21.0

FROM node:${NODE_VERSION}-trixie-slim AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# ---- build: every dependency, then "next build" with output: "standalone" ------------------------
FROM base AS build
# pnpm-workspace.yaml holds pnpm's build-script policy (allowBuilds). Without it the install stops
# with ERR_PNPM_IGNORED_BUILDS.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# pnpm at the version package.json pins, installed with npm: the Node 26 image has no corepack.
RUN npm install --global --allow-scripts=pnpm "$(node -p 'require("./package.json").packageManager')" \
 && pnpm install --frozen-lockfile
COPY . .
# "next build", not "pnpm build": the package script first generates test fixtures from ../docs,
# which is outside this context. .dockerignore leaves the tests out, so nothing needs them here.
RUN mkdir -p public && pnpm exec next build

# ---- run: server.js with its traced node_modules, the static assets and public/ ------------------
FROM base AS run
# Docker sets HOSTNAME to the container id and server.js binds to $HOSTNAME, so it must be overridden.
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
# The one directory the server writes to (Next's image cache). Everything else stays owned by root.
# With a read-only root filesystem, mount a tmpfs here with uid=1000,gid=1000.
RUN mkdir .next/cache && chown node:node .next/cache
USER node
EXPOSE 3000
# Needs no curl: the page is static, so this checks the server without touching the backend.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --start-interval=1s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:' + process.env.PORT).then(r => process.exit(r.ok ? 0 : 1))"]
# Exec form: node is PID 1 and handles SIGTERM itself, so "docker stop" ends an idle server at once.
CMD ["node", "server.js"]
