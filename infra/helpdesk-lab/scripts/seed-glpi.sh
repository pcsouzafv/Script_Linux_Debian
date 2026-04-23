#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

load_lab_env
require_commands docker curl jq
ensure_service_running db
ensure_service_running glpi

GLPI_API_URL="http://127.0.0.1:${GLPI_HOST_PORT}/apirest.php"
IDENTITY_FILE="$ROOT_DIR/backend/data/identities.lab.json"
DEFAULT_USER_PASSWORD="LabUser@123"

db_query() {
    local sql="$1"
    compose exec -T -e MYSQL_PWD="$LAB_DB_ROOT_PASSWORD" \
        db mysql --default-character-set=utf8mb4 -N -B -uroot -D "$GLPI_DB_NAME" -e "$sql"
}

sql_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"
}

glpi_open_session() {
    curl -sS \
        -H 'Content-Type: application/json' \
        -H 'Authorization: Basic Z2xwaTpnbHBp' \
        "${GLPI_API_URL}/initSession" |
        jq -r '.session_token // empty'
}

GLPI_SESSION="$(glpi_open_session)"
if [[ -z "$GLPI_SESSION" ]]; then
    echo "Nao foi possivel iniciar sessao na API do GLPI." >&2
    exit 1
fi

cleanup() {
    curl -sS \
        -H 'Content-Type: application/json' \
        -H "Session-Token: $GLPI_SESSION" \
        "${GLPI_API_URL}/killSession" >/dev/null || true
}
trap cleanup EXIT

glpi_api() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"

    if [[ -n "$payload" ]]; then
        curl -sS \
            -X "$method" \
            "${GLPI_API_URL}${path}" \
            -H 'Content-Type: application/json' \
            -H "Session-Token: $GLPI_SESSION" \
            -d "$payload"
    else
        curl -sS \
            -X "$method" \
            "${GLPI_API_URL}${path}" \
            -H 'Content-Type: application/json' \
            -H "Session-Token: $GLPI_SESSION"
    fi
}

find_user_id_by_login() {
    local login="$1"
    db_query "SELECT id FROM glpi_users WHERE name = $(sql_quote "$login") LIMIT 1;"
}

find_item_id_by_name() {
    local table="$1"
    local name="$2"
    db_query "SELECT id FROM ${table} WHERE name = $(sql_quote "$name") LIMIT 1;"
}

find_ticket_id_by_name() {
    local name="$1"
    db_query "SELECT id FROM glpi_tickets WHERE name = $(sql_quote "$name") AND is_deleted = 0 ORDER BY id DESC LIMIT 1;"
}

find_category_id_by_name() {
    local name="$1"
    db_query "SELECT id FROM glpi_itilcategories WHERE name = $(sql_quote "$name") LIMIT 1;"
}

find_group_id_by_name() {
    local name="$1"
    local parent_group_id="${2:-0}"
    db_query "SELECT id FROM glpi_groups WHERE name = $(sql_quote "$name") AND groups_id = ${parent_group_id} LIMIT 1;"
}

find_location_id_by_name() {
    local name="$1"
    local parent_location_id="${2:-0}"
    db_query "SELECT id FROM glpi_locations WHERE name = $(sql_quote "$name") AND locations_id = ${parent_location_id} LIMIT 1;"
}

ensure_group() {
    local name="$1"
    local parent_group_id="${2:-0}"
    local code="${3:-}"
    local is_assign="${4:-1}"
    local group_id

    group_id="$(find_group_id_by_name "$name" "$parent_group_id")"
    if [[ -n "$group_id" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$group_id" \
                --arg name "$name" \
                --arg code "$code" \
                --argjson parent_group_id "$parent_group_id" \
                --argjson is_assign "$is_assign" \
                '{input:{id:$id, name:$name, code:($code | if . == "" then null else . end), groups_id:$parent_group_id, is_requester:1, is_watcher:1, is_assign:$is_assign, is_task:$is_assign, is_notify:1, is_itemgroup:1, is_usergroup:1, is_manager:1}}'
        )"
        glpi_api PUT "/Group/${group_id}" "$payload" >/dev/null
        printf '%s\n' "$group_id"
        return 0
    fi

    local payload response
    payload="$(
        jq -n \
            --arg name "$name" \
            --arg code "$code" \
            --argjson parent_group_id "$parent_group_id" \
            --argjson is_assign "$is_assign" \
            '{input:{name:$name, code:($code | if . == "" then null else . end), groups_id:$parent_group_id, entities_id:0, is_recursive:1, is_requester:1, is_watcher:1, is_assign:$is_assign, is_task:$is_assign, is_notify:1, is_itemgroup:1, is_usergroup:1, is_manager:1}}'
    )"
    response="$(glpi_api POST '/Group/' "$payload")"
    group_id="$(printf '%s' "$response" | jq -r '.id // empty')"
    if [[ -z "$group_id" ]]; then
        echo "Falha ao criar grupo ${name} no GLPI." >&2
        printf '%s\n' "$response" >&2
        exit 1
    fi

    printf '%s\n' "$group_id"
}

