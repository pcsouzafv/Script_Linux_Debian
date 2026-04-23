#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$LAB_DIR/.env"
PROFILE="${1:-full}"

trim_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

load_env_file() {
    local line key value

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" != *=* ]] && continue

        key="$(trim_whitespace "${line%%=*}")"
        value="${line#*=}"
        value="${value%$'\r'}"

        if [[ "$value" =~ ^\".*\"$ || "$value" =~ ^\'.*\'$ ]]; then
            value="${value:1:-1}"
        fi

        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
}

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Arquivo .env ausente. Rode ./scripts/prepare.sh primeiro." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker nao encontrado no PATH." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Docker nao esta acessivel para o usuario atual." >&2
    exit 1
fi

load_env_file

check_port_free() {
    local port="$1"
    if ss -ltnH "( sport = :$port )" | grep -q .; then
        echo "Porta $port ja esta em uso no host." >&2
        return 1
    fi
    return 0
}

case "$PROFILE" in
    glpi)
        check_port_free "${GLPI_HOST_PORT}" || exit 1
        ;;
    zabbix)
        check_port_free "${ZABBIX_WEB_HOST_PORT}" || exit 1
        check_port_free "${GRAFANA_HOST_PORT}" || exit 1
        ;;
    ops)
        check_port_free "${POSTGRES_HOST_PORT}" || exit 1
        check_port_free "${REDIS_HOST_PORT}" || exit 1
        ;;
    full|all)
        check_port_free "${GLPI_HOST_PORT}" || exit 1
        check_port_free "${ZABBIX_WEB_HOST_PORT}" || exit 1
        check_port_free "${GRAFANA_HOST_PORT}" || exit 1
        check_port_free "${POSTGRES_HOST_PORT}" || exit 1
        check_port_free "${REDIS_HOST_PORT}" || exit 1
        ;;
    *)
        echo "Uso: ./scripts/preflight.sh [glpi|zabbix|ops|full]" >&2
        exit 1
        ;;
esac

echo "Preflight OK."

case "$PROFILE" in
    glpi)
        echo "GLPI usara 127.0.0.1:${GLPI_HOST_PORT}"
        ;;
    zabbix)
        echo "Zabbix usara 127.0.0.1:${ZABBIX_WEB_HOST_PORT}"
        echo "Grafana usara 127.0.0.1:${GRAFANA_HOST_PORT}"
        ;;
    ops)
        echo "PostgreSQL operacional usara 127.0.0.1:${POSTGRES_HOST_PORT}"
        echo "Redis operacional usara 127.0.0.1:${REDIS_HOST_PORT}"
        ;;
    full|all)
        echo "GLPI usara 127.0.0.1:${GLPI_HOST_PORT}"
        echo "Zabbix usara 127.0.0.1:${ZABBIX_WEB_HOST_PORT}"
        echo "Grafana usara 127.0.0.1:${GRAFANA_HOST_PORT}"
        echo "PostgreSQL operacional usara 127.0.0.1:${POSTGRES_HOST_PORT}"
        echo "Redis operacional usara 127.0.0.1:${REDIS_HOST_PORT}"
        ;;
esac
