package proxy

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/audit"
	"github.com/nishiog/7mimi-agent/services/claude-proxy/internal/config"
)

const testAPIKey = "sk-ant-test-secret-key"

func newTestHandler(baseURL string, auditOut io.Writer) *Handler {
	cfg := &config.Config{
		Addr:             ":0",
		AnthropicAPIKey:  testAPIKey,
		AnthropicBaseURL: baseURL,
		DevSessionToken:  "cp_sess_dev",
	}
	return NewHandler(cfg, audit.NewLogger(auditOut))
}

func TestHealthz(t *testing.T) {
	h := newTestHandler("http://unused.invalid", io.Discard)
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("healthz status = %d, want 200", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "ok") {
		t.Fatalf("healthz body = %q, want ok", rec.Body.String())
	}
}

func TestMissingAuthorizationBlocks(t *testing.T) {
	h := newTestHandler("http://unused.invalid", io.Discard)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader("{}"))
	req.Header.Set("X-7mimi-Session-Id", "sess_dev")
	req.Header.Set("X-7mimi-Role", "ai_it_topic_runner")
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestMissingSessionIDBlocks(t *testing.T) {
	h := newTestHandler("http://unused.invalid", io.Discard)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader("{}"))
	req.Header.Set("Authorization", "Bearer cp_sess_dev")
	req.Header.Set("X-7mimi-Role", "ai_it_topic_runner")
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
}

func TestForwardsWithAPIKeyInjectedAndAuditHasNoSecret(t *testing.T) {
	var upstreamReq *http.Request
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamReq = r.Clone(r.Context())
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"msg_test"}`))
	}))
	defer upstream.Close()

	var auditBuf bytes.Buffer
	h := newTestHandler(upstream.URL, &auditBuf)

	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(`{"model":"claude-fable-5"}`))
	req.Header.Set("Authorization", "Bearer cp_sess_dev")
	req.Header.Set("X-7mimi-Session-Id", "sess_dev")
	req.Header.Set("X-7mimi-Role", "ai_it_topic_runner")
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	if upstreamReq == nil {
		t.Fatal("upstream was not called")
	}
	if got := upstreamReq.Header.Get("x-api-key"); got != testAPIKey {
		t.Fatalf("x-api-key = %q, want injected key", got)
	}
	if upstreamReq.Header.Get("anthropic-version") == "" {
		t.Fatal("anthropic-version header missing on upstream request")
	}
	if upstreamReq.Header.Get("Authorization") != "" {
		t.Fatal("session token leaked to upstream")
	}
	if upstreamReq.Header.Get("X-7mimi-Session-Id") != "" {
		t.Fatal("X-7mimi-Session-Id leaked to upstream")
	}
	if !strings.Contains(rec.Body.String(), "msg_test") {
		t.Fatalf("response body = %q, want upstream body", rec.Body.String())
	}

	auditLine := auditBuf.String()
	if auditLine == "" {
		t.Fatal("no audit log written")
	}
	if strings.Contains(auditLine, testAPIKey) {
		t.Fatal("audit log contains ANTHROPIC_API_KEY")
	}
	if !strings.Contains(auditLine, `"session_id":"sess_dev"`) {
		t.Fatalf("audit log missing session attribution: %s", auditLine)
	}
	if strings.Contains(auditLine, "claude-fable-5") {
		t.Fatal("audit log contains request body content")
	}
}
