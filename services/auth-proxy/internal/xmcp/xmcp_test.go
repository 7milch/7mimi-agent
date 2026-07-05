package xmcp

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const testSessionToken = "test-session-token-abc123"

func newTestHandler(t *testing.T) *Handler {
	t.Helper()
	h, err := NewHandler(testSessionToken, nil)
	if err != nil {
		t.Fatalf("NewHandler failed: %v", err)
	}
	return h
}

func postJSON(t *testing.T, h *Handler, body string) *httptest.ResponseRecorder {
	t.Helper()
	return postJSONWithBearer(t, h, testSessionToken, body)
}

func postJSONWithBearer(t *testing.T, h *Handler, bearer string, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(body))
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)
	return rec
}

func decodeResponse(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	if rec.Body.Len() == 0 {
		return nil
	}
	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v, body=%s", err, rec.Body.String())
	}
	return resp
}

func TestInitialize(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	resp := decodeResponse(t, rec)
	result, ok := resp["result"].(map[string]any)
	if !ok {
		t.Fatalf("no result in response: %v", resp)
	}
	if result["protocolVersion"] != "2025-03-26" {
		t.Errorf("protocolVersion = %v", result["protocolVersion"])
	}
	serverInfo, ok := result["serverInfo"].(map[string]any)
	if !ok {
		t.Fatalf("no serverInfo: %v", result)
	}
	if serverInfo["name"] != "x-mcp-readonly" {
		t.Errorf("serverInfo.name = %v", serverInfo["name"])
	}
	if serverInfo["version"] != "0.2.0" {
		t.Errorf("serverInfo.version = %v", serverInfo["version"])
	}
}

func TestNotificationsInitialized(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","method":"notifications/initialized"}`)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	if rec.Body.Len() != 0 {
		t.Errorf("expected empty body, got %q", rec.Body.String())
	}
}

func TestToolsListExactFour(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`)
	resp := decodeResponse(t, rec)
	result := resp["result"].(map[string]any)
	toolsList, ok := result["tools"].([]any)
	if !ok {
		t.Fatalf("no tools list: %v", result)
	}
	if len(toolsList) != 4 {
		t.Fatalf("len(tools) = %d, want 4", len(toolsList))
	}
	wantNames := map[string]bool{
		"x.search_posts_recent":   true,
		"x.get_posts":             true,
		"x.get_users":             true,
		"x.get_users_by_username": true,
	}
	for _, raw := range toolsList {
		tool := raw.(map[string]any)
		name, _ := tool["name"].(string)
		if !wantNames[name] {
			t.Errorf("unexpected tool name: %s", name)
		}
		delete(wantNames, name)
		if _, ok := tool["inputSchema"]; !ok {
			t.Errorf("tool %s missing inputSchema", name)
		}
	}
	if len(wantNames) != 0 {
		t.Errorf("missing tools: %v", wantNames)
	}
}

func TestUnknownMethod(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":3,"method":"bogus"}`)
	resp := decodeResponse(t, rec)
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatalf("expected error, got %v", resp)
	}
	if code, _ := errObj["code"].(float64); int(code) != -32601 {
		t.Errorf("code = %v, want -32601", errObj["code"])
	}
}

func TestParseError(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{not json`)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	resp := decodeResponse(t, rec)
	if resp["id"] != nil {
		t.Errorf("id = %v, want nil", resp["id"])
	}
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatalf("expected error, got %v", resp)
	}
	if code, _ := errObj["code"].(float64); int(code) != -32700 {
		t.Errorf("code = %v, want -32700", errObj["code"])
	}
}

func TestToolsCallWriteToolRejected(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"x.post_tweet","arguments":{}}}`)
	resp := decodeResponse(t, rec)
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatalf("expected error, got %v", resp)
	}
	if code, _ := errObj["code"].(float64); int(code) != -32602 {
		t.Errorf("code = %v, want -32602", errObj["code"])
	}
}

// TestToolsCallCreatePostRejected exercises the real X write tool name
// (x.create_post) rather than a placeholder, since x-mcp-readonly's tool
// registry (var tools) never lists any write tool: any such name is
// "unregistered" and rejected with -32602, the same path as an arbitrary
// unknown tool name.
func TestToolsCallCreatePostRejected(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"x.create_post","arguments":{"text":"hello"}}}`)
	resp := decodeResponse(t, rec)
	errObj, ok := resp["error"].(map[string]any)
	if !ok {
		t.Fatalf("expected error, got %v", resp)
	}
	if code, _ := errObj["code"].(float64); int(code) != -32602 {
		t.Errorf("code = %v, want -32602", errObj["code"])
	}
}

func TestNewHandlerRequiresNonEmptySessionToken(t *testing.T) {
	if _, err := NewHandler("", nil); err == nil {
		t.Fatal("expected error for empty session token, got nil")
	}
}

func TestPostMissingBearerUnauthorized(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSONWithBearer(t, h, "", `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestPostWrongBearerUnauthorized(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSONWithBearer(t, h, "wrong-token", `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestNotificationsInitializedMissingBearerUnauthorized(t *testing.T) {
	h := newTestHandler(t)
	rec := postJSONWithBearer(t, h, "", `{"jsonrpc":"2.0","method":"notifications/initialized"}`)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestGetOtherVerbMethodNotAllowed(t *testing.T) {
	h := newTestHandler(t)
	req := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rec.Code)
	}
}

func TestMissingTokenIsError(t *testing.T) {
	t.Setenv("X_BEARER_TOKEN", "")
	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"x.get_users","arguments":{"ids":["1"]}}}`)
	resp := decodeResponse(t, rec)
	result := resp["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("expected isError true, got %v", result)
	}
	content := result["content"].([]any)[0].(map[string]any)
	text, _ := content["text"].(string)
	if text != "X_BEARER_TOKEN is not configured" {
		t.Errorf("text = %q", text)
	}
	if strings.Contains(rec.Body.String(), "Bearer") {
		t.Errorf("response leaked token marker: %s", rec.Body.String())
	}
}

