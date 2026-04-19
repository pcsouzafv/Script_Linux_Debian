#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$LAB_DIR"

if [[ "${1:-}" == "--volumes" ]]; then
    docker compose down -v
    exit 0
fi

docker compose down

