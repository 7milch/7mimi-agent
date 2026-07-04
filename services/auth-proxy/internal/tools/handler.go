package tools

import (
	"encoding/json"
	"net/http"

	"github.com/nishiog/7mimi-agent/services/auth-proxy/internal/audit"
	"github.com/nishiog/7mimi-agent/services/auth-proxy/internal/policy"
)

// AuthorizeRequest is the PreToolUse payload sent by agent-runner hooks.
type AuthorizeRequest struct {
	SessionID string         `json:"session_id"`
	TaskID    string         `json:"task_id"`
	Role      string         `json:"role"`
	ToolName  string         `json:"tool_name"`
	Arguments map[string]any `json:"arguments"`
}

type Handler struct {
	engine *policy.Engine
	logger *audit.Logger
}

func NewHandler(engine *policy.Engine, logger *audit.Logger) *Handler {
	return &Handler{engine: engine, logger: logger}
}

func (h *Handler) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.handleHealthz)
	mux.HandleFunc("POST /v1/tool/authorize", h.handleAuthorize)
	return mux
}

func (h *Handler) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"status":"ok"}`))
}

func (h *Handler) handleAuthorize(w http.ResponseWriter, r *http.Request) {
	var req AuthorizeRequest
	decision := func() policy.Decision {
		// fail-closed: any malformed input blocks
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			return policy.Decision{Decision: "block", Reason: "invalid request body", PolicyVersion: "dev"}
		}
		if req.Role == "" || req.ToolName == "" {
			return policy.Decision{Decision: "block", Reason: "role and tool_name are required", PolicyVersion: "dev"}
		}
		return h.engine.Decide(req.Role, req.ToolName)
	}()

	h.logger.Log(audit.Event{
		SessionID: req.SessionID,
		TaskID:    req.TaskID,
		Role:      req.Role,
		ToolName:  req.ToolName,
		Decision:  decision.Decision,
		Reason:    decision.Reason,
	})

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(decision)
}
