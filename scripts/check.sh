#!/usr/bin/env bash
# The one definition of "the checks". The git hooks (.githooks/) and CI
# (.github/workflows/ci.yml) both call this, so what passes here passes there.
#
#   scripts/check.sh                 lint + test + build
#   scripts/check.sh lint test       pick steps: lint test build audit secrets staged all
#
#   staged   the pre-commit set: only the files being committed, quick
#
# Locally a tool that is missing is skipped with a warning (gitleaks, pip-audit);
# in CI (CI=true) a missing tool is a failure.
set -euo pipefail
cd "$(dirname "$0")/.."

in_ci() { [[ "${CI:-}" == "true" ]]; }
say() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }

# The repo's venv when there is one; CI installs into the runner's Python.
if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi

missing() {  # missing <what> <how to get it>
  if in_ci; then echo "✗ $1 is required in CI" >&2; exit 1; fi
  warn "$1 not found — skipped ($2)"
}

has_py_module() { "$PY" -c "import $1" 2>/dev/null; }

need_node_modules() {
  [[ -d frontend/node_modules ]] || { echo "✗ frontend/node_modules is missing — run: (cd frontend && npm ci)" >&2; exit 1; }
}

ruff_check() {
  has_py_module ruff || { echo "✗ ruff is missing — run: $PY -m pip install -r backend/requirements-dev.txt" >&2; exit 1; }
  if in_ci; then "$PY" -m ruff check --output-format=github "$@"; else "$PY" -m ruff check "$@"; fi
}

step_lint() {
  say "ruff"
  ruff_check .
  need_node_modules
  say "eslint"
  (cd frontend && npx --no-install eslint .)
  say "undefined names in the SFCs"
  (cd frontend && npm run -s check)
}

step_test() {
  say "pytest"
  "$PY" -m pytest -q
}

step_build() {
  need_node_modules
  say "frontend build"
  (cd frontend && npm run -s build)
}

step_audit() {
  say "pip-audit"
  if has_py_module pip_audit; then
    "$PY" -m pip_audit -r backend/requirements.txt
  else
    missing pip-audit "$PY -m pip install -r backend/requirements-dev.txt"
  fi
  say "npm audit (what ships)"
  (cd frontend && npm audit --omit=dev --audit-level=high)
}

step_secrets() {
  say "gitleaks (whole history)"
  if command -v gitleaks >/dev/null; then
    gitleaks git --redact --no-banner .
  else
    missing gitleaks "https://github.com/gitleaks/gitleaks/releases"
  fi
}

# Pre-commit: only what is being committed, so a commit does not wait on the suite.
# (It reads the files as they are in the working tree.)
step_staged() {
  local py=() fe=()
  while IFS= read -r -d '' f; do py+=("$f"); done \
    < <(git diff --cached --name-only --diff-filter=ACMR -z -- '*.py')
  while IFS= read -r -d '' f; do fe+=("${f#frontend/}"); done \
    < <(git diff --cached --name-only --diff-filter=ACMR -z -- 'frontend/*.vue' 'frontend/*.js' 'frontend/*.mjs')

  if ((${#py[@]})); then
    say "ruff (${#py[@]} staged)"
    ruff_check --force-exclude "${py[@]}"
  fi
  if ((${#fe[@]})); then
    need_node_modules
    say "eslint (${#fe[@]} staged)"
    (cd frontend && npx --no-install eslint --no-warn-ignored "${fe[@]}")
  fi
  say "gitleaks (staged)"
  if command -v gitleaks >/dev/null; then
    gitleaks git --pre-commit --staged --redact --no-banner .
  else
    missing gitleaks "https://github.com/gitleaks/gitleaks/releases"
  fi
  say "release hygiene (no names, addresses or model ids in git)"
  "$PY" -m pytest -q -p no:cacheprovider tests/test_release_hygiene.py
}

steps=("$@")
((${#steps[@]})) || steps=(lint test build)
for s in "${steps[@]}"; do
  case "$s" in
    lint|test|build|audit|secrets|staged) "step_$s" ;;
    all) step_lint; step_test; step_build; step_audit; step_secrets ;;
    *) echo "unknown step: $s (lint test build audit secrets staged all)" >&2; exit 2 ;;
  esac
done
say "ok"
