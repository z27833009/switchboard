# Build stage: npm ci uses the committed package-lock.json, so the image and CI
# install the same dependency tree. Node 26 is the current LTS line.
FROM node:26-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

# Serve stage: nginx serves the static bundle and proxies /api to the api
# service (web/nginx.conf).
FROM nginx:1.31.8-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
