# Overview

7mimi Agent の目的、背景、基本原則をまとめる。

## 0. ドキュメントの位置づけ

設計ドキュメントは `docs/` 配下にテーマ別に分割して管理する。入口は `docs/README.md` とし、詳細設計は `docs/detailed-design/README.md` に置く。

ドキュメントを分割する目的は、設計の見通しを良くし、実装時に参照しやすくするためである。設計方針・ADR・詳細設計・運用方針は、関連するファイルへ整理して配置する。

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

7mimi Agent では、この「LLM の外側で固める層」を明示的に2つに分ける。

```text
claude-proxy:
  Claude API credential boundary。
  Anthropic / Claude API への通信を中継する。
  ANTHROPIC_API_KEY 等の provider credential を保持する。
  usage / budget / audit / session attribution を担当する。
  Claude Code process や session workspace は保持しない。

auth-proxy:
  外部 tool/API credential boundary。
  X MCP / J-Quants MCP / Web Fetch / Document Store などへの tool call を認可する。
  role別 allowlist、rate limit、audit log、secret分離、PreToolUse/PostToolUse を担当する。
  Claude credential は持たない。
```

名前としては、Claude API 向け gateway を **claude-proxy**、外部tool/API側を **auth-proxy** と呼ぶ。`llm-gateway` という名前は使わない。

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
  orchestrator, scheduler, session manager, claude-proxy, auth-proxy, hook, metrics, runner lifecycle

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
- Claude provider credential は claude-proxy のみに置く
- X / J-Quants など外部API credential は auth-proxy または各MCP server のみに置く
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

Claude provider credential は **claude-proxy** だけが持つ。  
X / J-Quants / Document Store など外部tool/APIの credential は **auth-proxy** または各 MCP server だけが持つ。

```text
agent-runner:
  Claude Code / LLM agent / workspace / MCP client / skills / hooks
  Claude provider credentialなし
  X / J-Quants / Document Store credentialなし
  Claude API通信は claude-proxy に向ける
  tool/API通信は auth-proxy に向ける

claude-proxy:
  Claude API credential boundary
  ANTHROPIC_API_KEY 等の provider credentialあり
  Claude Code process / workspace は持たない
  外部API credentialなし

auth-proxy:
  external tool/API credential boundary
  role policy, MCP tool authorization, audit log
  Claude credentialなし

x-mcp-readonly:
  X credentialsあり。ただし agent-runner からは auth-proxy 経由でのみ利用する。

jquants-mcp:
  J-Quants API keyあり。ただし agent-runner からは auth-proxy 経由でのみ利用する。

document-store:
  docsへのwrite権限あり。ただし agent-runner からは auth-proxy 経由でのみ利用する。
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
