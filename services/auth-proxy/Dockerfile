# auth-proxy: external tool/API credential boundary (Go service).
# Build context: services/auth-proxy
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -o /out/auth-proxy ./cmd/auth-proxy

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/auth-proxy /auth-proxy
EXPOSE 18081
ENTRYPOINT ["/auth-proxy"]