func TestSearchPostsRecentHappyPathWithRedaction(t *testing.T) {
	fakeToken := "test-bearer-fake-1234567890"
	var gotAuthHeader string
	fakeX := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuthHeader = r.Header.Get("Authorization")
		if r.URL.Path != "/2/tweets/search/recent" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": [
				{
					"id": "111",
					"author_id": "u1",
					"created_at": "2026-07-05T00:00:00.000Z",
					"text": "leaked api_key=sk-should-be-redacted rest of text",
					"public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0, "quote_count": 2},
					"entities": {"urls": [{"expanded_url": "https://example.com/a"}]}
				}
			],
			"includes": {"users": [{"id": "u1", "username": "alice"}]}
		}`))
	}))
	defer fakeX.Close()

	t.Setenv("X_API_BASE_URL", fakeX.URL)
	t.Setenv("X_BEARER_TOKEN", fakeToken)

	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"x.search_posts_recent","arguments":{"query":"foo"}}}`)
	resp := decodeResponse(t, rec)
	result := resp["result"].(map[string]any)
	if result["isError"] == true {
		t.Fatalf("unexpected error result: %v", result)
	}
	content := result["content"].([]any)[0].(map[string]any)
	text, _ := content["text"].(string)

	var parsed map[string]any
	if err := json.Unmarshal([]byte(text), &parsed); err != nil {
		t.Fatalf("failed to parse tool text as JSON: %v, text=%s", err, text)
	}
	posts := parsed["posts"].([]any)
	if len(posts) != 1 {
		t.Fatalf("len(posts) = %d, want 1", len(posts))
	}
	post := posts[0].(map[string]any)

	if post["url"] != "https://x.com/alice/status/111" {
		t.Errorf("url = %v", post["url"])
	}
	if post["author_handle"] != "alice" {
		t.Errorf("author_handle = %v", post["author_handle"])
	}
	textRedacted, _ := post["text_redacted"].(string)
	if strings.Contains(textRedacted, "api_key=") {
		t.Errorf("text_redacted still contains sensitive marker: %q", textRedacted)
	}
	if !strings.Contains(textRedacted, "[REDACTED:env_assignment]") {
		t.Errorf("text_redacted missing redaction token: %q", textRedacted)
	}
	urls, _ := post["urls"].([]any)
	if len(urls) != 1 || urls[0] != "https://example.com/a" {
		t.Errorf("urls = %v", urls)
	}
	engagement := post["engagement"].(map[string]any)
	if engagement["like_count"].(float64) != 3 {
		t.Errorf("like_count = %v", engagement["like_count"])
	}
	if engagement["repost_count"].(float64) != 1 {
		t.Errorf("repost_count = %v", engagement["repost_count"])
	}

	if gotAuthHeader != "Bearer "+fakeToken {
		t.Errorf("upstream did not receive expected bearer header")
	}
	// Ensure the response body sent back to the MCP client never contains the
	// literal token value (would indicate a leak of the credential).
	if strings.Contains(rec.Body.String(), fakeToken) {
		t.Fatalf("response leaked bearer token: %s", rec.Body.String())
	}

	collectedAt, _ := post["collected_at"].(string)
	if !strings.Contains(collectedAt, "+09:00") {
		t.Errorf("collected_at not in JST offset: %q", collectedAt)
	}
}

func TestUpstreamErrorIsErrorWithoutTokenLeak(t *testing.T) {
	fakeToken := "another-fake-token-should-not-leak"
	fakeX := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"title": "Unauthorized", "errors": [{"message": "Invalid or expired token"}]}`))
	}))
	defer fakeX.Close()

	t.Setenv("X_API_BASE_URL", fakeX.URL)
	t.Setenv("X_BEARER_TOKEN", fakeToken)

	h := newTestHandler(t)
	rec := postJSON(t, h, `{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"x.get_posts","arguments":{"ids":["1"]}}}`)
	resp := decodeResponse(t, rec)
	result := resp["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("expected isError true, got %v", result)
	}
	content := result["content"].([]any)[0].(map[string]any)
	text, _ := content["text"].(string)
	if !strings.Contains(text, "status=401") {
		t.Errorf("expected status=401 in error text, got %q", text)
	}
	if strings.Contains(rec.Body.String(), fakeToken) {
		t.Fatalf("response leaked bearer token: %s", rec.Body.String())
	}
}
