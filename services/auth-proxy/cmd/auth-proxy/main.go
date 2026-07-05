package main

import (
	"log"
	"net/http"
	"os"

	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/audit"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/githubapp"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/gitrelay"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/policy"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/tools"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/xmcp"
)

func main() {
	addr := os.Getenv("AUTH_PROXY_ADDR")
	if addr == "" {
		addr = ":18081"
	}
	logger := audit.NewLogger(os.Stdout)
	handler := tools.NewHandler(policy.NewDevEngine(), logger)

	mux := http.NewServeMux()
	mux.Handle("/", handler.Routes())
	mountGitRelay(mux, logger)
	mountXMCP(mux, logger)

	log.Printf("auth-proxy listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatalf("auth-proxy: %v", err)
	}
}

// mountGitRelay wires the git Smart HTTP relay (ADR-020) only when a session
// token is configured and GitHub App credentials are available; otherwise it
// logs a non-sensitive reason and leaves the tools routes serving alone.
func mountGitRelay(mux *http.ServeMux, logger *audit.Logger) {
	sessionToken := os.Getenv("AUTH_PROXY_SESSION_TOKEN")
	if sessionToken == "" {
		log.Printf("git relay disabled: no session token configured")
		return
	}

	tokens, err := githubapp.NewTokenSourceFromEnv()
	if err != nil {
		log.Printf("git relay disabled: github app credentials unavailable")
		return
	}

	upstream := os.Getenv("GIT_RELAY_UPSTREAM")
	relay, err := gitrelay.NewHandler(sessionToken, tokens, upstream, logger)
	if err != nil {
		log.Printf("git relay disabled: handler construction failed")
		return
	}

	mux.Handle("/git/", relay.Routes())
}

// mountXMCP mounts the x-mcp-readonly MCP endpoint (ADR-023) only when both
// X_BEARER_TOKEN (the X API credential) and AUTH_PROXY_SESSION_TOKEN (the
// same session Bearer that protects gitrelay) are configured; otherwise it
// logs which one is missing and leaves /mcp unmounted.
func mountXMCP(mux *http.ServeMux, logger *audit.Logger) {
	xBearerToken := os.Getenv("X_BEARER_TOKEN")
	sessionToken := os.Getenv("AUTH_PROXY_SESSION_TOKEN")
	if xBearerToken == "" {
		log.Printf("x-mcp disabled: X_BEARER_TOKEN not set")
		return
	}
	if sessionToken == "" {
		log.Printf("x-mcp disabled: AUTH_PROXY_SESSION_TOKEN not set")
		return
	}

	handler, err := xmcp.NewHandler(sessionToken, logger)
	if err != nil {
		log.Printf("x-mcp disabled: handler construction failed")
		return
	}
	mux.Handle("/mcp", handler.Routes())
}
