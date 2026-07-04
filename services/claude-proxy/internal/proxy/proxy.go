package proxy

import (
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/audit"
	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/config"
)

const defaultAnthropicVersion = "2023-06-01"

// Handler proxies POST /v1/messages to the Anthropic API, injecting the
// provider credential. agent-runner only ever sees a session token.
type Handler struct {
	cfg    *config.Config
	client *http.Client
	logger *audit.Logger
}

func NewHandler(cfg *config.Config, logger *audit.Logger) *Handler {
	return &Handler{
		cfg:    cfg,
		client: &http.Client{Timeout: 10 * time.Minute},
		logger: logger,
	}
}

func (h *Handler) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.handleHealthz)
	mux.HandleFunc("POST /v1/messages", h.handleMessages)
	return mux
}

func (h *Handler) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"status":"ok"}`))
}

func (h *Handler) handleMessages(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	sessionID := r.Header.Get("X-7mimi-Session-Id")
	role := r.Header.Get("X-7mimi-Role")

	deny := func(status int, reason string) {
		h.logger.Log(audit.Event{
			SessionID: sessionID, Role: role,
			Method: r.Method, Path: r.URL.Path,
			UpstreamStatus: 0, DurationMS: time.Since(start).Milliseconds(),
			Decision: "block", Reason: reason,
		})
		http.Error(w, reason, status)
	}

	token, ok := bearerToken(r.Header.Get("Authorization"))
	if !ok {
		deny(http.StatusUnauthorized, "missing or malformed Authorization bearer token")
		return
	}
	if token != h.cfg.DevSessionToken {
		deny(http.StatusUnauthorized, "invalid session token")
		return
	}
	if sessionID == "" {
		deny(http.StatusBadRequest, "missing X-7mimi-Session-Id")
		return
	}
	if role == "" {
		deny(http.StatusBadRequest, "missing X-7mimi-Role")
		return
	}

	upstreamURL := strings.TrimRight(h.cfg.AnthropicBaseURL, "/") + "/v1/messages"
	upstreamReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, upstreamURL, r.Body)
	if err != nil {
		deny(http.StatusInternalServerError, "failed to build upstream request")
		return
	}
	copyProxyHeaders(upstreamReq.Header, r.Header)
	// Credential injection happens only here; the caller-provided session
	// token and 7mimi headers are stripped from the upstream request.
	upstreamReq.Header.Set("x-api-key", h.cfg.AnthropicAPIKey)
	if upstreamReq.Header.Get("anthropic-version") == "" {
		upstreamReq.Header.Set("anthropic-version", defaultAnthropicVersion)
	}

	resp, err := h.client.Do(upstreamReq)
	if err != nil {
		deny(http.StatusBadGateway, "upstream request failed")
		return
	}
	defer resp.Body.Close()

	for key, values := range resp.Header {
		for _, v := range values {
			w.Header().Add(key, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	streamCopy(w, resp.Body)

	h.logger.Log(audit.Event{
		SessionID: sessionID, Role: role,
		Method: r.Method, Path: r.URL.Path,
		UpstreamStatus: resp.StatusCode, DurationMS: time.Since(start).Milliseconds(),
		Decision: "allow",
	})
}

func bearerToken(header string) (string, bool) {
	const prefix = "Bearer "
	if !strings.HasPrefix(header, prefix) {
		return "", false
	}
	token := strings.TrimSpace(strings.TrimPrefix(header, prefix))
	return token, token != ""
}

// copyProxyHeaders forwards content/accept/anthropic-* headers only.
// Authorization (session token) and X-7mimi-* attribution headers must not
// leak to the provider.
func copyProxyHeaders(dst, src http.Header) {
	for key, values := range src {
		lower := strings.ToLower(key)
		switch {
		case lower == "content-type", lower == "accept", lower == "accept-encoding":
		case strings.HasPrefix(lower, "anthropic-"):
		default:
			continue
		}
		for _, v := range values {
			dst.Add(key, v)
		}
	}
}

// streamCopy copies the upstream body flushing after each chunk so SSE
// streaming responses reach the caller incrementally.
func streamCopy(w http.ResponseWriter, body io.Reader) {
	flusher, _ := w.(http.Flusher)
	buf := make([]byte, 32*1024)
	for {
		n, err := body.Read(buf)
		if n > 0 {
			if _, werr := w.Write(buf[:n]); werr != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
		if err != nil {
			return
		}
	}
}
