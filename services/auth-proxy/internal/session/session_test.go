package session

import (
	"testing"
	"time"
)

func TestIssueAndResolve(t *testing.T) {
	s := NewStore()
	defer s.Close()
	token, ttl := s.Issue("ai_it_topic_runner")
	if token == "" {
		t.Fatal("expected non-empty token")
	}
	if ttl != defaultTTL {
		t.Fatalf("expected default ttl, got %v", ttl)
	}
	role, ok := s.Resolve(token)
	if !ok || role != "ai_it_topic_runner" {
		t.Fatalf("expected resolved role ai_it_topic_runner, got role=%q ok=%v", role, ok)
	}
}

func TestResolveUnknownToken(t *testing.T) {
	s := NewStore()
	defer s.Close()
	_, ok := s.Resolve("does-not-exist")
	if ok {
		t.Fatal("expected unknown token to not resolve")
	}
}

func TestResolveExpiredToken(t *testing.T) {
	s := NewStore(WithTTL(1 * time.Millisecond))
	defer s.Close()
	token, _ := s.Issue("ai_it_topic_runner")
	time.Sleep(5 * time.Millisecond)
	_, ok := s.Resolve(token)
	if ok {
		t.Fatal("expected expired token to not resolve")
	}
}

func TestSweepRemovesExpired(t *testing.T) {
	s := &Store{entries: make(map[string]*entry), ttl: time.Millisecond, callCap: 60, stopSweep: make(chan struct{})}
	token, _ := s.Issue("ai_it_topic_runner")
	time.Sleep(5 * time.Millisecond)
	s.sweep()
	s.mu.RLock()
	_, exists := s.entries[token]
	s.mu.RUnlock()
	if exists {
		t.Fatal("expected sweep to remove expired entry")
	}
}

func TestValid(t *testing.T) {
	s := NewStore()
	defer s.Close()
	token, _ := s.Issue("ai_it_topic_runner")
	if !s.Valid(token) {
		t.Fatal("expected valid token")
	}
	if s.Valid("unknown") {
		t.Fatal("expected unknown token to be invalid")
	}
}

func TestChargeCapsAndBlocks(t *testing.T) {
	s := NewStore(WithCallCap(3))
	defer s.Close()
	token, _ := s.Issue("ai_it_topic_runner")
	for i := 0; i < 3; i++ {
		if !s.Charge(token) {
			t.Fatalf("expected call %d to be within budget", i+1)
		}
	}
	if s.Charge(token) {
		t.Fatal("expected 4th call to exceed cap")
	}
}

func TestChargeUnknownToken(t *testing.T) {
	s := NewStore()
	defer s.Close()
	if s.Charge("unknown") {
		t.Fatal("expected charge on unknown token to return false")
	}
}
