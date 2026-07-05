package policy

import "testing"

func TestAiItTopicRunnerJqDenied(t *testing.T) {
	e := NewDevEngine()
	d := e.Decide("ai_it_topic_runner", "jq.get_listed_info")
	if d.Decision != "block" {
		t.Fatalf("expected jq.* denied for ai_it_topic_runner, got %+v", d)
	}
}

func TestAiItTopicRunnerXAllowed(t *testing.T) {
	e := NewDevEngine()
	d := e.Decide("ai_it_topic_runner", "x.search_posts_recent")
	if d.Decision != "allow" {
		t.Fatalf("expected x.search_posts_recent allowed for ai_it_topic_runner, got %+v", d)
	}
}

func TestInvestmentSignalRunnerAllowsSearchAndSlackDigest(t *testing.T) {
	e := NewDevEngine()
	for _, tool := range []string{"x.search_posts_recent", "slack.post_digest"} {
		d := e.Decide("investment_signal_runner", tool)
		if d.Decision != "allow" {
			t.Fatalf("expected %s allowed for investment_signal_runner, got %+v", tool, d)
		}
	}
}

func TestInvestmentSignalRunnerDeniesWritesAndJq(t *testing.T) {
	e := NewDevEngine()
	for _, tool := range []string{"x.create_post", "jq.get_listed_info", "trading.buy"} {
		d := e.Decide("investment_signal_runner", tool)
		if d.Decision != "block" {
			t.Fatalf("expected %s denied for investment_signal_runner, got %+v", tool, d)
		}
	}
}

func TestStockResearcherAllowsJqAndXSearch(t *testing.T) {
	e := NewDevEngine()
	for _, tool := range []string{
		"x.search_posts_recent",
		"jq.get_listed_info",
		"jq.get_daily_quotes",
		"jq.get_statements",
	} {
		d := e.Decide("stock_researcher", tool)
		if d.Decision != "allow" {
			t.Fatalf("expected %s allowed for stock_researcher, got %+v", tool, d)
		}
	}
}

func TestStockResearcherDeniesWritesAndTrading(t *testing.T) {
	e := NewDevEngine()
	for _, tool := range []string{"x.create_post", "document.write_markdown", "trading.buy"} {
		d := e.Decide("stock_researcher", tool)
		if d.Decision != "block" {
			t.Fatalf("expected %s denied for stock_researcher, got %+v", tool, d)
		}
	}
}

func TestUnknownRoleBlocks(t *testing.T) {
	e := NewDevEngine()
	d := e.Decide("unknown_role", "x.search_posts_recent")
	if d.Decision != "block" {
		t.Fatalf("expected unknown role blocked, got %+v", d)
	}
}
