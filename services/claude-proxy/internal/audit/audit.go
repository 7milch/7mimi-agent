package audit

import (
	"encoding/json"
	"io"
	"time"
)

// Event is metadata-only audit information. Request/response bodies and
// provider credentials must never appear here.
type Event struct {
	Timestamp      string `json:"timestamp"`
	SessionID      string `json:"session_id"`
	Role           string `json:"role"`
	Method         string `json:"method"`
	Path           string `json:"path"`
	UpstreamStatus int    `json:"upstream_status"`
	DurationMS     int64  `json:"duration_ms"`
	Decision       string `json:"decision,omitempty"`
	Reason         string `json:"reason,omitempty"`
}

type Logger struct {
	out io.Writer
}

func NewLogger(out io.Writer) *Logger {
	return &Logger{out: out}
}

func (l *Logger) Log(event Event) {
	if event.Timestamp == "" {
		event.Timestamp = time.Now().UTC().Format(time.RFC3339Nano)
	}
	line, err := json.Marshal(event)
	if err != nil {
		return // fail-open: audit must not break proxying
	}
	l.out.Write(append(line, '\n'))
}
