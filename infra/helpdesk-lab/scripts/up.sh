#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILE="${1:-full}"

case "$PROFILE" in
    glpi|zabbix|ops|full|all)
        ;;
    *)
        echo "Uso: ./scripts/up.sh [glpi|zabbix|ops|full]" >&2
        exit 1
        ;;
esac

"$SCRIPT_DIR/prepare.sh"
"$SCRIPT_DIR/preflight.sh" "$PROFILE"

cd "$LAB_DIR"
docker compose --profile "$PROFILE" up -d

