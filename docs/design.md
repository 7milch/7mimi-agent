# 7mimi Agent Design

Status: Draft v0.1  
Date: 2026-07-04  
Owner: 7milch

## 0. このドキュメントの位置づけ

このファイルを **7mimi Agent の設計の正本** とする。

設計メモ、ADR、ロードマップ、未決定事項、運用方針は原則としてこのファイルに集約する。ドキュメントを細かく分散させない。将来、実装量が増えて分割が必要になった場合も、まずこのファイルに目次と分割理由を残す。

---

## 1. Vision

7mimi Agent は、X/Twitter や金融データ、Web、将来的には Slack/Discord/GitHub などのイベントをトリガーにして、自律的に情報収集・銘柄調査・ドキュメント作成を行う **MCP-first autonomous research agent** である。

目指す世界は、ユーザーが毎回 AI に聞く世界ではなく、エージェントが常駐し、設定されたトリガーやスケジュールに応じて自律的に調査し、あとで読める形の知識に変換していく世界である。

一文でまとめると次の通り。

> X MCP で世の中のシグナルを拾い、J-Quants MCP で日本株のファクトを確認し、LLM Agent が調査キューとドキュメントに変換する。

---

## 2. Background: Mercari Engineering blog から取り込む思想

このプロジェクトは、Mercari Engineering Blog の「決済プラットフォームに常駐する自律AIエージェントの設計と運用」の思想を強く参考にする。

取り込む思想は以下。

### 2.1 Ambient Agent

エージェントは、単なるチャット UI ではなく、チームや個人の作業環境に常駐する運用担当として振る舞う。

```text
人間がAIを毎回キックする
  ではなく
イベント・スケジュール・外部シグナルをトリガーにAIが自律実行する
```

7mimi Agent では、まず以下のトリガーを想定する。

- cron schedule
- X search / trend signal
- manual CLI request
- 将来的に Slack / Discord mention
- 将来的に GitHub issue / PR event

### 2.2 LLMを信用しすぎない

LLM には「何を調べるべきか」「どう整理するか」を任せる。  
一方で、「何を実行してよいか」「どの API にアクセスできるか」「どこに書き込めるか」は、LLM の外側の決定的な仕組みで制御する。

```text
LLM:
  調査計画、仮説、要約、レポート作成

Platform / Gateway / Hook:
  認証、認可、rate limit、監査、危険操作のブロック、秘密情報保護
```

### 2.3 セッションごとの隔離

長期的には、1つのタスクまたはスレッドに対して1つの runner container を割り当てる。

```text
session A -> runner container A
session B -> runner container B
scheduled job C -> runner container C
```

MVPではコンテナ隔離を必須にしないが、設計上は最初から「セッション」「runner」「workspace」を分けて考える。

### 2.4 PreToolUse は fail-closed

危険操作を止める層は fail-closed にする。

```text
policy check success and allowed -> allow
policy check success and denied  -> block
policy check crashed             -> block
```

hook が壊れたときに安全側へ倒す。

### 2.5 PostToolUse は fail-open

計測・ログ保存は重要だが、エージェント本体を止める理由にはしない。

```text
metrics success -> continue
metrics failure -> log best-effort and continue
```

### 2.6 Platform と Tenant の分離

個別の役割やドメイン知識は tenant 側に寄せる。  
Slack/X/J-Quants/MCP接続、runner、hook、metrics、policy は platform 側に寄せる。

7mimi Agent では以下の分離を採用する。

```text
Platform:
  orchestrator, scheduler, session manager, mcp gateway, hook, metrics, runner lifecycle

Tenant / Role:
  x_collector, stock_researcher, document_writer, source_verifier のルールとスキル
```

---

## 3. Goals / Non-goals

### 3.1 Goals

- MCP-first architecture にする
- X MCP で情報収集する
- J-Quants MCP で日本株の構造化データを取得する
- X の情報を直接事実扱いせず、research queue に入れる
- J-Quants / EDINET / IR / Web などでファクト確認した上で銘柄調査メモを作る
- 生成物は Markdown として保存する
- tool call を監査ログとして保存する
- role ごとに使える MCP server / tool を制限する
- API key を agent-runner に直接渡さない
- 将来的にセッションごとに isolated runner container を起動する

### 3.2 Non-goals

初期版では以下をやらない。

