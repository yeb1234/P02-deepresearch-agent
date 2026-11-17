# ---- 构建阶段 ----
FROM docker.m.daocloud.io/library/node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY . .
RUN npm run build

# ---- 运行阶段 ----
FROM docker.m.daocloud.io/library/nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/app

EXPOSE 80
