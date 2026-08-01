# syntax=docker/dockerfile:1
# pnpm 11은 Node >= 22.13을 요구하고, Vite 8은 >= 22.12를 요구한다.
FROM node:24-alpine AS build

WORKDIR /app
RUN corepack enable

# lockfile을 먼저 복사해 의존성 레이어를 소스와 분리한다.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build


FROM nginx:alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