- 自動売買
- 売買推奨の生成
- X への自律投稿
- X の like / repost / follow / DM 操作
- 本番環境や外部サービスへの無制限な書き込み
- LLM に秘密情報を渡すこと
- 複数ドキュメントへの設計分散

---

## 4. Core principles

### 4.1 X is signal, J-Quants is evidence

X はシグナルであり、根拠ではない。

```text
X:
  話題化、速報、ノイズ、個人見解、ポジショントークを含む

J-Quants:
  上場銘柄、株価、財務、配当、決算予定などの構造化データ

EDINET / IR / TDnet-like source:
  法定開示、会社発表、決算資料、リスク情報
```

銘柄レポートでは必ず以下を分ける。

- 確認済み事実
- X由来の話題・仮説
- 未確認事項
- 次に調べること

### 4.2 Agent runner に秘密情報を置かない

API key は MCP server / credential broker / gateway 側だけが持つ。

```text
agent-runner:
  LLM runtime, MCP client, skills, prompt
  API keyなし

x-mcp-readonly:
  X credentialsあり

jquants-mcp:
  J-Quants API keyあり

document-store:
  docsへのwrite権限あり
```

### 4.3 role-based tool access

全Agentに全MCP toolを渡さない。

```text
x_collector:
  X read-only tools only

stock_researcher:
  J-Quants read tools + Web fetch

document_writer:
  Document store write tools

source_verifier:
  Read tools only
```

### 4.4 human-readable memory

生成物はあとで人間が読める Markdown として保存する。  
DB は検索・重複排除・状態管理に使うが、最終成果物は Markdown を正とする。

### 4.5 measure adoption, not just capability

「何ができるか」だけでなく「何に使われたか」を測る。

- どの role が何回動いたか
- どの MCP tool が使われたか
- どの research item がドキュメント化されたか
- どれだけ block されたか
- どの成果物が人間に読まれたか

---

## 5. High-level architecture

```text
[Trigger]
  ├─ cron schedule
  ├─ manual CLI
  ├─ X signal polling
  ├─ future: Slack / Discord mention
  └─ future: GitHub event
        │
        ▼
[Orchestrator]
  ├─ trigger router
  ├─ task planner
  ├─ session manager
  ├─ role resolver
  └─ job queue
        │
        ▼
[Agent Runner]
  ├─ LLM runtime
  ├─ role prompt / skill
  ├─ MCP client
  ├─ PreToolUse hook
  └─ PostToolUse hook
        │
        ▼
[MCP Gateway / Policy Layer]
  ├─ tool allowlist / denylist
  ├─ rate limit
  ├─ cache
  ├─ audit log
  ├─ data freshness check
  └─ credential boundary
        │
        ├─ X MCP read-only
        ├─ J-Quants MCP
        ├─ Web Fetch MCP
        ├─ EDINET / disclosure tool, future
        ├─ Document Store MCP
        └─ Metrics Store
```

---

## 6. Runtime and container model

### 6.1 Target model

最終的には、セッションごとに runner container を起動する。

```text
host
  ├─ agent-server
  ├─ scheduler
  ├─ mcp-gateway
  ├─ x-mcp-readonly
  ├─ jquants-mcp
  ├─ document-store
  └─ docker daemon
       ├─ runner-session-001
       ├─ runner-session-002
       └─ runner-scheduled-job-003
```

### 6.2 Session lifecycle

```text
created
  ↓
starting
  ↓
running
  ↓
idle
  ↓
stopped
  ↓
expired
```

想定ポリシー。

```yaml
session_policy:
  runner_idle_timeout_minutes: 30
  session_ttl_minutes: 10080 # 7 days
  runner_memory_limit: 4g
  runner_pids_limit: 256
```

### 6.3 MVP model

初期版は Docker を必須にしない。

```text
.sessions/
  sess_xxx/
    workspace/
    events.jsonl
    result.md
```

Phase 1 では通常の subprocess / local runtime として動かし、Phase 2 以降で Docker runner に移行する。

### 6.4 Container communication options

候補は3つ。

#### Option A: one request one process

```text
orchestrator -> spawn agent process -> result
```

- 実装が最も簡単
- 会話継続性は弱い
- MVP向き

#### Option B: one request one container

```text
orchestrator -> docker run runner -> result -> remove
```

- 隔離しやすい
- 起動コストが高い
- セッション継続は弱い

