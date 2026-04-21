#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$LAB_DIR/.env"
ENV_EXAMPLE="$LAB_DIR/.env.example"
TEMPLATE_FILE="$LAB_DIR/templates/initdb/01-bootstrap.sql.template"
RUNTIME_DIR="$LAB_DIR/runtime/initdb"
OUTPUT_FILE="$RUNTIME_DIR/01-bootstrap.sql"
POSTGRES_TEMPLATE_FILE="$LAB_DIR/templates/postgres-init/01-helpdesk-platform.sql.template"
POSTGRES_RUNTIME_DIR="$LAB_DIR/runtime/postgres-init"
POSTGRES_OUTPUT_FILE="$POSTGRES_RUNTIME_DIR/01-helpdesk-platform.sql"

trim_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

sync_missing_env_keys() {
    local line key

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" != *=* ]] && continue

        key="$(trim_whitespace "${line%%=*}")"
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            continue
        fi

        if ! grep -q "^${key}=" "$ENV_FILE"; then
            printf '%s\n' "$line" >> "$ENV_FILE"
            echo "Variavel ausente adicionada ao .env: $key"
        fi
    done < "$ENV_EXAMPLE"
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
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Arquivo .env criado a partir de .env.example. Ajuste as senhas antes de subir o laboratorio."
fi

sync_missing_env_keys

mkdir -p "$RUNTIME_DIR"
mkdir -p "$POSTGRES_RUNTIME_DIR"

load_env_file

required_vars=(
    GLPI_DB_NAME
    GLPI_DB_USER
    GLPI_DB_PASSWORD
    ZABBIX_DB_NAME
    ZABBIX_DB_USER
    ZABBIX_DB_PASSWORD
    OPS_POSTGRES_DB
    OPS_POSTGRES_USER
    OPS_POSTGRES_PASSWORD
    OPS_POSTGRES_SCHEMA
    OPS_REDIS_PASSWORD
)

for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "Variavel obrigatoria ausente em .env: $var_name" >&2
        exit 1
    fi
done

sed \
    -e "s|\${GLPI_DB_NAME}|$GLPI_DB_NAME|g" \
    -e "s|\${GLPI_DB_USER}|$GLPI_DB_USER|g" \
    -e "s|\${GLPI_DB_PASSWORD}|$GLPI_DB_PASSWORD|g" \
    -e "s|\${ZABBIX_DB_NAME}|$ZABBIX_DB_NAME|g" \
    -e "s|\${ZABBIX_DB_USER}|$ZABBIX_DB_USER|g" \
    -e "s|\${ZABBIX_DB_PASSWORD}|$ZABBIX_DB_PASSWORD|g" \
    "$TEMPLATE_FILE" > "$OUTPUT_FILE"

sed \
    -e "s|\${OPS_POSTGRES_SCHEMA}|$OPS_POSTGRES_SCHEMA|g" \
    "$POSTGRES_TEMPLATE_FILE" > "$POSTGRES_OUTPUT_FILE"

echo "Arquivo SQL gerado em $OUTPUT_FILE"
echo "Arquivo SQL operacional gerado em $POSTGRES_OUTPUT_FILE"
