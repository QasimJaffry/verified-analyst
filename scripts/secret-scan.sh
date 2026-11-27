#!/usr/bin/env bash
# Fails if a tracked file contains something shaped like an API key. Patterns: OpenAI/OpenRouter (sk-...), Groq (gsk_...), Google (AIza...), GitHub tokens.
set -u
if git grep -n -I -E 'sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}' -- . ':!scripts/secret-scan.sh'; then
  echo "secret-scan: a key-shaped string is committed. Revoke that key at the provider, then remove it." >&2
  exit 1
fi
echo "secret-scan: clean"