#### Option C: one session one persistent container

```text
orchestrator -> docker run runner for session
same session -> reuse runner
idle timeout -> stop
```

- Mercari blog の思想に最も近い
- 実装は重い
- warm session が速い

### 6.5 Current decision

MVP は Option A で始める。  
設計は Option C に移行できるように、最初から session / runner / workspace / tool call を分離して実装する。

---

## 7. Roles

### 7.1 Orchestrator

全体の司令塔。

責務:

- trigger を受ける
- role を選ぶ
- session を作る
- task を queue に入れる
- runner を起動する
- 成果物を document store に渡す

Orchestrator は外部データを直接解釈しない。判断は role agent に委譲する。

### 7.2 XCollectorAgent

X上の情報を収集し、research queue に入れる。

責務:

- 監視クエリで投稿検索
- 監視アカウントの投稿確認
- URL抽出
- 銘柄コード・企業名・テーマ抽出
- スパム・重複除外
- スコアリング
- research queue への登録

使える tool:

- X MCP read-only
- Web Fetch read-only, optional
- ResearchQueue append

禁止:

- Xへの投稿
- like / repost / follow / DM
- 銘柄評価の断定
- document への直接 final write

### 7.3 StockResearchAgent

銘柄調査を行う。

責務:

- 銘柄コードの正規化
- J-Quants MCP で基本情報・株価・財務・配当・決算予定を取得
- 必要に応じて EDINET / IR / Web を確認
- X由来の仮説をファクト確認する
- 銘柄調査メモの draft を作る

使える tool:

- J-Quants MCP
- Web Fetch MCP
- EDINET/disclosure tool, future
- Document read

禁止:

- X write
- Document final write
- 売買推奨
- 自動売買

### 7.4 DocumentWriterAgent

調査結果を Markdown に整える。

責務:

- daily digest 作成
- stock memo 作成
- topic note 作成
- research queue の status 更新
- 出力フォーマット統一

使える tool:

- Document Store MCP
- Metrics write

禁止:

- X / J-Quants の直接利用
- 数値の捏造
- source_verifier 未通過の重要レポートの publish

### 7.5 SourceVerifierAgent

調査結果の根拠を検証する。

責務:

- 出典があるか確認
- 数値・日付・決算期・単位の確認
- X情報を事実扱いしていないか確認
- 「買い」「売り」など投資助言表現の検出
- 古い情報を最新扱いしていないか確認

使える tool:

- J-Quants MCP read
- Web Fetch MCP read
- Document read

禁止:

- final document write
- 外部サービス write

---

## 8. MCP-first design

### 8.1 MCP servers

初期想定。

```text
x-mcp-readonly:
  X API access. 初期版では read-only tool のみ。

jquants-mcp:
  日本株の構造化データ取得。

web-fetch-mcp:
  URL / article / PDF / IRページ取得。

document-store-mcp:
  Markdown docs と research queue への書き込み。

metrics-store:
  tool call / session / output の計測。
```

将来候補。

```text
edinet-mcp or edinet-tool:
  有価証券報告書、大量保有報告書、臨時報告書など。

tdnet-like disclosure tool:
  適時開示、決算短信、業績修正、配当修正など。

slack/discord-mcp:
  通知・mention trigger。
```

### 8.2 MCP Gateway

Agent から MCP server を直接叩かせず、MCP Gateway / Policy Layer を挟む。

責務:

- role ごとの tool allowlist
- write 系 tool の block
- API key 秘匿
- rate limit
- cache
- audit log
- data freshness metadata の付与
- prompt injection 対策
- network allowlist

### 8.3 Tool allowlist draft

```yaml
roles:
  x_collector:
    mcp_servers:
      - x_mcp_readonly
      - web_fetch
      - research_queue
    allowed_tools:
      - x.search_posts_recent
      - x.get_posts
      - x.get_users
      - x.get_users_by_username
      - web.fetch_url
      - queue.append_candidate
    denied_tools:
      - x.create_post
      - x.delete_post
      - x.like_post
      - x.repost
      - x.follow_user
      - x.send_dm

  stock_researcher:
    mcp_servers:
      - jquants
      - web_fetch
      - document_store
    allowed_tools:
      - jquants.get_listed_info
      - jquants.get_daily_quotes
      - jquants.get_financial_statements
      - jquants.get_dividends
      - jquants.get_earnings_calendar
      - web.fetch_url
      - document.read
    denied_tools:
      - document.final_publish
      - x.create_post
      - trading.place_order

  document_writer:
    mcp_servers:
      - document_store
      - metrics
    allowed_tools:
      - document.write_markdown
      - document.update_research_queue
      - metrics.record_output
    denied_tools:
      - x.*
      - jquants.*

  source_verifier:
    mcp_servers:
      - jquants
      - web_fetch
      - document_store
    allowed_tools:
      - jquants.get_listed_info
      - jquants.get_daily_quotes
      - jquants.get_financial_statements
      - web.fetch_url
      - document.read
    denied_tools:
      - document.final_publish
      - x.*
```