ensure_group_user_link() {
    local user_id="$1"
    local group_id="$2"
    local is_manager="${3:-0}"
    local is_userdelegate="${4:-0}"
    local link_id

    link_id="$(
        db_query "SELECT id FROM glpi_groups_users WHERE users_id = ${user_id} AND groups_id = ${group_id} LIMIT 1;"
    )"

    local payload
    payload="$(
        jq -n \
            --argjson user_id "$user_id" \
            --argjson group_id "$group_id" \
            --argjson is_manager "$is_manager" \
            --argjson is_userdelegate "$is_userdelegate" \
            '{input:{users_id:$user_id, groups_id:$group_id, is_manager:$is_manager, is_userdelegate:$is_userdelegate}}'
    )"

    if [[ -n "$link_id" ]]; then
        payload="$(
            jq -n \
                --argjson id "$link_id" \
                --argjson user_id "$user_id" \
                --argjson group_id "$group_id" \
                --argjson is_manager "$is_manager" \
                --argjson is_userdelegate "$is_userdelegate" \
                '{input:{id:$id, users_id:$user_id, groups_id:$group_id, is_manager:$is_manager, is_userdelegate:$is_userdelegate}}'
        )"
        glpi_api PUT "/Group_User/${link_id}" "$payload" >/dev/null
        return 0
    fi

    glpi_api POST '/Group_User/' "$payload" >/dev/null
}

ensure_primary_group_for_user() {
    local user_id="$1"
    local group_id="$2"
    local is_manager="${3:-0}"
    local is_userdelegate="${4:-0}"

    db_query "DELETE FROM glpi_groups_users WHERE users_id = ${user_id} AND groups_id <> ${group_id};" >/dev/null
    ensure_group_user_link "$user_id" "$group_id" "$is_manager" "$is_userdelegate"
}

ensure_location() {
    local name="$1"
    local parent_location_id="${2:-0}"
    local building="${3:-}"
    local room="${4:-}"
    local town="${5:-}"
    local location_id

    location_id="$(find_location_id_by_name "$name" "$parent_location_id")"
    if [[ -n "$location_id" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$location_id" \
                --arg name "$name" \
                --argjson parent_location_id "$parent_location_id" \
                --arg building "$building" \
                --arg room "$room" \
                --arg town "$town" \
                '{input:{id:$id, name:$name, locations_id:$parent_location_id, building:($building | if . == "" then null else . end), room:($room | if . == "" then null else . end), town:($town | if . == "" then null else . end)}}'
        )"
        glpi_api PUT "/Location/${location_id}" "$payload" >/dev/null
        printf '%s\n' "$location_id"
        return 0
    fi

    local payload response
    payload="$(
        jq -n \
            --arg name "$name" \
            --argjson parent_location_id "$parent_location_id" \
            --arg building "$building" \
            --arg room "$room" \
            --arg town "$town" \
            '{input:{name:$name, entities_id:0, is_recursive:1, locations_id:$parent_location_id, building:($building | if . == "" then null else . end), room:($room | if . == "" then null else . end), town:($town | if . == "" then null else . end)}}'
    )"
    response="$(glpi_api POST '/Location/' "$payload")"
    location_id="$(printf '%s' "$response" | jq -r '.id // empty')"
    if [[ -z "$location_id" ]]; then
        echo "Falha ao criar localizacao ${name} no GLPI." >&2
        printf '%s\n' "$response" >&2
        exit 1
    fi

    printf '%s\n' "$location_id"
}

ensure_named_existing_user() {
    local user_id="$1"
    local firstname="$2"
    local realname="$3"
    local phone_number="$4"

    local payload
    payload="$(
        jq -n \
            --argjson id "$user_id" \
            --arg firstname "$firstname" \
            --arg realname "$realname" \
            --arg phone "$phone_number" \
            '{input:{id:$id, firstname:$firstname, realname:$realname, phone:$phone, mobile:$phone, is_active:1}}'
    )"
    glpi_api PUT "/User/${user_id}" "$payload" >/dev/null
}

ensure_user() {
    local login="$1"
    local firstname="$2"
    local realname="$3"
    local phone_number="$4"
    local legacy_login="${5:-}"
    local user_id

    user_id="$(find_user_id_by_login "$login")"
    if [[ -z "$user_id" && -n "$legacy_login" ]]; then
        user_id="$(find_user_id_by_login "$legacy_login")"
    fi

    if [[ -n "$user_id" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$user_id" \
                --arg login "$login" \
                --arg firstname "$firstname" \
                --arg realname "$realname" \
                --arg phone "$phone_number" \
                '{input:{id:$id, name:$login, firstname:$firstname, realname:$realname, phone:$phone, mobile:$phone, is_active:1}}'
        )"
        glpi_api PUT "/User/${user_id}" "$payload" >/dev/null
        printf '%s\n' "$user_id"
        return 0
    fi

    local payload response
    payload="$(
        jq -n \
            --arg login "$login" \
            --arg password "$DEFAULT_USER_PASSWORD" \
            --arg firstname "$firstname" \
            --arg realname "$realname" \
            --arg phone "$phone_number" \
            '{input:{name:$login, password:$password, password2:$password, firstname:$firstname, realname:$realname, phone:$phone, mobile:$phone, is_active:1}}'
    )"
    response="$(glpi_api POST '/User/' "$payload")"
    user_id="$(printf '%s' "$response" | jq -r '.id // empty')"
    if [[ -z "$user_id" ]]; then
        echo "Falha ao criar usuario ${firstname} ${realname} no GLPI." >&2
        printf '%s\n' "$response" >&2
        exit 1
    fi

    printf '%s\n' "$user_id"
}

