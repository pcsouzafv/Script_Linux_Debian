# Portas e Conectividade

## Objetivo

Este projeto precisa conviver com aplicações já publicadas em Docker. Por isso, nenhuma nova peça deve assumir portas comuns do host sem checagem prévia.

## Política atual

- Backend de desenvolvimento: usar `127.0.0.1` por padrão, nunca `0.0.0.0` sem necessidade explícita.
- Backend FastAPI: reservar a faixa `18001-18010` para execução local.
- Portas comuns de containers e serviços existentes não devem ser presumidas como livres.
- Serviços integrados como GLPI, Zabbix e WhatsApp são acessados por saída HTTP ou HTTPS; isso não exige novas portas de escuta no backend.

## Portas que já exigem atenção neste projeto

- `80`: Apache e possíveis proxies reversos ou containers web existentes.
- `443`: HTTPS do host ou proxies reversos.
- `3306`: MariaDB ou MySQL local ou em container com publicação no host.
- `10050`: Zabbix Agent.
- `10051`: Zabbix Server.
- `6443`: Kubernetes API quando o cluster local for inicializado.

## Backend local

O backend agora usa as variáveis abaixo:

- `HELPDESK_API_HOST`
- `HELPDESK_API_PORT`
- `HELPDESK_API_PORT_MAX`
- `HELPDESK_API_PORT_STRICT`

O script [backend/run_dev.sh](../backend/run_dev.sh) sobe a API com estas regras:

- Tenta primeiro a porta definida em `HELPDESK_API_PORT`.
- Se a porta estiver ocupada e `HELPDESK_API_PORT_STRICT=false`, procura a próxima porta livre até `HELPDESK_API_PORT_MAX`.
- Se `HELPDESK_API_PORT_STRICT=true`, falha imediatamente quando a porta configurada estiver ocupada.

## Execução recomendada

```bash
cd backend
./run_dev.sh
```

Para apenas verificar a porta que será usada:

```bash
cd backend
./run_dev.sh --dry-run
```

## Instalador Debian

O instalador [install_debian12_full_stack.sh](../install_debian12_full_stack.sh) agora valida previamente se as portas críticas do host já estão ocupadas. Se encontrar conflito, ele aborta antes de alterar Apache, Zabbix ou MariaDB.

Isso é importante quando o host já executa aplicações publicadas por Docker.

## Recomendação operacional

- Em host compartilhado com Docker, prefira manter o backend apenas em `127.0.0.1` e publicar externamente via reverse proxy quando necessário.
- Antes de rodar o instalador de stack completa, revise o uso real das portas do host.
- Se GLPI ou Zabbix já existirem em containers, não execute o instalador sem adaptar o plano de portas primeiro.