### 8.4 X MCP policy

初期版では X MCP は read-only で使う。

許可候補:

- search recent posts
- get posts
- get users
- get users by username

禁止:

- create post
- delete post
- like
- repost
- follow
- unfollow
- DM
- profile update
- bookmark write

将来的に投稿を行う場合も、以下の human-in-the-loop を必須にする。

```text
Agent drafts post
  ↓
Human approves
  ↓
Write-capable X MCP posts
```

### 8.5 J-Quants MCP policy

J-Quants MCP は StockResearchAgent の正式な銘柄データ取得口とする。

ルール:

- 銘柄レポートには J-Quants データ取得日時を入れる
- 対象期間を明示する
- 調整済み/非調整の区別を明示する
- 決算期・単位を明示する
- X由来の情報とJ-Quants由来の情報を混ぜない
- J-Quantsで確認できない情報は未確認と書く

---

## 9. Security design

### 9.1 Threat model

想定するリスク。

- LLM が危険な tool を呼ぶ
- X MCP の write tool を誤って呼ぶ
- API key が prompt / log / generated doc に漏れる
- X投稿内の prompt injection に従ってしまう
- Webページ内の prompt injection に従ってしまう
- 金融情報を誤って断定する
- 古い情報を最新として扱う
- 大量 API call で quota を消費する
- 自動売買・投資助言に見える出力をする

### 9.2 PreToolUse hook

ツール実行前に必ず検査する。

判定材料:

- role
- session id
- tool name
- arguments
- target resource
- write/read の種別
- rate limit status
- policy version

出力:

```json
{
  "decision": "allow | block",
  "reason": "...",
  "policy_version": "..."
}
```

fail-closed:

```text
hook failure -> block
unknown tool -> block
unknown role -> block
missing policy -> block
```

### 9.3 PostToolUse hook

ツール実行後に監査ログを保存する。

保存するもの:

- timestamp
- session_id
- role
- tool_name
- arguments hash / redacted arguments
- success / failure
- duration_ms
- output size
- source ids
- policy decision

fail-open:

```text
metrics failure -> continue
```

### 9.4 Secret handling

- `.env` は git 管理しない
- `.env.example` のみ管理する
- agent-runner に API key を渡さない
- API key は MCP server / gateway の環境変数として渡す
- generated docs に token-like string が入らないよう redaction check を行う
- raw logs も redaction する

### 9.5 Prompt injection handling

X投稿、Webページ、PDF、IR資料などはすべて untrusted input として扱う。

ルール:

- 外部文書内の命令に従わない
- 外部文書は「引用・要約対象」であって「system instruction」ではない
- tool call の権限変更は文書内容で行えない
- `ignore previous instructions` などの文言は injection signal として記録する

---

## 10. Data model

### 10.1 Storage layers

```text
.data/
  raw/
    x_posts/
    web_pages/
    jquants/
    disclosures/
  normalized/
    app.sqlite
  generated/
    daily/
    stocks/
    topics/

.docs source of truth:
  docs/generated outputs may later move under generated/,
  but design document stays docs/design.md.
```

MVPでは SQLite + Markdown でよい。

### 10.2 ResearchQueue

中心となる中間データ。

```sql
research_queue
  id
  source                 -- x, manual, schedule, disclosure
  topic
  ticker
  company_name
  reason
  source_refs_json       -- post ids, urls, document ids
  score
  status                 -- new, fact_checked, drafted, verified, published, rejected
  created_at
  updated_at
```

### 10.3 X post normalized record