ensure_asset() {
    local endpoint="$1"
    local table="$2"
    local name="$3"
    local serial="$4"
    local comment="$5"
    local legacy_name="${6:-}"
    local item_id

    item_id="$(find_item_id_by_name "$table" "$name")"
    if [[ -z "$item_id" && -n "$legacy_name" ]]; then
        item_id="$(find_item_id_by_name "$table" "$legacy_name")"
    fi

    if [[ -n "$item_id" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$item_id" \
                --arg name "$name" \
                --arg serial "$serial" \
                --arg comment "$comment" \
                '{input:{id:$id, name:$name, serial:$serial, comment:$comment}}'
        )"
        glpi_api PUT "/${endpoint}/${item_id}" "$payload" >/dev/null
        printf '%s\n' "$item_id"
        return 0
    fi

    local payload response
    payload="$(
        jq -n \
            --arg name "$name" \
            --arg serial "$serial" \
            --arg comment "$comment" \
            '{input:{name:$name, serial:$serial, comment:$comment}}'
    )"
    response="$(glpi_api POST "/${endpoint}/" "$payload")"
    item_id="$(printf '%s' "$response" | jq -r '.id // empty')"
    if [[ -z "$item_id" ]]; then
        echo "Falha ao criar ativo ${name} no GLPI." >&2
        printf '%s\n' "$response" >&2
        exit 1
    fi

    printf '%s\n' "$item_id"
}

set_item_location() {
    local endpoint="$1"
    local item_id="$2"
    local location_id="$3"

    if [[ -z "$location_id" || "$location_id" == "0" ]]; then
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --argjson id "$item_id" \
            --argjson location_id "$location_id" \
            '{input:{id:$id, locations_id:$location_id}}'
    )"
    glpi_api PUT "/${endpoint}/${item_id}" "$payload" >/dev/null
}

ensure_itil_category() {
    local name="$1"
    local code="$2"
    local category_id

    category_id="$(find_category_id_by_name "$name")"
    if [[ -n "$category_id" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$category_id" \
                --arg name "$name" \
                --arg code "$code" \
                '{input:{id:$id, name:$name, code:$code, is_helpdeskvisible:1, is_incident:1, is_request:1}}'
        )"
        glpi_api PUT "/ITILCategory/${category_id}" "$payload" >/dev/null
        printf '%s\n' "$category_id"
        return 0
    fi

    local payload response
    payload="$(
        jq -n \
            --arg name "$name" \
            --arg code "$code" \
            '{input:{name:$name, code:$code, is_helpdeskvisible:1, is_incident:1, is_request:1}}'
    )"
    response="$(glpi_api POST '/ITILCategory/' "$payload")"
    category_id="$(printf '%s' "$response" | jq -r '.id // empty')"
    if [[ -z "$category_id" ]]; then
        echo "Falha ao criar categoria ${name} no GLPI." >&2
        printf '%s\n' "$response" >&2
        exit 1
    fi

    printf '%s\n' "$category_id"
}

ensure_ticket() {
    local external_id="$1"
    local title="$2"
    local description="$3"
    local requester_id="$4"
    local assignee_id="$5"
    local status="$6"
    local priority="$7"
    local category_id="${8:-0}"
    local legacy_title="${9:-}"
    local ticket_id

    ticket_id="$(
        db_query "SELECT id FROM glpi_tickets WHERE externalid = $(sql_quote "$external_id") AND is_deleted = 0 ORDER BY id DESC LIMIT 1;"
    )"
    if [[ -z "$ticket_id" ]]; then
        ticket_id="$(find_ticket_id_by_name "$title")"
    fi
    if [[ -z "$ticket_id" && -n "$legacy_title" ]]; then
        ticket_id="$(find_ticket_id_by_name "$legacy_title")"
    fi

    if [[ -z "$ticket_id" ]]; then
        local create_payload create_response
        create_payload="$(
            jq -n \
                --arg name "$title" \
                --arg content "$description" \
                --arg externalid "$external_id" \
                --argjson priority "$priority" \
                --argjson category_id "$category_id" \
                --argjson requester_id "$requester_id" \
                '{input:{name:$name, content:$content, externalid:$externalid, priority:$priority, itilcategories_id:$category_id, _users_id_requester:$requester_id}}'
        )"
        create_response="$(glpi_api POST '/Ticket/' "$create_payload")"
        ticket_id="$(printf '%s' "$create_response" | jq -r '.id // empty')"
        if [[ -z "$ticket_id" ]]; then
            echo "Falha ao criar ticket ${title} no GLPI." >&2
            printf '%s\n' "$create_response" >&2
            exit 1
        fi
    fi

    local update_payload
    update_payload="$(
        jq -n \
            --argjson id "$ticket_id" \
            --arg name "$title" \
            --arg content "$description" \
            --arg externalid "$external_id" \
            --argjson status "$status" \
            --argjson priority "$priority" \
            --argjson category_id "$category_id" \
            --argjson requester_id "$requester_id" \
            --argjson assignee_id "$assignee_id" \
            '{input:{id:$id, name:$name, content:$content, externalid:$externalid, status:$status, priority:$priority, itilcategories_id:$category_id, _users_id_requester:$requester_id, _users_id_assign:$assignee_id}}'
    )"
    glpi_api PUT "/Ticket/${ticket_id}" "$update_payload" >/dev/null

    printf '%s\n' "$ticket_id"
}

set_ticket_location() {
    local ticket_id="$1"
    local location_id="$2"

    if [[ -z "$location_id" || "$location_id" == "0" ]]; then
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --argjson id "$ticket_id" \
            --argjson location_id "$location_id" \
            '{input:{id:$id, locations_id:$location_id}}'
    )"
    glpi_api PUT "/Ticket/${ticket_id}" "$payload" >/dev/null
}

