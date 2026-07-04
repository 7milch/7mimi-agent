package main

import (
	"log"
	"net/http"
	"os"

	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/audit"
	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/config"
	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/proxy"
)

func main() {
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
