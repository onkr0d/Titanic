FROM caddy:2-alpine

# for health check
RUN apk add --no-cache -u ca-certificates curl

COPY Caddyfile /etc/caddy/Caddyfile 