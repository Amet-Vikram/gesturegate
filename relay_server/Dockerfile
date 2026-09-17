# ---- Build stage ----
# Only this stage needs the Go toolchain; it's discarded entirely from the
# final image, which is the whole point of a multi-stage build.
FROM golang:1.22-alpine AS builder

WORKDIR /build

# Copy go.mod/go.sum and download deps BEFORE copying source. Docker caches
# each layer by its inputs -- as long as go.mod/go.sum don't change, this
# layer is reused even when application code does, so rebuilds after a code
# change skip re-downloading gorilla/websocket every time.
COPY go.mod go.sum ./
RUN go mod download

COPY *.go ./

# CGO_ENABLED=0 produces a fully static binary (no libc dependency) -- this
# is what makes running it on a minimal base image like alpine possible at
# all. Verified: `file` on the resulting binary confirms "statically linked".
RUN CGO_ENABLED=0 GOOS=linux go build -o relay-server .

# ---- Runtime stage ----
FROM alpine:3.20

# wget is here specifically so HEALTHCHECK below has something to call --
# alpine doesn't include it by default. It also means `docker exec -it
# <container> sh` gives you a real shell if something needs debugging live
# on a LAN deployment, unlike a `scratch` base.
RUN apk add --no-cache wget

# Non-root user -- standard hardening, costs nothing here.
RUN addgroup -S relay && adduser -S relay -G relay

WORKDIR /app
COPY --from=builder /build/relay-server .

# devices.json is DELIBERATELY NOT copied into the image -- it's your token
# store. Baking it in would mean real secrets live in an image layer that
# anyone with the image can read. Mount your real one as a volume at
# runtime (see docker-compose.yml). The app already fails loudly with a
# clear error if it's missing, rather than silently running with none.

RUN chown -R relay:relay /app
USER relay

EXPOSE 8080

# Matches main.go's graceful shutdown: SIGTERM triggers srv.Shutdown() +
# hub.shutdown, which together take up to ~10s in the worst case. Docker's
# default stop_grace_period before SIGKILL is 10s -- see docker-compose.yml,
# which extends it to 15s for margin.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/healthz || exit 1

ENTRYPOINT ["./relay-server"]