ensure_ticket_group_link() {
    local ticket_id="$1"
    local group_id="$2"
    local link_id

    link_id="$(
        db_query "SELECT id FROM glpi_groups_tickets WHERE tickets_id = ${ticket_id} AND type = 2 LIMIT 1;"
    )"

    if [[ -n "$link_id" ]]; then
        local current_group_id
        current_group_id="$(
            db_query "SELECT groups_id FROM glpi_groups_tickets WHERE id = ${link_id} LIMIT 1;"
        )"
        if [[ "$current_group_id" == "$group_id" ]]; then
            return 0
        fi

        local payload
        payload="$(
            jq -n \
                --argjson id "$link_id" \
                --argjson ticket_id "$ticket_id" \
                --argjson group_id "$group_id" \
                '{input:{id:$id, tickets_id:$ticket_id, groups_id:$group_id, type:2}}'
        )"
        glpi_api PUT "/Group_Ticket/${link_id}" "$payload" >/dev/null
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --argjson ticket_id "$ticket_id" \
            --argjson group_id "$group_id" \
            '{input:{tickets_id:$ticket_id, groups_id:$group_id, type:2, use_notification:1}}'
    )"
    glpi_api POST '/Group_Ticket/' "$payload" >/dev/null
}

ensure_ticket_item_link() {
    local ticket_id="$1"
    local item_type="$2"
    local item_id="$3"
    local existing_link

    existing_link="$(
        db_query "SELECT id FROM glpi_items_tickets WHERE tickets_id = ${ticket_id} AND itemtype = $(sql_quote "$item_type") AND items_id = ${item_id} LIMIT 1;"
    )"
    if [[ -n "$existing_link" ]]; then
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --arg itemtype "$item_type" \
            --argjson items_id "$item_id" \
            --argjson tickets_id "$ticket_id" \
            '{input:{itemtype:$itemtype, items_id:$items_id, tickets_id:$tickets_id}}'
    )"
    glpi_api POST '/Item_Ticket/' "$payload" >/dev/null
}

ensure_ticket_followup() {
    local ticket_id="$1"
    local author_id="$2"
    local content="$3"
    local existing_followup

    existing_followup="$(
        db_query "SELECT id FROM glpi_itilfollowups WHERE itemtype = 'Ticket' AND items_id = ${ticket_id} AND content = $(sql_quote "$content") LIMIT 1;"
    )"
    if [[ -n "$existing_followup" ]]; then
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --argjson ticket_id "$ticket_id" \
            --argjson author_id "$author_id" \
            --arg content "$content" \
            '{input:{itemtype:"Ticket", items_id:$ticket_id, users_id:$author_id, content:$content}}'
    )"
    glpi_api POST '/ITILFollowup/' "$payload" >/dev/null
}

ensure_ticket_solution() {
    local ticket_id="$1"
    local author_id="$2"
    local content="$3"
    local existing_solution
    local current_status
    local reopened_for_solution=0

    existing_solution="$(
        db_query "SELECT id FROM glpi_itilsolutions WHERE itemtype = 'Ticket' AND items_id = ${ticket_id} AND content = $(sql_quote "$content") LIMIT 1;"
    )"
    if [[ -n "$existing_solution" ]]; then
        return 0
    fi

    current_status="$(
        db_query "SELECT status FROM glpi_tickets WHERE id = ${ticket_id} LIMIT 1;"
    )"

    if [[ "$current_status" == "5" || "$current_status" == "6" ]]; then
        local reopen_payload
        reopen_payload="$(
            jq -n \
                --argjson id "$ticket_id" \
                '{input:{id:$id, status:2}}'
        )"
        glpi_api PUT "/Ticket/${ticket_id}" "$reopen_payload" >/dev/null
        reopened_for_solution=1
    fi

    local payload
    payload="$(
        jq -n \
            --argjson ticket_id "$ticket_id" \
            --argjson author_id "$author_id" \
            --arg content "$content" \
            '{input:{itemtype:"Ticket", items_id:$ticket_id, users_id:$author_id, content:$content}}'
    )"
    glpi_api POST '/ITILSolution/' "$payload" >/dev/null

    if [[ "$reopened_for_solution" == "1" ]]; then
        local restore_payload
        restore_payload="$(
            jq -n \
                --argjson id "$ticket_id" \
                --argjson status "$current_status" \
                '{input:{id:$id, status:$status}}'
        )"
        glpi_api PUT "/Ticket/${ticket_id}" "$restore_payload" >/dev/null
    fi
}

