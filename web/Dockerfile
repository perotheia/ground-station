# gs-web — build the React SPA, serve it from nginx (+ proxy /api → gs-api).
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /web/dist /usr/share/nginx/html
# Templated config — the nginx:alpine entrypoint runs envsubst on
# /etc/nginx/templates/*.template at start, substituting ${GS_API_KEY} (the
# operator key injected on the /api proxy). nginx's own $host/$uri are left alone
# (envsubst only replaces vars present in the environment). Drop the static copy.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
