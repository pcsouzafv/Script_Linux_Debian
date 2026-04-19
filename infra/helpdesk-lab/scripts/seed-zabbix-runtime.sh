#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

load_lab_env
require_commands docker curl jq ip ss
ensure_service_running zabbix-server
ensure_service_running zabbix-web

ZABBIX_API_URL="http://127.0.0.1:${ZABBIX_WEB_HOST_PORT}/api_jsonrpc.php"
ZABBIX_SERVER_CONTAINER="$(compose ps -q zabbix-server | xargs docker inspect --format '{{.Name}}' | sed 's#^/##')"

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
        echo "Nao foi possivel obter ou criar o host group ${group_name}." >&2
        exit 1
    fi

    printf '%s\n' "$group_id"
}

get_icmp_template_id() {
    zabbix_api "template.get" '{"output":["templateid"],"filter":{"host":["ICMP Ping"]}}' |
        jq -r '.result[0].templateid // empty'
}

sanitize_host_name() {
    printf '%s' "$1" | tr ' /:@' '----'
}

container_monitoring_kind() {
    local image="$1"
    local image_repo="${image%%[:@]*}"

    case "$image_repo" in
        postgres|*/postgres|mysql|*/mysql|mariadb|*/mariadb|redis|*/redis|docker.elastic.co/elasticsearch/elasticsearch|*/elasticsearch)
            printf 'database\n'
            ;;
        *)
            printf 'service\n'
            ;;
    esac
}

service_type_for_port() {
    local port="$1"

    case "$port" in
        80|3000|5000|5601|8080|9000|9200)
            printf 'http\n'
            ;;
        443|8443|9443)
            printf 'https\n'
            ;;
        *)
            printf 'tcp\n'
            ;;
    esac
}

severity_for_service() {
    local kind="$1"

    if [[ "$kind" == "database" ]]; then
        printf '4\n'
    else
        printf '3\n'
    fi
}

ensure_network_connection() {
    local network_name="$1"

    if docker inspect "$ZABBIX_SERVER_CONTAINER" --format '{{json .NetworkSettings.Networks}}' |
        jq -e --arg network "$network_name" 'has($network)' >/dev/null; then
        return 0
    fi

    docker network connect "$network_name" "$ZABBIX_SERVER_CONTAINER" >/dev/null 2>&1 || true
}

ensure_zabbix_server_network_access() {
    local container_name network_name

    while IFS= read -r container_name; do
        [[ -z "$container_name" ]] && continue

        while IFS= read -r network_name; do
            [[ -z "$network_name" ]] && continue
            ensure_network_connection "$network_name"
        done < <(
            docker inspect "$container_name" |
                jq -r '.[0].NetworkSettings.Networks | keys[]?'
        )
    done < <(docker ps --format '{{.Names}}')
}

