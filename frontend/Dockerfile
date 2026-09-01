# 前端镜像：开发期 dev target（Vite HMR），生产期 build -> nginx
FROM node:20-slim AS dev
WORKDIR /app
# npm 换淘宝镜像（加速依赖安装）
RUN npm config set registry https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN npm install
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------- 生产构建 ----------
FROM dev AS build
COPY . .
RUN npm run build

FROM nginx:alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
