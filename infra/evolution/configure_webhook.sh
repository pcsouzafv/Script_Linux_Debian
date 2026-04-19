#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

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

if [[ -f "${ENV_FILE}" ]]; then
    load_env_file
fi

EVOLUTION_BASE_URL="${EVOLUTION_BASE_URL:-http://localhost:8080}"
EVOLUTION_GLOBAL_APIKEY="${EVOLUTION_GLOBAL_APIKEY:-}"
EVOLUTION_INSTANCE_NAME="${EVOLUTION_INSTANCE_NAME:-helpdeskAutomacao}"
EVOLUTION_WEBHOOK_URL="${1:-${EVOLUTION_WEBHOOK_URL:-}}"
EVOLUTION_WEBHOOK_SECRET="${EVOLUTION_WEBHOOK_SECRET:-}"
EVOLUTION_WEBHOOK_SECRET_HEADER="${EVOLUTION_WEBHOOK_SECRET_HEADER:-X-Evolution-Webhook-Secret}"
EVOLUTION_WEBHOOK_EVENTS="${EVOLUTION_WEBHOOK_EVENTS:-MESSAGES_UPSERT}"
EVOLUTION_WEBHOOK_BY_EVENTS="${EVOLUTION_WEBHOOK_BY_EVENTS:-false}"
EVOLUTION_WEBHOOK_BASE64="${EVOLUTION_WEBHOOK_BASE64:-false}"

discover_global_apikey() {
    docker inspect evolution-api --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null |
        sed -n 's/^AUTHENTICATION_API_KEY=//p' |
        head -n 1
}

escape_json() {
    local value="$1"

    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "${value}"
}

csv_to_json_array() {
    local csv="$1"
    local item
    local out=""

    IFS=',' read -r -a items <<< "${csv}"
    for item in "${items[@]}"; do
        item="$(trim_whitespace "${item}")"
        if [[ -z "${item}" ]]; then
            continue
        fi
        out+="${out:+,}\"$(escape_json "${item}")\""
    done

    printf '[%s]' "${out}"
}

validate_boolean() {
    local label="$1"
    local value="$2"

    case "${value}" in
        true|false) ;;
        *)
            echo "${label} deve ser true ou false." >&2
            exit 1
            ;;
    esac
}

if [[ -z "${EVOLUTION_GLOBAL_APIKEY}" ]] && command -v docker >/dev/null 2>&1; then
    EVOLUTION_GLOBAL_APIKEY="$(discover_global_apikey || true)"
fi

if [[ -z "${EVOLUTION_GLOBAL_APIKEY}" ]]; then
    echo "Nao foi possivel resolver EVOLUTION_GLOBAL_APIKEY." >&2
    echo "Preencha infra/evolution/.env ou exporte a variavel antes de executar." >&2
    exit 1
fi

if [[ -z "${EVOLUTION_WEBHOOK_URL}" ]]; then
    echo "Informe a URL do webhook como argumento ou em EVOLUTION_WEBHOOK_URL." >&2
    exit 1
fi

if [[ ! "${EVOLUTION_WEBHOOK_URL}" =~ ^https?:// ]]; then
    echo "EVOLUTION_WEBHOOK_URL deve comecar com http:// ou https://." >&2
    exit 1
fi

validate_boolean "EVOLUTION_WEBHOOK_BY_EVENTS" "${EVOLUTION_WEBHOOK_BY_EVENTS}"
validate_boolean "EVOLUTION_WEBHOOK_BASE64" "${EVOLUTION_WEBHOOK_BASE64}"

events_json="$(csv_to_json_array "${EVOLUTION_WEBHOOK_EVENTS}")"
if [[ "${events_json}" == '[]' ]]; then
    echo "EVOLUTION_WEBHOOK_EVENTS nao pode ficar vazio." >&2
    exit 1
fi

headers_json='{}'
if [[ -n "${EVOLUTION_WEBHOOK_SECRET}" ]]; then
    headers_json="{\"$(escape_json "${EVOLUTION_WEBHOOK_SECRET_HEADER}")\":\"$(escape_json "${EVOLUTION_WEBHOOK_SECRET}")\"}"
fi

payload="$(cat <<EOF
{
  "webhook": {
    "enabled": true,
    "url": "$(escape_json "${EVOLUTION_WEBHOOK_URL}")",
    "events": ${events_json},
    "headers": ${headers_json},
    "byEvents": ${EVOLUTION_WEBHOOK_BY_EVENTS},
    "base64": ${EVOLUTION_WEBHOOK_BASE64}
  }
}
EOF
)"

response_file="$(mktemp)"
http_code="$(
    curl -sS \
        -o "${response_file}" \
        -w '%{http_code}' \
        -X POST "${EVOLUTION_BASE_URL%/}/webhook/set/${EVOLUTION_INSTANCE_NAME}" \
        -H 'Content-Type: application/json' \
        -H "apikey: ${EVOLUTION_GLOBAL_APIKEY}" \
        -d "${payload}"
)"

if [[ "${http_code}" != '200' && "${http_code}" != '201' ]]; then
    echo "Falha ao configurar webhook da instancia ${EVOLUTION_INSTANCE_NAME}." >&2
    cat "${response_file}" >&2
    rm -f "${response_file}"
    exit 1
fi

cat "${response_file}"
rm -f "${response_file}"

echo
echo "Webhook configurado para a instancia ${EVOLUTION_INSTANCE_NAME}."
echo "Destino: ${EVOLUTION_WEBHOOK_URL}"
echo "Eventos: ${EVOLUTION_WEBHOOK_EVENTS}"