get_primary_network_name() {
    local container_name="$1"

    docker inspect "$container_name" |
        jq -r '.[0].NetworkSettings.Networks as $nets
            | if $nets | has("helpdesk_lab") then "helpdesk_lab"
              elif $nets | has("bridge") then "bridge"
              else ($nets | to_entries | sort_by(.key) | map(select(.value.IPAddress != "")) | .[0].key // "")
              end'
}

get_primary_ip() {
    local container_name="$1"

    docker inspect "$container_name" |
        jq -r '.[0].NetworkSettings.Networks as $nets
            | if $nets | has("helpdesk_lab") then $nets["helpdesk_lab"].IPAddress
              elif $nets | has("bridge") then $nets["bridge"].IPAddress
              else ($nets | to_entries | sort_by(.key) | map(select(.value.IPAddress != "")) | .[0].value.IPAddress // "")
              end'
}

get_container_ports() {
    local container_name="$1"

    docker inspect "$container_name" |
        jq -r '.[0]
            | [(.Config.ExposedPorts // {} | keys[]?), (.NetworkSettings.Ports // {} | keys[]?)]
            | flatten
            | map(select(test("/tcp$")))
            | map(split("/")[0] | tonumber)
            | unique
            | sort
            | .[]'
}

ensure_host() {
    local technical_name="$1"
    local visible_name="$2"
    local interface_ip="$3"
    local groups_json="$4"
    local tags_json="$5"
    local description="$6"
    local host_json host_id interface_id payload

    host_json="$(
        zabbix_api "host.get" "{\"output\":[\"hostid\",\"host\",\"name\"],\"filter\":{\"host\":[\"${technical_name}\"]},\"selectInterfaces\":[\"interfaceid\",\"ip\",\"dns\",\"port\",\"type\",\"main\",\"useip\"]}" |
        jq -c '.result[0] // empty'
    )"

    if [[ -z "$host_json" ]]; then
        payload="$(
            jq -n \
                --arg host "$technical_name" \
                --arg name "$visible_name" \
                --arg ip "$interface_ip" \
                --arg description "$description" \
                --argjson groups "$groups_json" \
                --argjson tags "$tags_json" \
                --argjson icmp_template "$ICMP_TEMPLATE_JSON" \
                '{
                    host: $host,
                    name: $name,
                    description: $description,
                    groups: $groups,
                    tags: $tags,
                    templates: $icmp_template,
                    interfaces: [
                        {
                            type: 1,
                            main: 1,
                            useip: 1,
                            ip: $ip,
                            dns: "",
                            port: "10050"
                        }
                    ]
                }'
        )"
        host_id="$(
            zabbix_api "host.create" "$payload" |
                jq -r '.result.hostids[0] // empty'
        )"
        if [[ -z "$host_id" ]]; then
            echo "Falha ao criar host ${technical_name} no Zabbix." >&2
            exit 1
        fi
        interface_id="$(
            zabbix_api "hostinterface.get" "{\"output\":[\"interfaceid\"],\"hostids\":[\"${host_id}\"]}" |
                jq -r '.result[0].interfaceid // empty'
        )"
    else
        host_id="$(printf '%s' "$host_json" | jq -r '.hostid')"
        interface_id="$(printf '%s' "$host_json" | jq -r '.interfaces[0].interfaceid // empty')"

        payload="$(
            jq -n \
                --arg hostid "$host_id" \
                --arg name "$visible_name" \
                --arg description "$description" \
                --argjson groups "$groups_json" \
                --argjson tags "$tags_json" \
                --argjson icmp_template "$ICMP_TEMPLATE_JSON" \
                '{
                    hostid: $hostid,
                    name: $name,
                    description: $description,
                    groups: $groups,
                    tags: $tags,
                    templates: $icmp_template
                }'
        )"
        zabbix_api "host.update" "$payload" >/dev/null

        if [[ -n "$interface_id" ]]; then
            payload="$(
                jq -n \
                    --arg interfaceid "$interface_id" \
                    --arg ip "$interface_ip" \
                    '{
                        interfaceid: $interfaceid,
                        ip: $ip,
                        dns: "",
                        port: "10050",
                        type: 1,
                        main: 1,
                        useip: 1
                    }'
            )"
            zabbix_api "hostinterface.update" "$payload" >/dev/null
        fi
    fi

    printf '%s|%s\n' "$host_id" "$interface_id"
}

ensure_simple_item() {
    local host_id="$1"
    local interface_id="$2"
    local item_name="$3"
    local item_key="$4"

    local item_id payload
    item_id="$(
        zabbix_api "item.get" "{\"output\":[\"itemid\"],\"hostids\":[\"${host_id}\"],\"filter\":{\"key_\":[\"${item_key}\"]}}" |
        jq -r '.result[0].itemid // empty'
    )"

    if [[ -z "$item_id" ]]; then
        payload="$(
            jq -n \
                --arg hostid "$host_id" \
                --arg interfaceid "$interface_id" \
                --arg name "$item_name" \
                --arg key_ "$item_key" \
                '{
                    hostid: $hostid,
                    interfaceid: $interfaceid,
                    name: $name,
                    key_: $key_,
                    type: 3,
                    value_type: 3,
                    delay: "1m"
                }'
        )"
        item_id="$(
            zabbix_api "item.create" "$payload" |
                jq -r '.result.itemids[0] // empty'
        )"
    else
        payload="$(
            jq -n \
                --arg itemid "$item_id" \
                --arg interfaceid "$interface_id" \
                --arg name "$item_name" \
                '{
                    itemid: $itemid,
                    interfaceid: $interfaceid,
                    name: $name,
                    delay: "1m"
                }'
        )"
        zabbix_api "item.update" "$payload" >/dev/null
    fi

    printf '%s\n' "$item_id"
}

