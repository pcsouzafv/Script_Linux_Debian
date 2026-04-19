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
EVOLUTION_INSTANCE_TOKEN="${EVOLUTION_INSTANCE_TOKEN:-}"
EVOLUTION_MSG_CALL="${EVOLUTION_MSG_CALL:-Este numero atende apenas automacao de suporte.}"
EVOLUTION_CONNECTED_NAME="${EVOLUTION_CONNECTED_NAME:-}"
EVOLUTION_CONNECTED_JID="${EVOLUTION_CONNECTED_JID:-}"
EVOLUTION_PUBLIC_NUMBER="${EVOLUTION_PUBLIC_NUMBER:-}"

print_connection_identity() {
    if [[ -n "${EVOLUTION_CONNECTED_NAME}" ]]; then
        echo "Conectado como: ${EVOLUTION_CONNECTED_NAME}"
    fi

    if [[ -n "${EVOLUTION_CONNECTED_JID}" ]]; then
        echo "JID conectado: ${EVOLUTION_CONNECTED_JID}"
    fi

    if [[ -n "${EVOLUTION_PUBLIC_NUMBER}" ]]; then
        echo "Numero publico: ${EVOLUTION_PUBLIC_NUMBER}"
    fi
}

discover_global_apikey() {
    docker inspect evolution-api --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null |
        sed -n 's/^AUTHENTICATION_API_KEY=//p' |
        head -n 1
}

if [[ -z "${EVOLUTION_GLOBAL_APIKEY}" ]] && command -v docker >/dev/null 2>&1; then
    EVOLUTION_GLOBAL_APIKEY="$(discover_global_apikey || true)"
fi

if [[ -z "${EVOLUTION_GLOBAL_APIKEY}" ]]; then
    echo "Nao foi possivel resolver EVOLUTION_GLOBAL_APIKEY." >&2
    echo "Preencha infra/evolution/.env ou exporte a variavel antes de executar." >&2
    exit 1
fi

existing_instance="$(
    curl -sS \
        -H "apikey: ${EVOLUTION_GLOBAL_APIKEY}" \
        "${EVOLUTION_BASE_URL}/instance/fetchInstances?instanceName=${EVOLUTION_INSTANCE_NAME}"
)"

if printf '%s' "${existing_instance}" | grep -q "\"name\":\"${EVOLUTION_INSTANCE_NAME}\""; then
    echo "Instancia ${EVOLUTION_INSTANCE_NAME} ja existe."
    printf '%s\n' "${existing_instance}"
    print_connection_identity
    exit 0
fi

payload="$(cat <<EOF
{
  "instanceName": "${EVOLUTION_INSTANCE_NAME}",
  "integration": "WHATSAPP-BAILEYS",
  "token": "${EVOLUTION_INSTANCE_TOKEN}",
  "qrcode": true,
  "rejectCall": true,
  "msgCall": "${EVOLUTION_MSG_CALL}",
  "groupsIgnore": true,
  "alwaysOnline": true,
  "readMessages": false,
  "readStatus": true,
  "syncFullHistory": false
}
EOF
)"

response_file="$(mktemp)"
http_code="$(
    curl -sS \
        -o "${response_file}" \
        -w '%{http_code}' \
        -X POST "${EVOLUTION_BASE_URL}/instance/create" \
        -H "Content-Type: application/json" \
        -H "apikey: ${EVOLUTION_GLOBAL_APIKEY}" \
        -d "${payload}"
)"

if [[ "${http_code}" != "201" ]]; then
    echo "Falha ao criar a instancia ${EVOLUTION_INSTANCE_NAME}." >&2
    cat "${response_file}" >&2
    rm -f "${response_file}"
    exit 1
fi

cat "${response_file}"
rm -f "${response_file}"

echo
echo "Instancia criada: ${EVOLUTION_INSTANCE_NAME}"
echo "Manager: ${EVOLUTION_BASE_URL}/manager/"
print_connection_identity
