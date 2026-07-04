# claude-proxy: Claude API credential boundary (Go service).
# Build context: services/claude-proxy
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -o /out/claude-proxy ./cmd/claude-proxy

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/claude-proxy /claude-proxy
EXPOSE 18080
ENTRYPOINT ["/claude-proxy"]