ensure_trigger() {
    local description="$1"
    local expression="$2"
    local priority="$3"

    local trigger_id payload
    trigger_id="$(
        zabbix_api "trigger.get" "{\"output\":[\"triggerid\"],\"filter\":{\"description\":[\"${description}\"]}}" |
        jq -r '.result[0].triggerid // empty'
    )"

    if [[ -z "$trigger_id" ]]; then
        payload="$(
            jq -n \
                --arg description "$description" \
                --arg expression "$expression" \
                --argjson priority "$priority" \
                '{
                    description: $description,
                    expression: $expression,
                    priority: $priority
                }'
        )"
        zabbix_api "trigger.create" "$payload" >/dev/null
    else
        payload="$(
            jq -n \
                --arg triggerid "$trigger_id" \
                --arg description "$description" \
                --arg expression "$expression" \
                --argjson priority "$priority" \
                '{
                    triggerid: $triggerid,
                    description: $description,
                    expression: $expression,
                    priority: $priority
                }'
        )"
        zabbix_api "trigger.update" "$payload" >/dev/null
    fi
}

cleanup_managed_service_checks() {
    local host_id="$1"
    local trigger_ids_json item_ids_json

    trigger_ids_json="$(
        zabbix_api "trigger.get" "{\"output\":[\"triggerid\",\"description\"],\"hostids\":[\"${host_id}\"]}" |
            jq -c '[.result[]? | select(.description | contains(": porta ")) | .triggerid]'
    )"
    if [[ "$trigger_ids_json" != "[]" ]]; then
        zabbix_api "trigger.delete" "$trigger_ids_json" >/dev/null
    fi

    item_ids_json="$(
        zabbix_api "item.get" "{\"output\":[\"itemid\",\"key_\"],\"hostids\":[\"${host_id}\"]}" |
            jq -c '[.result[]? | select(.key_ | startswith("net.tcp.service[")) | .itemid]'
    )"
    if [[ "$item_ids_json" != "[]" ]]; then
        zabbix_api "item.delete" "$item_ids_json" >/dev/null
    fi
}

ensure_service_check() {
    local host_technical="$1"
    local host_visible="$2"
    local host_id="$3"
    local interface_id="$4"
    local port="$5"
    local severity="$6"
    local service_type item_key item_name description expression

    service_type="$(service_type_for_port "$port")"
    item_key="net.tcp.service[${service_type},,${port}]"
    item_name="Disponibilidade ${service_type^^} porta ${port}"
    ensure_simple_item "$host_id" "$interface_id" "$item_name" "$item_key" >/dev/null

    description="${host_visible}: porta ${port}/${service_type} indisponível"
    expression="last(/${host_technical}/${item_key})=0"
    ensure_trigger "$description" "$expression" "$severity"
}

ensure_discovery_rule() {
    local discovery_name="$1"
    local ip_range="$2"
    local dchecks_json="$3"
    local drule_id payload

    drule_id="$(
        zabbix_api "drule.get" "{\"output\":[\"druleid\"],\"filter\":{\"name\":[\"${discovery_name}\"]}}" |
        jq -r '.result[0].druleid // empty'
    )"

    if [[ -z "$drule_id" ]]; then
        payload="$(
            jq -n \
                --arg name "$discovery_name" \
                --arg iprange "$ip_range" \
                --argjson dchecks "$dchecks_json" \
                '{
                    name: $name,
                    iprange: $iprange,
                    delay: "1h",
                    status: 0,
                    concurrency_max: 50,
                    dchecks: $dchecks
                }'
        )"
        zabbix_api "drule.create" "$payload" >/dev/null
    else
        payload="$(
            jq -n \
                --arg druleid "$drule_id" \
                --arg iprange "$ip_range" \
                --argjson dchecks "$dchecks_json" \
                '{
                    druleid: $druleid,
                    iprange: $iprange,
                    delay: "1h",
                    status: 0,
                    concurrency_max: 50,
                    dchecks: $dchecks
                }'
        )"
        zabbix_api "drule.update" "$payload" >/dev/null
    fi
}

