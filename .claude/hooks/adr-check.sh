#!/bin/bash
# Stop hook: block completion when design changes lack an ADR update.
# Design-relevant paths: docs/architecture, docs/detailed-design, docs/workflows, config/*.yaml
input=$(cat)

# Prevent infinite loop: if we already blocked once this turn, let Claude stop.
if [ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ]; then
  exit 0
fi

root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0

# Changes in this work window: uncommitted diff + commits from the last 30 minutes.
changed=$(
  {
    git diff --name-only HEAD -- 2>/dev/null
    git log --since="30 minutes ago" --name-only --pretty=format: 2>/dev/null
  } | sort -u | sed '/^$/d'
)

design=$(printf '%s\n' "$changed" | grep -E '^(docs/(architecture|detailed-design|workflows)/|config/[^/]+\.ya?ml$)')

if [ -n "$design" ] && ! printf '%s\n' "$changed" | grep -q '^docs/planning/adr\.md$'; then
  jq -n --arg files "$(printf '%s' "$design" | head -10)" '{
    decision: "block",
    reason: ("設計変更が検出されましたが docs/planning/adr.md が更新されていません。\n変更ファイル:\n" + $files + "\n\n設計判断を伴う変更なら ADR を追記してください(採番は既存の最終ADRの次番号)。単なる誤字修正や既存ADRの範囲内であれば、その旨をユーザーに一言伝えてから完了してください。")
  }'
fi
exit 0
