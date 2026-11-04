# 필기봇 OCR Studio — frontend (Next.js standalone server)
ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
COPY scripts ./scripts
RUN npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
# Rewrites (/api -> backend) are resolved at build time in standalone mode.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=${BACKEND_URL} NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
