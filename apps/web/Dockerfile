# GradeMIND web (Next.js standalone). Build context: apps/web. The host has Node 18, so all JS tooling runs here.
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 NEXT_TELEMETRY_DISABLED=1
WORKDIR /w
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
# type check, lint and the unit tests (node --test) are part of the image build: a failing check fails the build
RUN pnpm exec tsc --noEmit && pnpm exec eslint --max-warnings 0 . && pnpm run test:unit && pnpm build

FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /w/.next/standalone ./
COPY --from=build --chown=node:node /w/.next/static ./.next/static
COPY --from=build --chown=node:node /w/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/api/health > /dev/null || exit 1
CMD ["node", "server.js"]
