package config

import (
	"fmt"
	"os"
)

// Config holds claude-proxy runtime configuration.
// ANTHROPIC_API_KEY is the provider credential boundary: it lives here and
// must never be forwarded to agent-runner or written to logs.
type Config struct {
	Addr             string
	AnthropicAPIKey  string
	AnthropicBaseURL string
	DevSessionToken  string
}

func FromEnv() (*Config, error) {
	cfg := &Config{
		Addr:             envOr("CLAUDE_PROXY_ADDR", ":18080"),
		AnthropicAPIKey:  os.Getenv("ANTHROPIC_API_KEY"),
		AnthropicBaseURL: envOr("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
		DevSessionToken:  envOr("CLAUDE_PROXY_DEV_TOKEN", "cp_sess_dev"),
	}
	if cfg.AnthropicAPIKey == "" {
		return nil, fmt.Errorf("ANTHROPIC_API_KEY is required")
	}
	return cfg, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
