# Build the TypeScript MCP server once, then run it from a small runtime image.
# The manifest ships with the code; the tenant file is mounted read-only by
# compose so both MCP servers read the same allowlists (design.md section 11).
FROM node:24-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json tsconfig.build.json manifest.yaml ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-alpine

WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/manifest.yaml ./manifest.yaml

EXPOSE 8002
USER node
CMD ["node", "dist/src/index.js"]
