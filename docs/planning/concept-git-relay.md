# Concept: Git Smart HTTP Relay(runner からの直接 git 操作)

Status: PM/TL レビュー承認済みコンセプト(2026-07-05)。実装着手時に `/new-spec` で issue 化し、ADR を追記すること。

参考: [Mercari Engineering — pcp-agent のセキュリティ設計](https://engineering.mercari.com/blog/entry/20260630-28a5eee688/)

## Problem Statement

agent-runner 内の Claude が git 操作(clone/fetch/push)を、許可されたリポジトリに対して直接実行できる汎用基盤を作る。credential は runner に一切置かず、git の HTTP(S) を auth-proxy 経由に強制し、proxy 側で認証と token 注入を行う(Mercari pcp-agent 方式)。ADR-018 の暫定ホスト publish(`DocumentRepositoryWriter.publish`)を置き換える。

## Chosen Concept: Git Smart HTTP 中継

auth-proxy(Go)に `/git/{owner}/{repo}/…` を追加し、smart HTTP の 3 エンドポイント(`info/refs`、`git-upload-pack`=read、`git-receive-pack`=write)をプロトコル互換でリバースプロキシする。runner は remote URL を proxy に向けるだけで素の git がそのまま動く。

検討済み代替案: git 操作の tool 化(`git.push` 等)は path 単位検査が可能だが、素の git ワークフロー(rebase/conflict 解消)を殺すため不採用。

## Core Workflow

1. orchestrator が runner env に `GIT_PROXY_URL` とセッション token のみ渡す。git への注入は `GIT_CONFIG_*` env(`http.<proxy>.extraHeader=Authorization: Bearer <session>`)+ `GIT_TERMINAL_PROMPT=0` + `credential.helper=`(空)で、ディスク・URL に秘密を書かない
2. runner 内の Claude が clone → 編集 → commit → push
3. auth-proxy: セッション Bearer 検証 → GitHub App の installation access token(短命)を注入して GitHub へ中継 → metadata のみ監査ログ
4. **repo 制限は credential scope で強制**: App の installation 対象外 repo への操作は GitHub が 404/403 を返す。proxy 側での repo×操作 ACL 判定は行わない(判定ロジックではなく token scope が強制点 — Mercari と同方式)
5. proxy 停止時: runner は credential を持たないため GitHub に到達する手段がない(fail-closed)

## Credential 設計(GitHub App)

- GitHub App(Contents: Read and write)を一度だけ手動作成。private key は auth-proxy の env/ファイルのみに配置(runner・リポジトリに置かない)
- auth-proxy が実行時に App JWT(RS256)→ `POST /app/installations/{id}/access_tokens` で installation token を mint(TTL 1h、残 5 分で再発行のキャッシュ)。漏洩時の被害は「1 時間 × installation 対象 repo」に自動限定
- **installation 対象 repo の管理**: 当面は GitHub UI での手動管理。将来は別 private repo `7milch/terraform` を作成し、`github_app_installation_repository` リソースで IaC 管理する想定(このリポジトリには Terraform を置かない)
- MVP の installation 対象は `7milch/ai-it-research-notes` のみ。repo 追加は named consumer が現れてから

## Design Pillars

1. **Credential-free runner** — dummy 値すら置かない。認証は 100% proxy 側
2. **Deterministic enforcement, outside the LLM** — 強制点は credential scope(installation 対象)とセッション検証。CLAUDE.md は誘導、強制は権限層
3. **素の git を殺さない** — プロトコル互換の透過中継。Claude Code の通常 git ワークフローがそのまま動く

## Anti-goals(Out of Scope)

- GitHub 以外の git ホスティング対応
- pack 内容の path/branch 検査(branch protection は GitHub 側の責務)
- proxy 側の role 別 repo×操作 ACL(`DecideGit`)— 複数 role で権限差を付ける要件が出るまで実装しない(単一 credential scope で十分)
- Terraform IaC の同居(`7milch/terraform` 側で別途管理予定。それまで手動)

## Supersession(PM 承認条件)

- 実装 ADR は **ADR-018 を supersede** する: 縦切り成功後、ホスト側 `--publish` 経路(`DocumentRepositoryWriter.publish`)は廃止し、書き込み制御点を proxy 経路に一本化する
- `config/policy.yaml` の `git push` deny パターン(runner のローカル git push 禁止)と整合させること: relay 経由の push が唯一の許可経路になるよう deny/allow を再定義する

## Technical Notes(TL 承認条件)

- `httputil.ReverseProxy` + custom Director。**`FlushInterval = -1`**(pack negotiation の ping-pong が既定バッファリングでハングするため)
- **全体 `http.Client{Timeout}` を使わない**(大きな clone/fetch が途中で殺される)。Transport の connect/TLS/response-header timeout のみ設定し、body copy は context キャンセルに委ねる
- redirect 対策(credential echo 防止): Director で upstream パスを常に `.git` 正規化 + `ModifyResponse` で 3xx の Location を検証し cross-host redirect をブロック
- `Git-Protocol: version=2` ヘッダを透過(落とすと v0 に silent downgrade)。gzip / Content-Type(`application/x-git-*`)は無変換で透過
- 実装は新規 `internal/gitrelay` パッケージ(`internal/tools` の JSON 判定ハンドラと同居させない)。audit は `internal/audit` を再利用
- GitHub への認証注入は `Authorization: Basic base64("x-access-token:" + token)`
- Authorization ヘッダ・Basic 値・App private key・installation token は絶対にログしない(metadata-only 監査の原則)

## MVP 定義

1. auth-proxy に gitrelay(read+write 透過中継、セッション検証、token mint、redirect scrubbing、監査)
2. GitHub App 手動セットアップ手順の README 追記
3. runner env 配線(`GIT_PROXY_URL` + `GIT_CONFIG_*` 注入)
4. E2E: runner コンテナ内の git が notes repo を clone → digest commit → push まで通ること
5. 成功後: ADR-018 経路の廃止(同 issue または直後の issue)

## Review 結果

- `[PM-SCOPE]: APPROVE`(条件 3 点: ADR-018 supersede / interface 汎用・有効化は notes repo のみ / token 方式の明示 — いずれも本文書に反映済み。その後の GitHub App 化により「短命 token 後回し」の負債自体が消滅)
- `[TL-FEASIBILITY]: APPROVE`(実装条件は Technical Notes に反映)
