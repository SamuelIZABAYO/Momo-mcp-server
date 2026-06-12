#!/usr/bin/env bash
# Fail if anything that looks like a committed secret is in the tracked tree
# Run in CI on every push and locally pre-commit.
set -euo pipefail

cd "$(dirname "$0")/.."

fail=0

# 1) A committed .env is an instant fail.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked by git. It must never be committed." >&2
  fail=1
fi

# 2) Grep tracked files for secret-shaped assignments with a real value.
#    Allow empty assignments (KEY=) and the .env.example values.
#    Patterns: non-empty MOMO_*_KEY / API key assignments to a literal value.
#    Excludes f-string interpolations (={var}) — those print runtime values,
#    not committed secrets — and the example/scanner/docs files.
pattern='(MOMO_[A-Z_]*KEY|MOMO_API_(USER|KEY))=[^[:space:]{"'"'"']+'
matches=$(git grep -nIE "$pattern" -- ':!.env.example' ':!scripts/check_secrets.sh' ':!docs/**' || true)
if [ -n "$matches" ]; then
  echo "ERROR: possible secret value committed:" >&2
  echo "$matches" >&2
  fail=1
fi

# 3) Bare 32-hex tokens anywhere in source (subscription-key shaped).
hexmatches=$(git grep -nIE '\b[0-9a-f]{32}\b' -- 'src' 'scripts' ':!scripts/check_secrets.sh' || true)
if [ -n "$hexmatches" ]; then
  echo "ERROR: 32-hex token (subscription-key shaped) found in source:" >&2
  echo "$hexmatches" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "Secret scan FAILED." >&2
  exit 1
fi
echo "Secret scan passed: no committed secrets detected."