build_group_refs_json() {
    jq -n --argjson docker_hosts "$DOCKER_HOSTS_GROUP_ID" --argjson docker_databases "$DOCKER_DATABASES_GROUP_ID" '
        [
            {groupid: ($docker_hosts | tostring)},
            {groupid: ($docker_databases | tostring)}
        ]'
}

ensure_zabbix_server_network_access

DOCKER_HOSTS_GROUP_ID="$(ensure_host_group "Docker Hosts")"
DOCKER_DATABASES_GROUP_ID="$(ensure_host_group "Docker Databases")"
HOST_MACHINE_GROUP_ID="$(ensure_host_group "Host Machine")"
ICMP_TEMPLATE_ID="$(get_icmp_template_id)"

if [[ -z "$ICMP_TEMPLATE_ID" ]]; then
    echo "Template 'ICMP Ping' nao encontrado no Zabbix." >&2
    exit 1
fi

ICMP_TEMPLATE_JSON="$(jq -n --arg templateid "$ICMP_TEMPLATE_ID" '[{templateid: $templateid}]')"

RUNTIME_HOSTS_CREATED=0
DATABASE_HOSTS_CREATED=0
SKIPPED_CONTAINERS=()

while IFS= read -r container_name; do
    [[ -z "$container_name" ]] && continue

    container_image="$(docker inspect "$container_name" | jq -r '.[0].Config.Image')"
    compose_project="$(docker inspect "$container_name" | jq -r '.[0].Config.Labels["com.docker.compose.project"] // empty')"
    network_name="$(get_primary_network_name "$container_name")"
    interface_ip="$(get_primary_ip "$container_name")"

    if [[ -z "$interface_ip" ]]; then
        SKIPPED_CONTAINERS+=("$container_name")
        continue
    fi

    host_kind="$(container_monitoring_kind "$container_image")"
    if [[ "$host_kind" == "database" ]]; then
        host_kind="database"
        groups_json="$(build_group_refs_json)"
        DATABASE_HOSTS_CREATED=$((DATABASE_HOSTS_CREATED + 1))
    else
        host_kind="service"
        groups_json="$(jq -n --argjson docker_hosts "$DOCKER_HOSTS_GROUP_ID" '[{groupid: ($docker_hosts | tostring)}]')"
    fi

    technical_name="docker-$(sanitize_host_name "$container_name")"
    visible_name="Docker ${container_name}"

    ports_list="$(get_container_ports "$container_name" | paste -sd, -)"
    description="Container ${container_name} | image=${container_image} | network=${network_name} | ip=${interface_ip}"
    if [[ -n "$compose_project" ]]; then
        description="${description} | compose=${compose_project}"
    fi
    if [[ -n "$ports_list" ]]; then
        description="${description} | ports=${ports_list}"
    fi

    tags_json="$(
        jq -n \
            --arg scope "docker" \
            --arg container "$container_name" \
            --arg image "$container_image" \
            --arg network "$network_name" \
            --arg kind "$host_kind" \
            --arg compose_project "$compose_project" \
            '[
                {tag:"scope", value:$scope},
                {tag:"container", value:$container},
                {tag:"image", value:$image},
                {tag:"network", value:$network},
                {tag:"kind", value:$kind}
            ] + (if $compose_project != "" then [{tag:"compose_project", value:$compose_project}] else [] end)'
    )"

    host_result="$(ensure_host "$technical_name" "$visible_name" "$interface_ip" "$groups_json" "$tags_json" "$description")"
    host_id="${host_result%%|*}"
    interface_id="${host_result##*|}"
    RUNTIME_HOSTS_CREATED=$((RUNTIME_HOSTS_CREATED + 1))
    cleanup_managed_service_checks "$host_id"

    while IFS= read -r port; do
        [[ -z "$port" ]] && continue
        ensure_service_check "$technical_name" "$visible_name" "$host_id" "$interface_id" "$port" "$(severity_for_service "$host_kind")"
    done < <(get_container_ports "$container_name")
