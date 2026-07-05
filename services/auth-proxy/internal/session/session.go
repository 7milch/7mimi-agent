// Package session implements short-lived, role-bound session tokens minted
// via POST /session/issue (ADR-028). Tokens are high-entropy random values
// stored in an in-memory map; lookups are lazy-expiring and a background
// sweep periodically removes stale entries. A per-token call counter backs
// a deterministic hard cap on /mcp tools/call usage, independent of the
// prompt-level guardrails.
package session

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"
)

const defaultTTL = 35 * time.Minute
const sweepInterval = 5 * time.Minute

type entry struct {
	role      string
	expiresAt time.Time
	calls     int
}

// Store holds minted session tokens in memory, guarded by a mutex.
type Store struct {
	mu        sync.RWMutex
	entries   map[string]*entry
	ttl       time.Duration
	callCap   int
	stopSweep chan struct{}
}

// Option configures a Store at construction time.
type Option func(*Store)

// WithTTL overrides the default token TTL (35 minutes).
func WithTTL(ttl time.Duration) Option {
	return func(s *Store) { s.ttl = ttl }
}

// WithCallCap overrides the default per-token tools/call cap.
func WithCallCap(cap int) Option {
	return func(s *Store) { s.callCap = cap }
}

// NewStore builds a Store and starts its background sweep goroutine. Call
// Close to stop the goroutine (mainly useful in tests).
func NewStore(opts ...Option) *Store {
	s := &Store{
		entries:   make(map[string]*entry),
		ttl:       defaultTTL,
		callCap:   60,
		stopSweep: make(chan struct{}),
	}
	for _, opt := range opts {
		opt(s)
	}
	go s.sweepLoop()
	return s
}

// Close stops the background sweep goroutine.
func (s *Store) Close() {
	select {
	case <-s.stopSweep:
		// already closed
	default:
		close(s.stopSweep)
	}
}

func (s *Store) sweepLoop() {
	ticker := time.NewTicker(sweepInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			s.sweep()
		case <-s.stopSweep:
			return
		}
	}
}

func (s *Store) sweep() {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	for token, e := range s.entries {
		if now.After(e.expiresAt) {
			delete(s.entries, token)
		}
	}
}

// Issue mints a new session token bound to role, with the Store's
// configured TTL. Returns the token and its TTL.
func (s *Store) Issue(role string) (string, time.Duration) {
	token := generateToken()
	s.mu.Lock()
	s.entries[token] = &entry{role: role, expiresAt: time.Now().Add(s.ttl)}
	s.mu.Unlock()
	return token, s.ttl
}

func generateToken() string {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand failures are effectively unrecoverable; panic rather
		// than mint a predictable token.
		panic("session: crypto/rand read failed: " + err.Error())
	}
	return hex.EncodeToString(buf)
}

// Resolve returns the role bound to token, if the token exists and has not
// expired (lazy expiry: an expired entry is removed and treated as absent).
func (s *Store) Resolve(token string) (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.entries[token]
	if !ok {
		return "", false
	}
	if time.Now().After(e.expiresAt) {
		delete(s.entries, token)
		return "", false
	}
	return e.role, true
}

// Valid reports whether token exists and is unexpired, without requiring
// interest in its bound role (used by gitrelay, which only cares about
// validity).
func (s *Store) Valid(token string) bool {
	_, ok := s.Resolve(token)
	return ok
}

// Charge increments token's call counter and reports whether the call is
// within budget (true) or the cap has been exceeded (false). Charging an
// unknown or expired token returns false.
func (s *Store) Charge(token string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.entries[token]
	if !ok {
		return false
	}
	if time.Now().After(e.expiresAt) {
		delete(s.entries, token)
		return false
	}
	e.calls++
	return e.calls <= s.callCap
}