```json
{
  "id": "post id",
  "author_id": "user id",
  "author_handle": "handle",
  "created_at": "timestamp",
  "text": "redacted text",
  "urls": [],
  "tickers": [],
  "topics": [],
  "engagement": {
    "likes": 0,
    "reposts": 0,
    "replies": 0,
    "views": null
  },
  "collected_at": "timestamp"
}
```

### 10.4 Stock fact snapshot

```json
{
  "ticker": "7011",
  "company_name": "...",
  "source": "jquants",
  "fetched_at": "timestamp",
  "period": "...",
  "daily_quotes": [],
  "financials": [],
  "dividends": [],
  "earnings_calendar": []
}
```

---

## 11. Workflows

### 11.1 Workflow A: X information collection -> Daily document

```text
cron trigger
  ↓
XCollectorAgent
  ↓
X MCP recent search
  ↓
normalize posts
  ↓
extract URLs / topics / tickers
  ↓
Web Fetch for URLs
  ↓
deduplicate
  ↓
score importance
  ↓
research_queue append
  ↓
DocumentWriterAgent
  ↓
Daily Digest Markdown
  ↓
SourceVerifierAgent lightweight check
```

出力例:

```text
.data/generated/daily/2026-07-04.md
```

### 11.2 Workflow B: X stock signal -> J-Quants fact check -> Market digest

```text
XCollectorAgent
  ↓
extract stock-related signals
  ↓
research_queue
  ↓
StockResearchAgent
  ↓
J-Quants MCP fact check
  ↓
SourceVerifierAgent
  ↓
DocumentWriterAgent
  ↓
Daily Market Research Digest
```

重要ルール:

```text
X投稿を銘柄評価の根拠にしない。
X投稿は research_queue に入る理由としてのみ使う。
```

### 11.3 Workflow C: Manual stock research

```text
user: 7011を調べて
  ↓
Orchestrator resolves role: stock_researcher
  ↓
StockResearchAgent gets J-Quants data
  ↓
Web/IR/EDINET optional check
  ↓
Draft stock memo
  ↓
SourceVerifierAgent
  ↓
DocumentWriterAgent writes markdown
```

### 11.4 Workflow D: Weekly research queue review

```text
weekly schedule
  ↓
review research_queue
  ↓
select high score candidates
  ↓
remove stale/noisy candidates
  ↓
update topic notes and stock notes
```

---

## 12. Output templates

### 12.1 Daily digest

```markdown
# Daily Digest - YYYY-MM-DD

## Summary

## Top Topics

### 1. Topic name

- Why it matters:
- Key sources:
- Related posts:
- Confidence: High / Medium / Low
- Next action:

## Research Queue Updates

| Score | Topic | Ticker | Reason | Status |
|---:|---|---|---|---|

## Notes

- X is treated as signal, not evidence.
```

### 12.2 Stock memo

```markdown
# Stock Research Memo: TICKER Company Name

## 0. Metadata

- Created at:
- Data fetched at:
- Data sources:
  - J-Quants:
  - EDINET/IR/Web:
  - X posts: signal only
- This is not investment advice.

## 1. Executive summary

## 2. Company overview

## 3. Price / volume overview

## 4. Financials

## 5. Dividends / shareholder returns

## 6. Catalysts

## 7. Risks

## 8. X signals

X情報は調査トリガーとしてのみ扱う。

## 9. Verified facts

## 10. Unverified items

## 11. Next actions
```

### 12.3 Verification report

```markdown
# Verification Report

## Checked items

- [ ] 数値に出典がある
- [ ] データ取得日時がある
- [ ] X情報を事実扱いしていない
- [ ] 投資助言表現がない
- [ ] 古い情報を最新扱いしていない
- [ ] API key / secret が出力に含まれていない

## Findings

## Required fixes
```

---

## 13. Scheduler design

初期ジョブ案。

