#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

load_lab_env
require_commands docker curl jq
ensure_service_running db
ensure_service_running glpi
ensure_service_running zabbix-server
ensure_service_running zabbix-web
ensure_backend_env_file

compose exec -T db mysql -uroot -p"$LAB_DB_ROOT_PASSWORD" <<SQL
UPDATE ${GLPI_DB_NAME}.glpi_configs
SET value = '1'
WHERE context = 'core'
  AND name IN ('enable_api', 'enable_api_login_credentials');

INSERT INTO ${GLPI_DB_NAME}.glpi_apiclients (
    entities_id,
    is_recursive,
    name,
    is_active,
    ipv4_range_start,
    ipv4_range_end,
    dolog_method,
    comment
)
SELECT
    0,
    1,
    'helpdesk-lab-docker-bridge',
    1,
    INET_ATON('172.16.0.0'),
    INET_ATON('172.31.255.255'),
    0,
    'Permite acesso do host Docker ao laboratorio local'
WHERE NOT EXISTS (
    SELECT 1
    FROM ${GLPI_DB_NAME}.glpi_apiclients
    WHERE name = 'helpdesk-lab-docker-bridge'
);
SQL

GLPI_SESSION="$(
    curl -sS \
        -H 'Content-Type: application/json' \
        -H 'Authorization: Basic Z2xwaTpnbHBp' \
        "http://127.0.0.1:${GLPI_HOST_PORT}/apirest.php/initSession" |
    jq -r '.session_token // empty'
)"

if [[ -z "$GLPI_SESSION" ]]; then
    echo "Falha ao validar a API do GLPI apos bootstrap." >&2
    exit 1
fi

curl -sS \
    -H 'Content-Type: application/json' \
    -H "Session-Token: $GLPI_SESSION" \
    "http://127.0.0.1:${GLPI_HOST_PORT}/apirest.php/killSession" >/dev/null || true

ZABBIX_AUTH="$(
    curl -sS -X POST \
        "http://127.0.0.1:${ZABBIX_WEB_HOST_PORT}/api_jsonrpc.php" \
        -H 'Content-Type: application/json-rpc' \
        -d '{"jsonrpc":"2.0","method":"user.login","params":{"username":"Admin","password":"zabbix"},"id":1}' |
    jq -r '.result // empty'
)"

if [[ -z "$ZABBIX_AUTH" ]]; then
    echo "Falha ao validar autenticacao no Zabbix apos bootstrap." >&2
    exit 1
fi

ENCODED_POSTGRES_USER="$(urlencode "$OPS_POSTGRES_USER")"
ENCODED_POSTGRES_PASSWORD="$(urlencode "$OPS_POSTGRES_PASSWORD")"
ENCODED_REDIS_PASSWORD="$(urlencode "$OPS_REDIS_PASSWORD")"

upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_PROVIDER" "glpi"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_STORE_PATH" "data/identities.lab.json"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_GLPI_USER_PROFILES" "Self-Service"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_GLPI_TECHNICIAN_PROFILES" "Technician"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES" "Super-Admin"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES" ""
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_GLPI_BASE_URL" "http://127.0.0.1:${GLPI_HOST_PORT}/apirest.php"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_GLPI_APP_TOKEN" ""
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_GLPI_USER_TOKEN" ""
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_GLPI_USERNAME" "glpi"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_GLPI_PASSWORD" "glpi"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_ZABBIX_BASE_URL" "http://127.0.0.1:${ZABBIX_WEB_HOST_PORT}/api_jsonrpc.php"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_ZABBIX_API_TOKEN" ""
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_ZABBIX_USERNAME" "Admin"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_ZABBIX_PASSWORD" "zabbix"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_OPERATIONAL_POSTGRES_DSN" "postgresql://${ENCODED_POSTGRES_USER}:${ENCODED_POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${OPS_POSTGRES_DB}"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_OPERATIONAL_POSTGRES_SCHEMA" "$OPS_POSTGRES_SCHEMA"
upsert_env_key "$BACKEND_ENV_FILE" "HELPDESK_REDIS_URL" "redis://:${ENCODED_REDIS_PASSWORD}@127.0.0.1:${REDIS_HOST_PORT}/0"

echo "Integracoes do laboratorio bootstrapadas com sucesso."
echo "GLPI API: http://127.0.0.1:${GLPI_HOST_PORT}/apirest.php"
echo "Zabbix API: http://127.0.0.1:${ZABBIX_WEB_HOST_PORT}/api_jsonrpc.php"
echo "PostgreSQL operacional: postgresql://${OPS_POSTGRES_USER}:***@127.0.0.1:${POSTGRES_HOST_PORT}/${OPS_POSTGRES_DB}"
echo "Redis operacional: redis://:***@127.0.0.1:${REDIS_HOST_PORT}/0"
echo "Backend .env alinhado para usar o laboratorio."
echo "Reinicie o backend para recarregar as credenciais."
