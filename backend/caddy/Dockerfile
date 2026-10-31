# Rebuild with the builder's newer Go and patched deps; the stock caddy binary lags on Go CVEs
FROM caddy:2-builder-alpine AS builder
RUN xcaddy build \
    --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
    --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6

FROM caddy:2-alpine AS runtime

# for health check
RUN apk add --no-cache -u ca-certificates curl

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
