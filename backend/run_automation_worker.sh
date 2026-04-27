#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

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

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Ambiente virtual não encontrado em backend/.venv. Instale as dependências antes de subir o worker." >&2
    exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
    load_env_file
fi

cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" -m app.workers.automation_worker "$@"
cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" -m app.workers.automation_worker
