#!/bin/bash
# Public steps for the existing v0.1.0 release: the
# release description, the repository About text and topics. Prints the
# commands by default; --apply runs them (needs `gh auth login` and the
# owner's consent). Merging into main and the CI check of the published
# commit are separate owner steps and are not done here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REPO="${REPO:-wpartscom/bolorgir}"
ABOUT="Embeddable structured-generation engine for LLMs: a small Zig core with a versioned C ABI, Python bindings, and a Hugging Face Transformers adapter"
TOPICS=(zig llm structured-output constrained-decoding json-schema python)

cmds=(
  "gh release edit v0.1.0 --repo $REPO --notes-file \"$ROOT/docs/release/v0.1.0.md\""
  "gh repo edit $REPO --description \"$ABOUT\""
)
for t in "${TOPICS[@]}"; do
  cmds+=("gh repo edit $REPO --add-topic $t")
done

if [ "${1:-}" != "--apply" ]; then
  printf '%s\n' "${cmds[@]}"
  echo "# dry run; pass --apply to execute" >&2
  exit 0
fi
gh auth status
gh release edit v0.1.0 --repo "$REPO" --notes-file "$ROOT/docs/release/v0.1.0.md"
gh repo edit "$REPO" --description "$ABOUT"
for t in "${TOPICS[@]}"; do
  gh repo edit "$REPO" --add-topic "$t"
done
gh release view v0.1.0 --repo "$REPO" --json body -q .body | head -n 5
gh repo view "$REPO" --json description,repositoryTopics
