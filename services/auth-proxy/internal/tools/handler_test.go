package tools

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/audit"
	"github.com/7milch/7mimi-agent/services/auth-proxy/internal/policy"
)

func authorize(t *testing.T, body string) map[string]any {
	t.Helper()
	h := NewHandler(policy.NewDevEngine(), audit.NewLogger(io.Discard))
	req := httptest.NewRequest(http.MethodPost, "/v1/tool/authorize", strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	var decision map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &decision); err != nil {
		t.Fatalf("invalid decision JSON: %v", err)
	}
	return decision
}

func TestAiItRunnerCanSearchPosts(t *testing.T) {
	decision := authorize(t, `{"session_id":"sess_dev","task_id":"task_dev","role":"ai_it_topic_runner","tool_name":"x.search_posts_recent","arguments":{"query":"\"Claude Code\"","max_results":50}}`)
	if decision["decision"] != "allow" {
		t.Fatalf("decision = %v, want allow (reason: %v)", decision["decision"], decision["reason"])
	}
}

func TestAiItRunnerCannotCreatePost(t *testing.T) {
	decision := authorize(t, `{"role":"ai_it_topic_runner","tool_name":"x.create_post"}`)
	if decision["decision"] != "block" {
		t.Fatalf("decision = %v, want block", decision["decision"])
	}
	if !strings.Contains(decision["reason"].(string), "x.create_post") {
		t.Fatalf("reason = %v, want mention of denied pattern", decision["reason"])
	}
}

// TestAiItRunnerDeniesAllXWriteTools is a regression guard (Issue #18): X
// posts are signals, never evidence, and no role may ever be authorized to
// perform an X write operation. ai_it_topic_runner is the only role covered
// by the embedded dev policy (policy.NewDevEngine); every X write tool must
// still block for it whether via an explicit deny pattern or default-deny
// (not present in the role's allow list).
func TestAiItRunnerDeniesAllXWriteTools(t *testing.T) {
	xWriteTools := []string{
		"x.create_post",
		"x.delete_post",
		"x.like_post",
		"x.repost",
		"x.follow_user",
		"x.send_dm",
		"x.update_profile",
	}
	for _, toolName := range xWriteTools {
		toolName := toolName
		t.Run(toolName, func(t *testing.T) {
			decision := authorize(t, `{"role":"ai_it_topic_runner","tool_name":"`+toolName+`"}`)
			if decision["decision"] != "block" {
				t.Fatalf("decision = %v, want block for %s", decision["decision"], toolName)
			}
		})
	}
}

func TestAiItRunnerJquantsWildcardDenied(t *testing.T) {
	decision := authorize(t, `{"role":"ai_it_topic_runner","tool_name":"jquants.get_daily_quotes"}`)
	if decision["decision"] != "block" {
		t.Fatalf("decision = %v, want block", decision["decision"])
	}
}

func TestUnknownRoleBlocks(t *testing.T) {
	decision := authorize(t, `{"role":"nonexistent_role","tool_name":"x.search_posts_recent"}`)
	if decision["decision"] != "block" {
		t.Fatalf("decision = %v, want block", decision["decision"])
	}
}

func TestUnknownToolBlocks(t *testing.T) {
	decision := authorize(t, `{"role":"ai_it_topic_runner","tool_name":"filesystem.delete_everything"}`)
	if decision["decision"] != "block" {
		t.Fatalf("decision = %v, want block", decision["decision"])
	}
}

func TestMalformedBodyBlocks(t *testing.T) {
	decision := authorize(t, `{not json`)
	if decision["decision"] != "block" {
		t.Fatalf("decision = %v, want block", decision["decision"])
	}
}

func TestHealthz(t *testing.T) {
	h := NewHandler(policy.NewDevEngine(), audit.NewLogger(io.Discard))
	rec := httptest.NewRecorder()
	h.Routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("healthz status = %d, want 200", rec.Code)
	}
}
