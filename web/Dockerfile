# gs-web — build the React SPA, serve it from nginx (+ proxy /api → gs-api).
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
WORKDIR /web
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY --from=build /web/dist /usr/share/nginx/html
# Templated config — the nginx:alpine entrypoint runs envsubst on
# /etc/nginx/templates/*.template at start, substituting ${GS_API_KEY} (the
# operator key injected on the /api proxy). nginx's own $host/$uri are left alone
# (envsubst only replaces vars present in the environment). Drop the static copy.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
