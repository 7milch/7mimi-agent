package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/audit"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/session"
)

func generateTestPEMKey(t *testing.T) []byte {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generating test key: %v", err)
	}
	der := x509.MarshalPKCS1PrivateKey(key)
	return pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: der})
}

func writeTempKeyFile(t *testing.T, pemBytes []byte) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "app-key.pem")
	if err := os.WriteFile(path, pemBytes, 0o600); err != nil {
		t.Fatalf("writing temp key file: %v", err)
	}
	return path
}

// TestMountGitRelayDisabledWithoutSessionToken verifies the relay is not
// mounted (mux has no /git/ handler) when AUTH_PROXY_SESSION_TOKEN is unset,
// matching the ADR-020 requirement that the relay is opt-in.
func TestMountGitRelayDisabledWithoutSessionToken(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "")
	t.Setenv("GITHUB_APP_ID", "")
	t.Setenv("GITHUB_APP_PRIVATE_KEY_PATH", "")

	mux := http.NewServeMux()
	mountGitRelay(mux, audit.NewLogger(io.Discard), session.NewStore())

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/git/owner/repo/info/refs?service=git-upload-pack", nil)
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404 (relay must not be mounted without a session token)", rec.Code)
	}
}

// TestMountGitRelayDisabledWithoutGitHubAppCreds verifies the relay is not
// mounted when the session token is set but GitHub App credentials are
// missing (TokenSource construction fails).
func TestMountGitRelayDisabledWithoutGitHubAppCreds(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "some-session-token")
	t.Setenv("GITHUB_APP_ID", "")
	t.Setenv("GITHUB_APP_PRIVATE_KEY_PATH", "")

	mux := http.NewServeMux()
	mountGitRelay(mux, audit.NewLogger(io.Discard), session.NewStore())

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/git/owner/repo/info/refs?service=git-upload-pack", nil)
	req.Header.Set("Authorization", "Bearer some-session-token")
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404 (relay must not be mounted without GitHub App credentials)", rec.Code)
	}
}

// TestMountGitRelayEnabledWithSessionTokenAndCreds verifies the relay is
// mounted (routes respond, even if unauthorized) once both a session token
// and GitHub App credentials are present.
func TestMountGitRelayEnabledWithSessionTokenAndCreds(t *testing.T) {
	key := generateTestPEMKey(t)
	keyPath := writeTempKeyFile(t, key)

	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "some-session-token")
	t.Setenv("GITHUB_APP_ID", "12345")
	t.Setenv("GITHUB_APP_INSTALLATION_ID", "999")
	t.Setenv("GITHUB_APP_PRIVATE_KEY_PATH", keyPath)

	mux := http.NewServeMux()
	mountGitRelay(mux, audit.NewLogger(io.Discard), session.NewStore())

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/git/owner/repo/info/refs?service=git-upload-pack", nil)
	// No Authorization header: relay must be mounted and respond 401 (not 404).
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401 (relay should be mounted and reachable, but reject missing auth)", rec.Code)
	}
}

// toolNamesFromMCP calls tools/list against the mounted /mcp handler and
// returns the tool names present, using the given session token.
func toolNamesFromMCP(t *testing.T, mux *http.ServeMux, sessionToken string) []string {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}`))
	req.Header.Set("Authorization", "Bearer "+sessionToken)
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("tools/list status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode tools/list response: %v", err)
	}
	names := make([]string, 0, len(resp.Result.Tools))
	for _, tool := range resp.Result.Tools {
		names = append(names, tool.Name)
	}
	return names
}

func containsName(names []string, want string) bool {
	for _, n := range names {
		if n == want {
			return true
		}
	}
	return false
}

func TestMountXMCPDisabledWithoutAnyCredential(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "sess-tok")
	t.Setenv("X_BEARER_TOKEN", "")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "")

	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), session.NewStore())

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{}`))
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404 (mcp must not be mounted without any credential)", rec.Code)
	}
}

func TestMountXMCPXOnly(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "sess-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "")

	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), session.NewStore())

	names := toolNamesFromMCP(t, mux, "sess-tok")
	if !containsName(names, "x.search_posts_recent") {
		t.Errorf("expected x tools present, got %v", names)
	}
	if containsName(names, "jq.get_listed_info") {
		t.Errorf("did not expect jq tools present, got %v", names)
	}
}

func TestMountXMCPJQuantsOnly(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "sess-tok")
	t.Setenv("X_BEARER_TOKEN", "")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")

	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), session.NewStore())

	names := toolNamesFromMCP(t, mux, "sess-tok")
	if containsName(names, "x.search_posts_recent") {
		t.Errorf("did not expect x tools present, got %v", names)
	}
	for _, want := range []string{"jq.get_listed_info", "jq.get_daily_quotes", "jq.get_statements"} {
		if !containsName(names, want) {
			t.Errorf("expected %s present, got %v", want, names)
		}
	}
}

func TestMountXMCPBothConfigured(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "sess-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")

	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), session.NewStore())

	names := toolNamesFromMCP(t, mux, "sess-tok")
	if !containsName(names, "x.search_posts_recent") {
		t.Errorf("expected x tools present, got %v", names)
	}
	if !containsName(names, "jq.get_listed_info") {
		t.Errorf("expected jq tools present, got %v", names)
	}
}

// --- ADR-028: /session/issue + role-scoped /mcp + /git enforcement ---

