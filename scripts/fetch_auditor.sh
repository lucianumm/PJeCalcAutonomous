#!/usr/bin/env bash
# Wrapper multiplataforma; a revisão deve ser fixada por AUDITORPROCESSUAL_REF.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python "$ROOT/scripts/fetch_auditor.py" --project "$ROOT" "$@"