```yaml
jobs:
  - name: x-signal-collector
    enabled: true
    role: x_collector
    cron: "*/30 8-23 * * *"
    timezone: "Asia/Tokyo"
    active_deadline_seconds: 600
    backoff_limit: 1
    concurrency_policy: forbid
    prompt: |
      監視クエリに基づいてX投稿を収集する。
      日本株銘柄、AI Agent関連技術、重要URLを抽出し、research_queue に登録する。
      Xへのwrite操作は禁止。

  - name: stock-signal-fact-check
    enabled: true
    role: stock_researcher
    cron: "0 16 * * 1-5"
    timezone: "Asia/Tokyo"
    active_deadline_seconds: 1200
    backoff_limit: 1
    concurrency_policy: forbid
    prompt: |
      research_queue の上位候補について、J-Quants MCPで基本情報・株価・財務を確認する。
      X情報は調査トリガーとしてのみ扱う。

  - name: daily-digest-writer
    enabled: true
    role: document_writer
    cron: "30 17 * * 1-5"
    timezone: "Asia/Tokyo"
    active_deadline_seconds: 900
    backoff_limit: 1
    concurrency_policy: forbid
    prompt: |
      本日のXシグナルとファクト確認結果をもとに daily digest をMarkdownで作成する。
      売買推奨ではなく、調査候補と確認済み事実を分ける。

  - name: weekly-research-review
    enabled: true
    role: source_verifier
    cron: "0 10 * * 6"
    timezone: "Asia/Tokyo"
    active_deadline_seconds: 1800
    backoff_limit: 0
    concurrency_policy: forbid
    prompt: |
      research_queue と生成済みドキュメントを見直し、古い候補・未確認候補・要深掘り候補を整理する。
```

---

## 14. Metrics and observability

### 14.1 Events

```json
{
  "timestamp": "2026-07-04T00:00:00+09:00",
  "session_id": "sess_xxx",
  "role": "stock_researcher",
  "tool": "jquants.get_financial_statements",
  "decision": "allow",
  "success": true,
  "duration_ms": 1234,
  "input_hash": "...",
  "output_size": 2048
}
```

### 14.2 Metrics to track

- sessions count
- jobs count
- role usage count
- tool call count
- blocked tool call count
- X posts collected
- research queue candidates created
- candidates fact-checked
- documents generated
- verification failures
- average runtime
- API quota usage

### 14.3 Adoption metrics

Capability ではなく adoption を見る。

- どのレポートが継続生成されているか
- どの topic / ticker が何度も queue に上がるか
- 人間が手で再調査したものは何か
- verifier がよく落とすパターンは何か

---

## 15. File and project structure

初期構成案。

```text
7mimi-agent/
  README.md
  .env.example
  .gitignore
  docs/
    design.md                 # このファイル。設計の正本。
  src/                        # future Python package
    sevenmimi_agent/
      orchestrator/
      runner/
      gateway/
      roles/
      mcp/
      metrics/
  config/
    roles.yaml                # role definitions
    policy.yaml               # deterministic platform policy
    schedules.yaml            # autonomous job definitions
  .data/                      # runtime, gitignored
  .sessions/                  # runtime, gitignored
```

ドキュメント分散を避けるため、当面 `docs/design.md` 以外の設計ドキュメントは作らない。

---

## 16. Implementation roadmap

### Phase 0: Design and repository initialization

- [x] git init
- [x] README.md
- [x] .gitignore
- [x] .env.example
- [x] docs/design.md

### Phase 1: Local MVP

- [ ] SQLite schema for research_queue / events
- [ ] local orchestrator
- [ ] role definitions
- [ ] mock MCP gateway
- [ ] X MCP read-only connection test
- [ ] J-Quants MCP connection test
- [ ] manual command: `research stock 7011`
- [ ] manual command: `collect x ai-agent`
- [ ] Markdown output generation

### Phase 2: Policy and hooks

- [ ] PreToolUse hook
- [ ] PostToolUse hook
- [ ] tool allowlist per role
- [ ] secret redaction
- [ ] X write tool block tests
- [ ] prompt injection fixture tests

### Phase 3: Scheduled autonomy

- [ ] cron scheduler
- [ ] x-signal-collector job
- [ ] stock-signal-fact-check job
- [ ] daily-digest-writer job
- [ ] concurrency policy
- [ ] retry / timeout

### Phase 4: Containerized runner

- [ ] runner image
- [ ] one request one container
- [ ] session workspace
- [ ] resource limits
- [ ] network restrictions
- [ ] API key separation by MCP container

### Phase 5: Persistent session runner

- [ ] one session one runner container
- [ ] idle timeout
- [ ] session TTL
- [ ] workspace reuse
- [ ] warm session support

### Phase 6: Source expansion

- [ ] EDINET tool / MCP
- [ ] IR page fetch and parsing
- [ ] TDnet-like disclosure integration if available / needed
- [ ] Slack / Discord notification
- [ ] GitHub issue / PR trigger

---

## 17. ADR: decisions so far