ensure_ticket_task() {
    local ticket_id="$1"
    local technician_user_id="$2"
    local group_id="$3"
    local content="$4"
    local actiontime="${5:-900}"
    local state="${6:-2}"
    local existing_task

    existing_task="$(
        db_query "SELECT id FROM glpi_tickettasks WHERE tickets_id = ${ticket_id} AND content = $(sql_quote "$content") LIMIT 1;"
    )"
    if [[ -n "$existing_task" ]]; then
        local payload
        payload="$(
            jq -n \
                --argjson id "$existing_task" \
                --argjson ticket_id "$ticket_id" \
                --argjson technician_user_id "$technician_user_id" \
                --argjson group_id "$group_id" \
                --arg content "$content" \
                --argjson actiontime "$actiontime" \
                --argjson state "$state" \
                '{input:{id:$id, tickets_id:$ticket_id, content:$content, users_id_tech:$technician_user_id, groups_id_tech:$group_id, actiontime:$actiontime, state:$state}}'
        )"
        glpi_api PUT "/TicketTask/${existing_task}" "$payload" >/dev/null
        return 0
    fi

    local payload
    payload="$(
        jq -n \
            --argjson ticket_id "$ticket_id" \
            --argjson technician_user_id "$technician_user_id" \
            --argjson group_id "$group_id" \
            --arg content "$content" \
            --argjson actiontime "$actiontime" \
            --argjson state "$state" \
            '{input:{tickets_id:$ticket_id, content:$content, users_id_tech:$technician_user_id, groups_id_tech:$group_id, actiontime:$actiontime, state:$state}}'
    )"
    glpi_api POST '/TicketTask/' "$payload" >/dev/null
}

cleanup_legacy_ticket_duplicates() {
    local title="$1"
    local keep_id="$2"

    db_query "UPDATE glpi_tickets SET is_deleted = 1 WHERE name = $(sql_quote "$title") AND externalid IS NULL AND id <> ${keep_id};" >/dev/null
}

write_identity_file() {
    local maria_id="$1"
    local carlos_id="$2"
    local ana_id="$3"
    local paula_id="$4"
    local bruno_id="$5"
    local renata_id="$6"
    local luciana_id="$7"
    local rafael_id="$8"
    local patricia_id="$9"
    local fabio_id="${10}"

    cat >"$IDENTITY_FILE" <<EOF
{
  "users": [
    {
            "phone_number": "+5521997775269",
            "external_id": "user-maria-santos",
      "display_name": "Maria Santos",
      "role": "user",
      "team": "financeiro",
      "glpi_user_id": ${maria_id},
      "active": true
    },
    {
      "phone_number": "+5511977776666",
      "external_id": "user-carlos-lima",
      "display_name": "Carlos Lima",
            "role": "user",
      "team": "recepcao",
      "glpi_user_id": ${carlos_id},
      "active": true
    },
    {
      "phone_number": "+5511912345678",
      "external_id": "tech-ana-souza",
      "display_name": "Ana Souza",
      "role": "technician",
      "team": "infraestrutura",
            "glpi_user_id": ${ana_id},
      "active": true
    },
    {
            "phone_number": "+5521972008679",
      "external_id": "supervisor-paula-almeida",
      "display_name": "Paula Almeida",
      "role": "supervisor",
      "team": "service-desk",
      "glpi_user_id": ${paula_id},
      "active": true
        },
    {
      "phone_number": "+5511966665555",
      "external_id": "user-bruno-costa",
      "display_name": "Bruno Costa",
      "role": "user",
      "team": "operacoes",
      "glpi_user_id": ${bruno_id},
      "active": true
    },
    {
            "phone_number": "+5511944443333",
      "external_id": "user-renata-melo",
      "display_name": "Renata Melo",
      "role": "user",
      "team": "redes",
      "glpi_user_id": ${renata_id},
      "active": true
    },
    {
      "phone_number": "+5511955554444",
      "external_id": "user-luciana-prado",
      "display_name": "Luciana Prado",
      "role": "user",
      "team": "financeiro",
      "glpi_user_id": ${luciana_id},
      "active": true
    },
    {
      "phone_number": "+5511933332222",
      "external_id": "user-rafael-nunes",
      "display_name": "Rafael Nunes",
      "role": "user",
      "team": "seguranca",
      "glpi_user_id": ${rafael_id},
      "active": true
    },
    {
      "phone_number": "+5511922221111",
      "external_id": "user-patricia-gomes",
      "display_name": "Patricia Gomes",
      "role": "user",
      "team": "administrativo",
      "glpi_user_id": ${patricia_id},
      "active": true
    },
    {
      "phone_number": "+5511910101010",
      "external_id": "user-fabio-teixeira",
      "display_name": "Fabio Teixeira",
      "role": "user",
      "team": "logistica",
      "glpi_user_id": ${fabio_id},
      "active": true
    }
  ]
}
EOF
}

ensure_named_existing_user 4 "Ana" "Souza" "+5511912345678"
ensure_named_existing_user 2 "Paula" "Almeida" "+5521972008679"

MARIA_ID="$(ensure_user "maria.santos" "Maria" "Santos" "+5521997775269" "lab.probe.user")"
CARLOS_ID="$(ensure_user "carlos.lima" "Carlos" "Lima" "+5511977776666")"
BRUNO_ID="$(ensure_user "bruno.costa" "Bruno" "Costa" "+5511966665555")"
RENATA_ID="$(ensure_user "renata.melo" "Renata" "Melo" "+5511944443333")"
LUCIANA_ID="$(ensure_user "luciana.prado" "Luciana" "Prado" "+5511955554444")"
RAFAEL_ID="$(ensure_user "rafael.nunes" "Rafael" "Nunes" "+5511933332222")"
PATRICIA_ID="$(ensure_user "patricia.gomes" "Patricia" "Gomes" "+5511922221111")"
FABIO_ID="$(ensure_user "fabio.teixeira" "Fabio" "Teixeira" "+5511910101010")"

