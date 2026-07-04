package policy

import (
	"fmt"
	"path"
)

// Decision mirrors the Python PolicyEngine decision shape so agent-runner
// hooks can treat local and remote decisions identically.
type Decision struct {
	Decision      string `json:"decision"`
	Reason        string `json:"reason"`
	PolicyVersion string `json:"policy_version"`
}

func allow(reason string) Decision {
	return Decision{Decision: "allow", Reason: reason, PolicyVersion: "dev"}
}

func block(reason string) Decision {
	return Decision{Decision: "block", Reason: reason, PolicyVersion: "dev"}
}

// RolePolicy holds glob-style allow/deny tool patterns. Deny wins over allow;
// anything not explicitly allowed is blocked (default deny).
type RolePolicy struct {
	Allow []string
	Deny  []string
}

type Engine struct {
	roles map[string]RolePolicy
}

// NewDevEngine returns the embedded development policy. It intentionally
// covers only ai_it_topic_runner for the MVP; full config/policy.yaml
// compatibility comes later.
func NewDevEngine() *Engine {
	return &Engine{roles: map[string]RolePolicy{
		"ai_it_topic_runner": {
			Allow: []string{
				"x.search_posts_recent",
				"x.get_posts",
				"x.get_users",
				"x.get_users_by_username",
				"web.fetch_url",
				"web.extract_article",
				"document.write_markdown",
				"document.commit_and_push_markdown_repo",
			},
			Deny: []string{
				"x.create_post",
				"x.like_post",
				"x.repost",
				"x.follow_user",
				"x.send_dm",
				"jquants.*",
				"trading.*",
				"document.write_outside_workspace",
				"document.delete_recursive",
			},
		},
	}}
}

// Decide is deterministic and fail-closed: unknown roles, unknown tools, and
// pattern errors all result in block.
func (e *Engine) Decide(role, toolName string) Decision {
	rolePolicy, ok := e.roles[role]
	if !ok {
		return block(fmt.Sprintf("unknown role or missing role policy: %s", role))
	}
	for _, pattern := range rolePolicy.Deny {
		if matched, err := path.Match(pattern, toolName); err == nil && matched {
			return block(fmt.Sprintf("tool denied for role %s: %s", role, pattern))
		}
	}
	for _, pattern := range rolePolicy.Allow {
		if matched, err := path.Match(pattern, toolName); err == nil && matched {
			return allow("allowed")
		}
	}
	return block(fmt.Sprintf("tool not allowed for role %s: %s", role, toolName))
}