func TestSessionIssueRequiresStaticBearer(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountSessionIssue(mux, store)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/session/issue", strings.NewReader(`{"role":"ai_it_topic_runner"}`))
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401 without bearer", rec.Code)
	}

	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/session/issue", strings.NewReader(`{"role":"ai_it_topic_runner"}`))
	req.Header.Set("Authorization", "Bearer wrong-tok")
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401 with wrong bearer", rec.Code)
	}
}

func TestSessionIssueMintsToken(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountSessionIssue(mux, store)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/session/issue", strings.NewReader(`{"role":"ai_it_topic_runner"}`))
	req.Header.Set("Authorization", "Bearer static-tok")
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var resp sessionIssueResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Token == "" {
		t.Fatal("expected non-empty token")
	}
	if resp.TTLSeconds <= 0 {
		t.Fatalf("expected positive ttl_seconds, got %d", resp.TTLSeconds)
	}
	role, ok := store.Resolve(resp.Token)
	if !ok || role != "ai_it_topic_runner" {
		t.Fatalf("expected minted token to resolve to ai_it_topic_runner, got role=%q ok=%v", role, ok)
	}
}

func TestMCPSessionTokenRoleFilteredToolsList(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), store)

	token, _ := store.Issue("ai_it_topic_runner")
	names := toolNamesFromMCP(t, mux, token)
	if !containsName(names, "x.search_posts_recent") {
		t.Errorf("expected x tools visible to ai_it_topic_runner, got %v", names)
	}
	if containsName(names, "jq.get_listed_info") {
		t.Errorf("expected jq tools filtered out for ai_it_topic_runner, got %v", names)
	}
}

func TestMCPSessionTokenDeniedToolCallBlocked(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), store)

	token, _ := store.Issue("ai_it_topic_runner")
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"jq.get_listed_info","arguments":{}}}`))
	req.Header.Set("Authorization", "Bearer "+token)
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Error *struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Error == nil {
		t.Fatal("expected jsonrpc error for denied tool")
	}
}

func TestMCPSessionTokenAllowedToolCallPasses(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("X_BEARER_TOKEN", "")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), store)

	token, _ := store.Issue("stock_researcher")
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"jq.get_listed_info","arguments":{}}}`))
	req.Header.Set("Authorization", "Bearer "+token)
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Error *struct{} `json:"error"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("expected no jsonrpc error for allowed tool, got %+v", resp.Error)
	}
}

func TestMCPSessionTokenCallCapExceeded(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "")
	store := session.NewStore(session.WithCallCap(1))
	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), store)

	token, _ := store.Issue("ai_it_topic_runner")
	body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x.search_posts_recent","arguments":{"query":"foo"}}}`

	for i := 0; i < 2; i++ {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		mux.ServeHTTP(rec, req)
		var resp struct {
			Error *struct{} `json:"error"`
		}
		_ = json.NewDecoder(rec.Body).Decode(&resp)
		if i == 0 && resp.Error != nil {
			t.Fatalf("expected first call within cap to pass, got error %+v", resp.Error)
		}
		if i == 1 && resp.Error == nil {
			t.Fatal("expected second call to exceed cap and error")
		}
	}
}

func TestMCPStaticTokenStillFullAccessWithSession(t *testing.T) {
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("X_BEARER_TOKEN", "x-tok")
	t.Setenv("JQUANTS_REFRESH_TOKEN", "jq-tok")
	store := session.NewStore()
	mux := http.NewServeMux()
	mountXMCP(mux, audit.NewLogger(io.Discard), store)

	names := toolNamesFromMCP(t, mux, "static-tok")
	if !containsName(names, "x.search_posts_recent") || !containsName(names, "jq.get_listed_info") {
		t.Errorf("expected static token to see full tool list, got %v", names)
	}
}

func TestGitRelayAcceptsMintedSessionToken(t *testing.T) {
	key := generateTestPEMKey(t)
	keyPath := writeTempKeyFile(t, key)
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("GITHUB_APP_ID", "12345")
	t.Setenv("GITHUB_APP_INSTALLATION_ID", "999")
	t.Setenv("GITHUB_APP_PRIVATE_KEY_PATH", keyPath)

	store := session.NewStore()
	mux := http.NewServeMux()
	mountGitRelay(mux, audit.NewLogger(io.Discard), store)

	token, _ := store.Issue("ai_it_topic_runner")
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/git/owner/repo/info/refs?service=git-upload-pack", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	mux.ServeHTTP(rec, req)
	// Reaches the proxy stage (may fail upstream in test env, but must not
	// be rejected as unauthorized).
	if rec.Code == http.StatusUnauthorized {
		t.Fatalf("expected minted session token to be accepted by git relay, got 401")
	}
}

func TestGitRelayRejectsUnknownOrExpiredToken(t *testing.T) {
	key := generateTestPEMKey(t)
	keyPath := writeTempKeyFile(t, key)
	t.Setenv("AUTH_PROXY_SESSION_TOKEN", "static-tok")
	t.Setenv("GITHUB_APP_ID", "12345")
	t.Setenv("GITHUB_APP_INSTALLATION_ID", "999")
	t.Setenv("GITHUB_APP_PRIVATE_KEY_PATH", keyPath)

	store := session.NewStore()
	mux := http.NewServeMux()
	mountGitRelay(mux, audit.NewLogger(io.Discard), store)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/git/owner/repo/info/refs?service=git-upload-pack", nil)
	req.Header.Set("Authorization", "Bearer unknown-token")
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401 for unknown token", rec.Code)
	}
}
