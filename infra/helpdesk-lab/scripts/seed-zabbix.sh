#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

load_lab_env
require_commands docker curl jq
ensure_service_running zabbix-server
ensure_service_running zabbix-web

if ! compose exec -T zabbix-server sh -lc 'command -v zabbix_sender >/dev/null 2>&1'; then
    echo "O binario zabbix_sender nao esta disponivel no container zabbix-server." >&2
    exit 1
fi

ZABBIX_API_URL="http://127.0.0.1:${ZABBIX_WEB_HOST_PORT}/api_jsonrpc.php"
AUTH="$(
    curl -sS -X POST \
        "$ZABBIX_API_URL" \
        -H 'Content-Type: application/json-rpc' \
        -d '{"jsonrpc":"2.0","method":"user.login","params":{"username":"Admin","password":"zabbix"},"id":1}' |
    jq -r '.result // empty'
)"

if [[ -z "$AUTH" ]]; then
    echo "Nao foi possivel autenticar na API do Zabbix." >&2
    exit 1
fi

cleanup() {
    curl -sS -X POST \
        "$ZABBIX_API_URL" \
        -H 'Content-Type: application/json-rpc' \
        -H "Authorization: Bearer $AUTH" \
        -d '{"jsonrpc":"2.0","method":"user.logout","params":[],"id":1}' >/dev/null || true
}
trap cleanup EXIT

zabbix_api() {
    local method="$1"
    local params_json="$2"

    curl -sS -X POST \
        "$ZABBIX_API_URL" \
        -H 'Content-Type: application/json-rpc' \
        -H "Authorization: Bearer $AUTH" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"${method}\",\"params\":${params_json},\"id\":1}"
}

ensure_host_group() {
    local group_name="$1"
    local group_id

    group_id="$(
        zabbix_api "hostgroup.get" "{\"output\":[\"groupid\"],\"filter\":{\"name\":[\"${group_name}\"]}}" |
        jq -r '.result[0].groupid // empty'
    )"
    if [[ -z "$group_id" ]]; then
        group_id="$(
            zabbix_api "hostgroup.create" "{\"name\":\"${group_name}\"}" |
            jq -r '.result.groupids[0] // empty'
        )"
    fi

    if [[ -z "$group_id" ]]; then
        echo "Nao foi possivel obter ou criar o host group ${group_name} no Zabbix." >&2
        exit 1
    fi

    printf '%s\n' "$group_id"
}

ensure_host() {
    local host_name="$1"
    local group_id="$2"
    local ip_address="${3:-127.0.0.1}"
    local host_id

    host_id="$(
        zabbix_api "host.get" "{\"output\":[\"hostid\"],\"filter\":{\"host\":[\"${host_name}\"]}}" |
        jq -r '.result[0].hostid // empty'
    )"
    if [[ -z "$host_id" ]]; then
        host_id="$(
            zabbix_api "host.create" "{\"host\":\"${host_name}\",\"interfaces\":[{\"type\":1,\"main\":1,\"useip\":1,\"ip\":\"${ip_address}\",\"dns\":\"\",\"port\":\"10050\"}],\"groups\":[{\"groupid\":\"${group_id}\"}]}" |
            jq -r '.result.hostids[0] // empty'
        )"
    fi

    if [[ -z "$host_id" ]]; then
        echo "Nao foi possivel obter ou criar o host ${host_name}." >&2
        exit 1
    fi

    printf '%s\n' "$host_id"
}

ensure_trapper_item() {
    local host_id="$1"
    local item_key="$2"
    local item_name="$3"
    local item_id

    item_id="$(
        zabbix_api "item.get" "{\"output\":[\"itemid\"],\"hostids\":[\"${host_id}\"],\"filter\":{\"key_\":[\"${item_key}\"]}}" |
        jq -r '.result[0].itemid // empty'
    )"
    if [[ -z "$item_id" ]]; then
        item_id="$(
            zabbix_api "item.create" "{\"name\":\"${item_name}\",\"key_\":\"${item_key}\",\"hostid\":\"${host_id}\",\"type\":2,\"value_type\":3,\"trapper_hosts\":\"127.0.0.1\",\"delay\":\"0\"}" |
            jq -r '.result.itemids[0] // empty'
        )"
    fi

    if [[ -z "$item_id" ]]; then
        echo "Nao foi possivel obter ou criar o item ${item_key} no host ${host_id}." >&2
        exit 1
    fi

    printf '%s\n' "$item_id"
}

ensure_trigger() {
    local host_id="$1"
    local host_name="$2"
    local description="$3"
    local priority="$4"
    local service_tag="$5"
    local trigger_id

    trigger_id="$(
        zabbix_api "trigger.get" "{\"output\":[\"triggerid\"],\"hostids\":[\"${host_id}\"],\"filter\":{\"description\":[\"${description}\"]}}" |
        jq -r '.result[0].triggerid // empty'
    )"
    if [[ -z "$trigger_id" ]]; then
        trigger_id="$(
            zabbix_api "trigger.create" "{\"description\":\"${description}\",\"expression\":\"last(/${host_name}/lab.problem)>0\",\"priority\":${priority},\"tags\":[{\"tag\":\"service\",\"value\":\"${service_tag}\"},{\"tag\":\"lab\",\"value\":\"helpdesk\"}]}" |
            jq -r '.result.triggerids[0] // empty'
        )"
    fi

    if [[ -z "$trigger_id" ]]; then
        echo "Nao foi possivel obter ou criar a trigger ${description}." >&2
        exit 1
    fi

    printf '%s\n' "$trigger_id"
}

