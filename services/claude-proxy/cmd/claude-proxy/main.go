package main

import (
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/7milch/7mimi-agent/services/claude-proxy/internal/audit"
	"github.com/7milch/7mimi-agent/services/claude-proxy/internal/config"
	"github.com/7milch/7mimi-agent/services/claude-proxy/internal/proxy"
)

// runHealthcheck is invoked as `claude-proxy -healthcheck` from a Docker
// HEALTHCHECK CMD. The distroless nonroot base image ships no shell, curl, or
// wget, so the binary performs the self-GET /healthz itself and exits
// non-zero on any failure.
func runHealthcheck() {
	addr := os.Getenv("CLAUDE_PROXY_ADDR")
	if addr == "" {
		addr = ":18080"
	}
	// CLAUDE_PROXY_ADDR may be host:port (e.g. "0.0.0.0:18080" so the
	// process listens on all interfaces per ADR-024); the self-check always
	// targets 127.0.0.1, so keep only the ":port" suffix.
	if idx := strings.LastIndex(addr, ":"); idx >= 0 {
		addr = addr[idx:]
	}
	client := http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1" + addr + "/healthz")
	if err != nil {
		os.Exit(1)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	os.Exit(0)
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "-healthcheck" {
		runHealthcheck()
		return
	}
	cfg, err := config.FromEnv()
	if err != nil {
		log.Fatalf("claude-proxy: %v", err)
	}
	handler := proxy.NewHandler(cfg, audit.NewLogger(os.Stdout))
	log.Printf("claude-proxy listening on %s (upstream %s)", cfg.Addr, cfg.AnthropicBaseURL)
	if err := http.ListenAndServe(cfg.Addr, handler.Routes()); err != nil {
		log.Fatalf("claude-proxy: %v", err)
	}
}