TI_GROUP_ID="$(ensure_group "TI" 0 "TI" 0)"
SERVICE_DESK_GROUP_ID="$(ensure_group "Service Desk" "$TI_GROUP_ID" "SD" 0)"
SERVICE_DESK_N1_GROUP_ID="$(ensure_group "N1" "$SERVICE_DESK_GROUP_ID" "SD-N1" 1)"
SERVICE_DESK_ACCESS_GROUP_ID="$(ensure_group "Acessos" "$SERVICE_DESK_GROUP_ID" "SD-ACCESS" 1)"
INFRA_ROOT_GROUP_ID="$(ensure_group "Infraestrutura" "$TI_GROUP_ID" "INFRA" 0)"
INFRA_N1_GROUP_ID="$(ensure_group "N1" "$INFRA_ROOT_GROUP_ID" "INFRA-N1" 1)"
NOC_ROOT_GROUP_ID="$(ensure_group "NOC" "$TI_GROUP_ID" "NOC" 0)"
NOC_CRITICAL_GROUP_ID="$(ensure_group "Critico" "$NOC_ROOT_GROUP_ID" "NOC-CRIT" 1)"

FINANCEIRO_GROUP_ID="$(ensure_group "financeiro" 0 "FIN" 0)"
RECEPCAO_GROUP_ID="$(ensure_group "recepcao" 0 "RECEP" 0)"
INFRA_TEAM_GROUP_ID="$(ensure_group "infraestrutura" 0 "TEAM-INFRA" 0)"
SERVICE_DESK_TEAM_GROUP_ID="$(ensure_group "service-desk" 0 "TEAM-SD" 0)"
OPERACOES_GROUP_ID="$(ensure_group "operacoes" 0 "OPS" 0)"
REDES_GROUP_ID="$(ensure_group "redes" 0 "NET" 0)"
SEGURANCA_GROUP_ID="$(ensure_group "seguranca" 0 "SEC" 0)"
ADMINISTRATIVO_GROUP_ID="$(ensure_group "administrativo" 0 "ADM" 0)"
LOGISTICA_GROUP_ID="$(ensure_group "logistica" 0 "LOG" 0)"

ensure_primary_group_for_user "$MARIA_ID" "$FINANCEIRO_GROUP_ID"
ensure_primary_group_for_user "$CARLOS_ID" "$RECEPCAO_GROUP_ID"
ensure_primary_group_for_user 4 "$INFRA_TEAM_GROUP_ID"
ensure_primary_group_for_user 2 "$SERVICE_DESK_TEAM_GROUP_ID" 1 0
ensure_primary_group_for_user "$BRUNO_ID" "$OPERACOES_GROUP_ID"
ensure_primary_group_for_user "$RENATA_ID" "$REDES_GROUP_ID"
ensure_primary_group_for_user "$LUCIANA_ID" "$FINANCEIRO_GROUP_ID"
ensure_primary_group_for_user "$RAFAEL_ID" "$SEGURANCA_GROUP_ID"
ensure_primary_group_for_user "$PATRICIA_ID" "$ADMINISTRATIVO_GROUP_ID"
ensure_primary_group_for_user "$FABIO_ID" "$LOGISTICA_GROUP_ID"

MATRIZ_LOCATION_ID="$(ensure_location "Matriz" 0 "Matriz" "" "Sao Paulo")"
DATACENTER_LOCATION_ID="$(ensure_location "Datacenter" "$MATRIZ_LOCATION_ID" "Matriz" "Sala Cofre" "Sao Paulo")"
FINANCEIRO_LOCATION_ID="$(ensure_location "Financeiro" "$MATRIZ_LOCATION_ID" "Matriz" "Financeiro" "Sao Paulo")"
RECEPCAO_LOCATION_ID="$(ensure_location "Recepcao" "$MATRIZ_LOCATION_ID" "Matriz" "Recepcao" "Sao Paulo")"
OPERACOES_LOCATION_ID="$(ensure_location "Operacoes" "$MATRIZ_LOCATION_ID" "Matriz" "Operacoes" "Sao Paulo")"
SEGURANCA_LOCATION_ID="$(ensure_location "Seguranca" "$MATRIZ_LOCATION_ID" "Matriz" "Seguranca" "Sao Paulo")"
NOC_LOCATION_ID="$(ensure_location "NOC" "$MATRIZ_LOCATION_ID" "Matriz" "NOC" "Sao Paulo")"

ERP_WEB_ID="$(ensure_asset "Computer" "glpi_computers" "erp-web-01" "LAB-ERP-01" "Servidor web do ERP no laboratorio." "lab-probe-computer")"
DB_PROD_ID="$(ensure_asset "Computer" "glpi_computers" "db-prod-01" "LAB-DB-01" "Banco de dados principal do ERP.")"
AUTH_ID="$(ensure_asset "Computer" "glpi_computers" "auth-01" "LAB-AUTH-01" "Servidor de autenticacao corporativa.")"
PRINT_SPOOL_ID="$(ensure_asset "Computer" "glpi_computers" "print-spool-01" "LAB-SPOOL-01" "Servidor de spool de impressao.")"
VPN_EDGE_ID="$(ensure_asset "NetworkEquipment" "glpi_networkequipments" "vpn-edge-01" "LAB-VPN-01" "Gateway VPN do laboratorio.")"
ROUTER_EDGE_ID="$(ensure_asset "NetworkEquipment" "glpi_networkequipments" "router-edge-02" "LAB-ROUTER-02" "Roteador de borda secundario.")"
PRINTER_ID="$(ensure_asset "Printer" "glpi_printers" "printer-matriz-01" "LAB-PRINTER-01" "Impressora principal da recepcao.")"

