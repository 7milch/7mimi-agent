package audit

import (
	"encoding/json"
	"io"
	"time"
)

// Event is metadata-only audit information for tool authorization decisions.
// Tool arguments are not logged by default.
type Event struct {
	Timestamp string `json:"timestamp"`
	SessionID string `json:"session_id"`
	TaskID    string `json:"task_id"`
	Role      string `json:"role"`
	ToolName  string `json:"tool_name"`
	Decision  string `json:"decision"`
	Reason    string `json:"reason"`
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
		return // fail-open: audit must not break authorization responses
	}
	l.out.Write(append(line, '\n'))
}
