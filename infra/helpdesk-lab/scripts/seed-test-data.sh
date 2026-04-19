#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/bootstrap-integrations.sh"
"$SCRIPT_DIR/seed-glpi.sh"
"$SCRIPT_DIR/seed-zabbix.sh"
"$SCRIPT_DIR/seed-zabbix-runtime.sh"

echo "Laboratorio pronto para testes integrados com backend, GLPI e Zabbix."