set_item_location "Computer" "$ERP_WEB_ID" "$DATACENTER_LOCATION_ID"
set_item_location "Computer" "$DB_PROD_ID" "$DATACENTER_LOCATION_ID"
set_item_location "Computer" "$AUTH_ID" "$DATACENTER_LOCATION_ID"
set_item_location "Computer" "$PRINT_SPOOL_ID" "$DATACENTER_LOCATION_ID"
set_item_location "NetworkEquipment" "$VPN_EDGE_ID" "$NOC_LOCATION_ID"
set_item_location "NetworkEquipment" "$ROUTER_EDGE_ID" "$NOC_LOCATION_ID"
set_item_location "Printer" "$PRINTER_ID" "$RECEPCAO_LOCATION_ID"

ACCESS_CATEGORY_ID="$(ensure_itil_category "Acesso" "ACCESS")"
IDENTITY_CATEGORY_ID="$(ensure_itil_category "Identidade" "IDENTITY")"
PASSWORD_CATEGORY_ID="$(ensure_itil_category "Senha" "PASSWORD")"
NETWORK_CATEGORY_ID="$(ensure_itil_category "Rede" "NETWORK")"
SERVER_CATEGORY_ID="$(ensure_itil_category "Servidor" "SERVER")"
INFRA_CATEGORY_ID="$(ensure_itil_category "Infra" "INFRA")"

ERP_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-erp" \
        "ERP indisponível para o financeiro" \
        "Usuários do financeiro relatam indisponibilidade do ERP desde 08:10. Host relacionado: erp-web-01." \
        "$MARIA_ID" \
        4 \
        2 \
        4 \
        "$INFRA_CATEGORY_ID" \
        "lab-probe-ticket"
)"
VPN_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-vpn" \
        "VPN intermitente para equipes remotas" \
        "A conexão da VPN cai a cada poucos minutos para equipes remotas. Equipamento relacionado: vpn-edge-01." \
        "$RENATA_ID" \
        4 \
        3 \
        3 \
        "$NETWORK_CATEGORY_ID"
)"
PRINTER_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-printer" \
        "Impressora da recepção sem resposta" \
        "A impressora principal da recepção não responde após troca de toner. Equipamento relacionado: printer-matriz-01." \
        "$CARLOS_ID" \
        4 \
        1 \
        3 \
        "$INFRA_CATEGORY_ID"
)"
AUTH_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-auth" \
        "Falha de autenticação no portal corporativo" \
        "Usuários relatam falha ao autenticar no portal corporativo. Servidor relacionado: auth-01." \
        "$BRUNO_ID" \
        4 \
        4 \
        3 \
        "$ACCESS_CATEGORY_ID"
)"
PRINT_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-spool" \
        "Fila de impressão parada no spooler" \
        "Jobs de impressão não são entregues para a recepção. Servidor relacionado: print-spool-01." \
        "$PATRICIA_ID" \
        2 \
        2 \
        4 \
        "$SERVER_CATEGORY_ID"
)"
PERMISSION_TICKET_ID="$(
    ensure_ticket \
        "lab-ticket-permission" \
        "Validação de permissão concluída" \
        "Revisão de permissões realizada com sucesso para o usuário final. Ambiente relacionado: auth-01." \
        "$RAFAEL_ID" \
        2 \
        5 \
        2 \
        "$IDENTITY_CATEGORY_ID"
)"

set_ticket_location "$ERP_TICKET_ID" "$FINANCEIRO_LOCATION_ID"
set_ticket_location "$VPN_TICKET_ID" "$OPERACOES_LOCATION_ID"
set_ticket_location "$PRINTER_TICKET_ID" "$RECEPCAO_LOCATION_ID"
set_ticket_location "$AUTH_TICKET_ID" "$SEGURANCA_LOCATION_ID"
set_ticket_location "$PRINT_TICKET_ID" "$RECEPCAO_LOCATION_ID"
set_ticket_location "$PERMISSION_TICKET_ID" "$SEGURANCA_LOCATION_ID"

ensure_ticket_item_link "$ERP_TICKET_ID" "Computer" "$ERP_WEB_ID"
ensure_ticket_item_link "$VPN_TICKET_ID" "NetworkEquipment" "$VPN_EDGE_ID"
ensure_ticket_item_link "$PRINTER_TICKET_ID" "Printer" "$PRINTER_ID"
ensure_ticket_item_link "$AUTH_TICKET_ID" "Computer" "$AUTH_ID"
ensure_ticket_item_link "$PRINT_TICKET_ID" "Computer" "$PRINT_SPOOL_ID"
ensure_ticket_item_link "$PERMISSION_TICKET_ID" "Computer" "$AUTH_ID"

ensure_ticket_group_link "$ERP_TICKET_ID" "$INFRA_N1_GROUP_ID"
ensure_ticket_group_link "$VPN_TICKET_ID" "$INFRA_N1_GROUP_ID"
ensure_ticket_group_link "$PRINTER_TICKET_ID" "$INFRA_N1_GROUP_ID"
ensure_ticket_group_link "$AUTH_TICKET_ID" "$SERVICE_DESK_ACCESS_GROUP_ID"
ensure_ticket_group_link "$PRINT_TICKET_ID" "$INFRA_N1_GROUP_ID"
ensure_ticket_group_link "$PERMISSION_TICKET_ID" "$SERVICE_DESK_ACCESS_GROUP_ID"