open_problem_on_host() {
    local host_name="$1"
    local attempt

    for attempt in 1 2 3; do
        if compose exec -T zabbix-server sh -lc \
            "zabbix_sender -z 127.0.0.1 -p 10051 -s ${host_name} -k lab.problem -o 1" >/dev/null; then
            return 0
        fi
        sleep 1
    done

    return 0
}

wait_for_problem_event() {
    local search_term="$1"
    local event_id=""

    for _ in $(seq 1 10); do
        event_id="$(
            zabbix_api "problem.get" "{\"output\":[\"eventid\",\"name\",\"severity\",\"objectid\"],\"sortfield\":[\"eventid\"],\"sortorder\":\"DESC\",\"search\":{\"name\":\"${search_term}\"},\"limit\":5}" |
            jq -r '.result[0].eventid // empty'
        )"
        if [[ -n "$event_id" ]]; then
            break
        fi
        sleep 2
    done

    if [[ -z "$event_id" ]]; then
        echo "A trigger ${search_term} foi criada, mas nenhum problema aberto apareceu no Zabbix." >&2
        exit 1
    fi

    printf '%s\n' "$event_id"
}

GROUP_ID="$(ensure_host_group "Helpdesk Lab")"

ERP_HOST_ID="$(ensure_host "erp-web-01" "$GROUP_ID")"
ERP_ITEM_ID="$(ensure_trapper_item "$ERP_HOST_ID" "lab.problem" "LAB problem state")"
ERP_TRIGGER_ID="$(ensure_trigger "$ERP_HOST_ID" "erp-web-01" "ERP indisponível no host {HOST.NAME}" 4 "erp")"
open_problem_on_host "erp-web-01"
ERP_EVENT_ID="$(wait_for_problem_event "ERP indisponível")"

VPN_HOST_ID="$(ensure_host "vpn-edge-01" "$GROUP_ID")"
VPN_ITEM_ID="$(ensure_trapper_item "$VPN_HOST_ID" "lab.problem" "LAB problem state")"
VPN_TRIGGER_ID="$(ensure_trigger "$VPN_HOST_ID" "vpn-edge-01" "VPN intermitente no host {HOST.NAME}" 3 "vpn")"
open_problem_on_host "vpn-edge-01"
VPN_EVENT_ID="$(wait_for_problem_event "VPN intermitente")"

AUTH_HOST_ID="$(ensure_host "auth-01" "$GROUP_ID")"
AUTH_ITEM_ID="$(ensure_trapper_item "$AUTH_HOST_ID" "lab.problem" "LAB problem state")"
AUTH_TRIGGER_ID="$(ensure_trigger "$AUTH_HOST_ID" "auth-01" "Falha de autenticação no host {HOST.NAME}" 4 "auth")"
open_problem_on_host "auth-01"
AUTH_EVENT_ID="$(wait_for_problem_event "Falha de autenticação")"

PRINT_HOST_ID="$(ensure_host "print-spool-01" "$GROUP_ID")"
PRINT_ITEM_ID="$(ensure_trapper_item "$PRINT_HOST_ID" "lab.problem" "LAB problem state")"
PRINT_TRIGGER_ID="$(ensure_trigger "$PRINT_HOST_ID" "print-spool-01" "Fila de impressão parada no host {HOST.NAME}" 3 "impressao")"
open_problem_on_host "print-spool-01"
PRINT_EVENT_ID="$(wait_for_problem_event "Fila de impressão parada")"

echo "Seed do Zabbix concluido."
echo "Host group: Helpdesk Lab (${GROUP_ID})"
echo "Host: erp-web-01 (${ERP_HOST_ID}) | Trigger: ERP indisponível no host {HOST.NAME} (${ERP_TRIGGER_ID}) | Evento: ${ERP_EVENT_ID}"
echo "Host: vpn-edge-01 (${VPN_HOST_ID}) | Trigger: VPN intermitente no host {HOST.NAME} (${VPN_TRIGGER_ID}) | Evento: ${VPN_EVENT_ID}"
echo "Host: auth-01 (${AUTH_HOST_ID}) | Trigger: Falha de autenticação no host {HOST.NAME} (${AUTH_TRIGGER_ID}) | Evento: ${AUTH_EVENT_ID}"
echo "Host: print-spool-01 (${PRINT_HOST_ID}) | Trigger: Fila de impressão parada no host {HOST.NAME} (${PRINT_TRIGGER_ID}) | Evento: ${PRINT_EVENT_ID}"
echo "Itens trapper: erp=${ERP_ITEM_ID}, vpn=${VPN_ITEM_ID}, auth=${AUTH_ITEM_ID}, impressao=${PRINT_ITEM_ID}"
