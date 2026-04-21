#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$LAB_DIR/../.." && pwd)"
LAB_ENV_FILE="$LAB_DIR/.env"
BACKEND_ENV_FILE="$ROOT_DIR/backend/.env"
BACKEND_ENV_EXAMPLE="$ROOT_DIR/backend/.env.example"

load_lab_env() {
    if [[ ! -f "$LAB_ENV_FILE" ]]; then
        echo "Arquivo ausente: $LAB_ENV_FILE" >&2
        exit 1
    fi

    set -a
    # shellcheck disable=SC1090
    source "$LAB_ENV_FILE"
    set +a
}

require_commands() {
    local cmd
    for cmd in "$@"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "Comando obrigatorio nao encontrado: $cmd" >&2
            exit 1
        fi
    done
}

compose() {
    (
        cd "$LAB_DIR"
        docker compose "$@"
    )
}

ensure_service_running() {
    local service="$1"
    if ! compose ps --status running --services | grep -Fx "$service" >/dev/null 2>&1; then
        echo "Servico do laboratorio nao esta em execucao: $service" >&2
        exit 1
    fi
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\\/&]/\\&/g'
}

urlencode() {
    jq -rn --arg value "$1" '$value|@uri'
}

upsert_env_key() {
    local file="$1"
    local key="$2"
    local value="$3"
    local escaped_value

    escaped_value="$(escape_sed_replacement "$value")"

    if [[ ! -f "$file" ]]; then
        touch "$file"
    fi

    if grep -q "^${key}=" "$file"; then
        sed -i "s/^${key}=.*/${key}=${escaped_value}/" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

ensure_backend_env_file() {
    if [[ ! -f "$BACKEND_ENV_FILE" ]]; then
        cp "$BACKEND_ENV_EXAMPLE" "$BACKEND_ENV_FILE"
    fi
}