ensure_ticket_followup "$ERP_TICKET_ID" 4 "Coletando logs do host afetado e validando correlação com o Zabbix."
ensure_ticket_followup "$VPN_TICKET_ID" 4 "Janela de observação aberta para validar perda de conectividade no gateway."
ensure_ticket_followup "$PRINTER_TICKET_ID" 4 "Equipe verificando spool local e conectividade da impressora."
ensure_ticket_followup "$AUTH_TICKET_ID" 4 "Aguardando evidências adicionais do usuário e revisão do serviço de autenticação."
ensure_ticket_followup "$PRINT_TICKET_ID" 2 "Supervisor acompanhando fila de impressão e priorizando o atendimento."
ensure_ticket_followup "$PERMISSION_TICKET_ID" 2 "Solicitação validada e encerrada com aceite do solicitante."

ensure_ticket_task "$ERP_TICKET_ID" 4 "$INFRA_N1_GROUP_ID" "Executar coleta inicial de logs e validar indisponibilidade do ERP no host erp-web-01." 1800 2
ensure_ticket_task "$VPN_TICKET_ID" 4 "$INFRA_N1_GROUP_ID" "Validar estabilidade do gateway vpn-edge-01 e evidências de perda de conectividade." 1200 2
ensure_ticket_task "$AUTH_TICKET_ID" 4 "$SERVICE_DESK_ACCESS_GROUP_ID" "Revisar credenciais, perfil e trilha de autenticação do portal corporativo." 900 2
ensure_ticket_task "$PRINT_TICKET_ID" 2 "$INFRA_N1_GROUP_ID" "Acompanhar fila do spooler e confirmar retomada da impressão para a recepção." 1200 2

ensure_ticket_solution "$PERMISSION_TICKET_ID" 2 "Permissões revisadas no perfil corporativo e acesso validado com o solicitante."

cleanup_legacy_ticket_duplicates "ERP indisponível para o financeiro" "$ERP_TICKET_ID"
cleanup_legacy_ticket_duplicates "VPN intermitente para equipes remotas" "$VPN_TICKET_ID"
cleanup_legacy_ticket_duplicates "Impressora da recepção sem resposta" "$PRINTER_TICKET_ID"
cleanup_legacy_ticket_duplicates "Falha de autenticação no portal corporativo" "$AUTH_TICKET_ID"
cleanup_legacy_ticket_duplicates "Fila de impressão parada no spooler" "$PRINT_TICKET_ID"
cleanup_legacy_ticket_duplicates "Validação de permissão concluída" "$PERMISSION_TICKET_ID"

write_identity_file \
    "$MARIA_ID" \
    "$CARLOS_ID" \
    4 \
    2 \
    "$BRUNO_ID" \
    "$RENATA_ID" \
    "$LUCIANA_ID" \
    "$RAFAEL_ID" \
    "$PATRICIA_ID" \
    "$FABIO_ID"

echo "Seed do GLPI concluido."
echo "Usuarios operacionais: Ana Souza (id 4), Paula Almeida (id 2)"
echo "Usuarios finais: Maria=${MARIA_ID}, Carlos=${CARLOS_ID}, Bruno=${BRUNO_ID}, Renata=${RENATA_ID}, Luciana=${LUCIANA_ID}, Rafael=${RAFAEL_ID}, Patricia=${PATRICIA_ID}, Fabio=${FABIO_ID}"
echo "Categorias: acesso=${ACCESS_CATEGORY_ID}, identidade=${IDENTITY_CATEGORY_ID}, senha=${PASSWORD_CATEGORY_ID}, rede=${NETWORK_CATEGORY_ID}, servidor=${SERVER_CATEGORY_ID}, infra=${INFRA_CATEGORY_ID}"
echo "Grupos de fila: ServiceDesk-N1=${SERVICE_DESK_N1_GROUP_ID}, ServiceDesk-Acessos=${SERVICE_DESK_ACCESS_GROUP_ID}, Infraestrutura-N1=${INFRA_N1_GROUP_ID}, NOC-Critico=${NOC_CRITICAL_GROUP_ID}"
echo "Grupos de time: financeiro=${FINANCEIRO_GROUP_ID}, recepcao=${RECEPCAO_GROUP_ID}, infraestrutura=${INFRA_TEAM_GROUP_ID}, service-desk=${SERVICE_DESK_TEAM_GROUP_ID}"
echo "Localizacoes: matriz=${MATRIZ_LOCATION_ID}, datacenter=${DATACENTER_LOCATION_ID}, financeiro=${FINANCEIRO_LOCATION_ID}, recepcao=${RECEPCAO_LOCATION_ID}, operacoes=${OPERACOES_LOCATION_ID}, seguranca=${SEGURANCA_LOCATION_ID}, noc=${NOC_LOCATION_ID}"
echo "Ativos: erp-web-01=${ERP_WEB_ID}, db-prod-01=${DB_PROD_ID}, auth-01=${AUTH_ID}, print-spool-01=${PRINT_SPOOL_ID}, vpn-edge-01=${VPN_EDGE_ID}, router-edge-02=${ROUTER_EDGE_ID}, printer-matriz-01=${PRINTER_ID}"
echo "Tickets: ERP=${ERP_TICKET_ID}, VPN=${VPN_TICKET_ID}, Printer=${PRINTER_TICKET_ID}, Auth=${AUTH_TICKET_ID}, Spool=${PRINT_TICKET_ID}, Permissao=${PERMISSION_TICKET_ID}"
echo "Arquivo de identidades atualizado em ${IDENTITY_FILE}."
