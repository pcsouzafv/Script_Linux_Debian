#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
VENV_UVICORN="$SCRIPT_DIR/.venv/bin/uvicorn"

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

if [[ ! -x "$VENV_PYTHON" || ! -x "$VENV_UVICORN" ]]; then
    echo "Ambiente virtual não encontrado em backend/.venv. Instale as dependências antes de subir a API." >&2
    exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
    load_env_file
fi

HOST="${HELPDESK_API_HOST:-127.0.0.1}"
BASE_PORT="${HELPDESK_API_PORT:-18001}"
MAX_PORT="${HELPDESK_API_PORT_MAX:-18010}"
STRICT_MODE="${HELPDESK_API_PORT_STRICT:-false}"

is_port_available() {
    "$VENV_PYTHON" - "$HOST" "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

select_port() {
    local port="$BASE_PORT"

    if is_port_available "$port"; then
        echo "$port"
        return 0
    fi

    if [[ "$STRICT_MODE" == "true" ]]; then
        echo "A porta configurada $port já está em uso e o modo estrito está habilitado." >&2
        exit 1
    fi

    while (( port < MAX_PORT )); do
        port=$((port + 1))
        if is_port_available "$port"; then
            echo "$port"
            return 0
        fi
    done

    echo "Nenhuma porta livre encontrada na faixa ${BASE_PORT}-${MAX_PORT}." >&2
    exit 1
}

SELECTED_PORT="$(select_port)"

if [[ "${1:-}" == "--dry-run" ]]; then
    echo "${HOST}:${SELECTED_PORT}"
    exit 0
fi

if [[ "$SELECTED_PORT" != "$BASE_PORT" ]]; then
    echo "Porta ${BASE_PORT} ocupada. Subindo API em ${HOST}:${SELECTED_PORT}."
else
    echo "Subindo API em ${HOST}:${SELECTED_PORT}."
fi

cd "$SCRIPT_DIR"
exec "$VENV_UVICORN" app.main:app --reload --host "$HOST" --port "$SELECTED_PORT" "$@"