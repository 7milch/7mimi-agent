# 7mimi-agent 技術解説シリーズ — 目録

このリポジトリの実コードと ADR を題材にした、教科書スタイルの技術解説集。前提知識のない読者を想定し、実際のコードを一行ずつ引用しながら解説する。各編は独立して読める。各編には HTML 版(ライト/ダーク両対応・図入り)と Markdown 版がある。

| # | タイトル | 分類 | HTML | Markdown |
|---|---|---|---|---|
| 0 | 自律 AI エージェント基盤 7mimi-agent における「信頼しない設計」の実装 | 技術レポート・全体像 | [index.html](index.html) | [report.md](report.md) |
| 1 | 7mimi-agent のセキュリティ設計を読む | 全体像 | [security-design-guide.html](security-design-guide.html) | [security-design-guide.md](security-design-guide.md) |
| 2 | claude-proxy と auth-proxy を読む | Go・HTTP 入門 | [proxy-guide.html](proxy-guide.html) | [proxy-guide.md](proxy-guide.md) |
| 3 | MCP と JSON-RPC 入門 | プロトコル | [mcp-guide.html](mcp-guide.html) | [mcp-guide.md](mcp-guide.md) |
| 4 | 自律 digest パイプラインを読む | Python コード | [digest-pipeline-guide.html](digest-pipeline-guide.html) | [digest-pipeline-guide.md](digest-pipeline-guide.md) |
| 5 | scheduler と cron を読む | 自律実行の時計 | [scheduler-guide.html](scheduler-guide.html) | [scheduler-guide.md](scheduler-guide.md) |
| 6 | CLAUDE.md と .claude/ を読む | 開発の委譲 | [claude-config-guide.html](claude-config-guide.html) | [claude-config-guide.md](claude-config-guide.md) |
| 7 | ポリグロット設計と ADR を読む | 設計判断 | [polyglot-adr-guide.html](polyglot-adr-guide.html) | [polyglot-adr-guide.md](polyglot-adr-guide.md) |

---

## 各編の概要

**0. 自律 AI エージェント基盤 7mimi-agent における「信頼しない設計」の実装** — プロジェクト全体を俯瞰する技術レポート。設計動機・動作概要から、アーキテクチャ・境界サービス・防御機構・開発プロセス・運用・既知の課題までを通して報告する。まずはここから。

**1. 7mimi-agent のセキュリティ設計を読む** — 「AI を信頼しない設計」の総論。脅威モデル、credential 分離、多層防御、egress 強制、決定的認可、prompt injection 対策、コスト制御、監査を通史的に。

**2. claude-proxy と auth-proxy を読む** — 2 つの境界サービスを完全に読み解く。設定・監査・中継・認証注入・リバースプロキシ・短命トークン。Go/HTTP の前提知識ゼロから。

**3. MCP と JSON-RPC 入門** — auth-proxy の `/mcp` を教材に、Model Context Protocol と JSON-RPC 2.0 を解説。ツールの宣言・認可・実行、Claude Code の直結接続。

**4. 自律 digest パイプラインを読む** — 収集(MCP 直結)→ LLM 執筆 → 検証 → 公開(git relay / Slack)の一連を、runner の Python コードを引用して追う。

**5. scheduler と cron を読む** — cron パーサ・スケジューラエンジン・耐障害ループ。決定的な時計が非決定的な AI を定時に起動する仕組み。

**6. CLAUDE.md と .claude/ を読む** — AI エージェントに開発作業を委ねる仕組み。仕様駆動・委譲ルール・スキル・サブエージェント・ADR 強制フック。

**7. ポリグロット設計と ADR を読む** — なぜ Python と Go に分けたのか。両言語の境界の「契約」、そして設計判断を残す ADR の仕組みと歴史。

---

*7mimi(しちみみ)。HTML 版はすべてライト/ダーク両対応で、右上のボタンで切り替えられる。*