done < <(docker ps --format '{{.Names}}' | sort)

LOCAL_IFACE="$(ip route get 1.1.1.1 | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')"
LOCAL_IP="$(ip route get 1.1.1.1 | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')"
LOCAL_CIDR="$(ip -o -4 addr show dev "$LOCAL_IFACE" | awk '{print $4}' | head -n1)"
LOCAL_PREFIX="${LOCAL_CIDR##*/}"

if [[ "$LOCAL_PREFIX" == "24" ]]; then
    IFS='.' read -r oct1 oct2 oct3 _ <<<"$LOCAL_IP"
    DISCOVERY_RANGE="${oct1}.${oct2}.${oct3}.1-254"
else
    DISCOVERY_RANGE="$LOCAL_IP"
fi

HOST_PORTS="$(
    ss -H -ltn4 |
        awk -v local_ip="$LOCAL_IP" '$4 ~ /^0\.0\.0\.0:/ || index($4, local_ip ":") == 1 {print $4}' |
        sed 's/.*://' |
        sort -n |
        uniq
)"

HOST_TAGS_JSON="$(
    jq -n \
        --arg scope "host-machine" \
        --arg iface "$LOCAL_IFACE" \
        --arg ip "$LOCAL_IP" \
        '[
            {tag:"scope", value:$scope},
            {tag:"iface", value:$iface},
            {tag:"ip", value:$ip}
        ]'
)"
HOST_GROUPS_JSON="$(jq -n --argjson host_machine "$HOST_MACHINE_GROUP_ID" '[{groupid: ($host_machine | tostring)}]')"
HOST_DESCRIPTION="Máquina local do laboratório | iface=${LOCAL_IFACE} | ip=${LOCAL_IP}"
HOST_RESULT="$(ensure_host "local-machine" "Máquina local (${LOCAL_IP})" "$LOCAL_IP" "$HOST_GROUPS_JSON" "$HOST_TAGS_JSON" "$HOST_DESCRIPTION")"
HOST_HOST_ID="${HOST_RESULT%%|*}"
HOST_INTERFACE_ID="${HOST_RESULT##*|}"
cleanup_managed_service_checks "$HOST_HOST_ID"

while IFS= read -r port; do
    [[ -z "$port" ]] && continue
    ensure_service_check "local-machine" "Máquina local (${LOCAL_IP})" "$HOST_HOST_ID" "$HOST_INTERFACE_ID" "$port" 3
done <<<"$HOST_PORTS"

DISCOVERY_DCHECKS_JSON="$(
    jq -n '[
        {type: 12},
        {type: 8, ports: "22"},
        {type: 4, ports: "80,8080"},
        {type: 14, ports: "443,9443"}
    ]'
)"
ensure_discovery_rule "Descoberta LAN local" "$DISCOVERY_RANGE" "$DISCOVERY_DCHECKS_JSON"

echo "Seed de runtime do Zabbix concluido."
echo "Hosts Docker garantidos no Zabbix: ${RUNTIME_HOSTS_CREATED}"
echo "Hosts classificados como bancos/servicos de dados: ${DATABASE_HOSTS_CREATED}"
echo "Máquina local incluida: ${LOCAL_IP}"
echo "Regra de descoberta de rede atualizada: ${DISCOVERY_RANGE}"
if ((${#SKIPPED_CONTAINERS[@]} > 0)); then
    echo "Containers ignorados por ausencia de IP roteavel no Docker: ${SKIPPED_CONTAINERS[*]}"
fi