### ADR-001: Single design document

Decision: 設計は `docs/design.md` に集約する。  
Reason: ドキュメントが散らばることを避けるため。

### ADR-002: MCP-first architecture

Decision: 外部サービス連携は原則 MCP 経由にする。  
Reason: Agent runtime と API 実装・認証情報を分離しやすく、role-based policy を適用しやすいため。

### ADR-003: X is signal, not evidence

Decision: X情報は調査トリガーとして扱い、銘柄評価の根拠にはしない。  
Reason: Xには噂、ノイズ、ポジショントーク、誤情報が混ざるため。

### ADR-004: J-Quants MCP as primary stock data source

Decision: 日本株の構造化データは J-Quants MCP を主たる取得口にする。  
Reason: 契約済みであり、自律AgentからMCPとして扱いやすいため。

### ADR-005: X MCP read-only in initial version

Decision: 初期版では X MCP の write tool を無効化する。  
Reason: 自律投稿・like・follow などは事故時の影響が大きく、human-in-the-loop が必要なため。

### ADR-006: Start local, design for containers

Decision: MVPは local runner で始めるが、設計は session-based container runner へ移行可能にする。  
Reason: 最初からコンテナ管理を作り込むと重いため。ただしMercari blogの思想であるセッション隔離は将来の中核にする。

### ADR-007: PreToolUse fail-closed, PostToolUse fail-open

Decision: 危険操作を止める hook は fail-closed、計測 hook は fail-open。  
Reason: セキュリティは安全側に倒し、計測は本体動作を妨げないため。

### ADR-008: Python as initial implementation language

Decision: 初期実装言語は Python とする。  
Reason: データ収集、SQLite、金融データ処理、スケジューラー、バッチ実行との相性がよく、まず自律リサーチの縦切りを作るため。Bot/UI統合は後から追加する。

### ADR-009: Config-first minimal platform policy

Decision: role / policy / schedule は `config/roles.yaml`, `config/policy.yaml`, `config/schedules.yaml` に分離する。  
Reason: 設計ドキュメントは1本に保ちつつ、実行時設定は機械可読なYAMLとして管理するため。

---

## 18. Open questions / 壁打ちしたいこと

### Q1. 最初の実装言語

候補:

- TypeScript / Node.js / Bun
- Python / FastAPI / asyncio

決定:

- Python を採用する。

理由:

- データ収集・正規化・SQLite・スケジューラー・金融データ処理との相性がよい。
- MCP server/client 連携はSDKまたはsubprocess境界で吸収できる。
- まずは自律Research Agentとしての縦切りを優先し、Web UIやbot統合は後段に回す。

### Q2. 最初の出力先

候補:

- Markdown file
- Notion
- GitHub Wiki
- Slack / Discord

現時点の仮決め:

- Markdown file。Git管理・差分確認・再現性が高いため。

### Q3. X監視テーマ

候補:

- AI Agent / Claude Code / Codex / MCP
- 日本株テーマ
- 半導体
- 防衛
- 電力 / データセンター
- 高配当 / バリュー
- 自分が指定するアカウント群

要確認:

- 最初の監視クエリを何にするか。

### Q4. 銘柄調査の深さ

候補:

- Level 1: 1ページ概要
- Level 2: 財務・株価・直近開示まで
- Level 3: 有報・セグメント・同業比較まで
- Level 4: 投資仮説・リスク・カタリスト・ウォッチ条件まで

現時点の仮決め:

- MVPは Level 1〜2。

### Q5. EDINET / TDnet 相当の扱い

現時点の仮決め:

- MVPでは J-Quants + Web fetch に絞る。
- EDINET は Phase 6 で追加。
- TDnet相当は費用・API可用性を見て判断。

### Q6. 自律度

候補:

- read-only fully autonomous
- write draft autonomous, publish manual approval
- full autonomous publish

現時点の仮決め:

- read-only + Markdown生成までは自律。
- X投稿など外部への発信は manual approval 必須。

---

## 19. Immediate next steps

1. 実装言語を決める
2. MCP接続方式を確認する
3. `roles.yaml` / `policy.yaml` / `schedules.yaml` の最小案を作る
4. SQLite schema を作る
5. X MCP read-only で1クエリ取得する
6. J-Quants MCP で1銘柄取得する
7. `research_queue -> stock memo` の縦切りを作る

