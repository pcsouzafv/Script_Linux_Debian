#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILE="${1:-full}"

case "$PROFILE" in
    glpi)
        services=(db glpi)
        ;;
    zabbix)
        services=(db zabbix-server zabbix-web)
        ;;
    full|all)
        PROFILE="full"
        services=(db glpi zabbix-server zabbix-web)
        ;;
    *)
        echo "Uso: ./scripts/pull.sh [glpi|zabbix|full]" >&2
        exit 1
        ;;
esac

cd "$LAB_DIR"
docker compose --profile "$PROFILE" pull "${services[@]}"

