package main

import (
	"log"
	"net/http"
	"os"

	"github.com/nishiog/7mimi-agent/services/auth-proxy/internal/audit"
	"github.com/nishiog/7mimi-agent/services/auth-proxy/internal/policy"
	"github.com/nishiog/7mimi-agent/services/auth-proxy/internal/tools"
)

func main() {
	addr := os.Getenv("AUTH_PROXY_ADDR")
	if addr == "" {
		addr = ":18081"
	}
	handler := tools.NewHandler(policy.NewDevEngine(), audit.NewLogger(os.Stdout))
	log.Printf("auth-proxy listening on %s", addr)
	if err := http.ListenAndServe(addr, handler.Routes()); err != nil {
		log.Fatalf("auth-proxy: %v", err)
	}
}
