# Open Questions

壁打ちしたい事項・未決定事項をまとめる。

## 19. Open questions / 壁打ちしたいこと

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
