CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  workspace_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_active_at TEXT,
  expires_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_role ON sessions(role);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_session_id ON tasks(session_id);

CREATE TABLE IF NOT EXISTS tool_events (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  task_id TEXT,
  role TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  decision TEXT NOT NULL,
  success INTEGER,
  duration_ms INTEGER,
  input_hash TEXT,
  input_redacted_json TEXT,
  output_hash TEXT,
  output_size INTEGER,
  error_json TEXT,
  policy_version TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_tool_events_session_id ON tool_events(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_events_tool_name ON tool_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_events_created_at ON tool_events(created_at);

CREATE TABLE IF NOT EXISTS research_queue (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  topic TEXT NOT NULL,
  ticker TEXT,
  company_name TEXT,
  reason TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  assigned_role TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_research_queue_status ON research_queue(status);
CREATE INDEX IF NOT EXISTS idx_research_queue_topic ON research_queue(topic);
CREATE INDEX IF NOT EXISTS idx_research_queue_score ON research_queue(score);

CREATE TABLE IF NOT EXISTS x_posts (
  id TEXT PRIMARY KEY,
  author_id TEXT,
  author_handle TEXT,
  created_at TEXT,
  collected_at TEXT NOT NULL,
  text_redacted TEXT NOT NULL,
  urls_json TEXT NOT NULL DEFAULT '[]',
  topics_json TEXT NOT NULL DEFAULT '[]',
  tickers_json TEXT NOT NULL DEFAULT '[]',
  engagement_json TEXT NOT NULL DEFAULT '{}',
  raw_ref TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_x_posts_collected_at ON x_posts(collected_at);
CREATE INDEX IF NOT EXISTS idx_x_posts_author_handle ON x_posts(author_handle);

CREATE TABLE IF NOT EXISTS web_sources (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  canonical_url TEXT,
  title TEXT,
  fetched_at TEXT NOT NULL,
  published_at TEXT,
  source_type TEXT,
  text_hash TEXT,
  summary TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_web_sources_url ON web_sources(url);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  repo TEXT,
  path TEXT NOT NULL,
  title TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  status TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  commit_sha TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_documents_repo_path ON documents(repo, path);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);
