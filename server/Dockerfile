# --- build: compile the single static binary (CGo-free pure-Go SQLite) ---
FROM golang:1.23-alpine AS build
WORKDIR /src
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
COPY . .
# go.sum is generated here so the image is self-contained even from a fresh
# checkout without a local Go toolchain.
RUN go mod tidy && go build -ldflags="-s -w" -o /out/cleanyfin-api ./cmd/api

# --- runtime: tiny image, non-root, data on a mounted volume ---
FROM alpine:latest
RUN adduser -D -u 10001 app && mkdir -p /data && chown app:app /data
COPY --from=build /out/cleanyfin-api /usr/local/bin/cleanyfin-api
USER app
ENV CLEANYFIN_ADDR=":8080" CLEANYFIN_DB="/data/cleanyfin.db"
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
ENTRYPOINT ["cleanyfin-api"]
