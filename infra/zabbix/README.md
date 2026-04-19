# Zabbix Infra

## Escopo

Esta pasta deve concentrar os ativos de implantacao e operacao do Zabbix como fonte oficial de eventos e alertas.

## Responsabilidades

- configuracao do servidor Zabbix;
- banco e parametros de conexao;
- frontend web;
- agentes e itens minimos de monitoracao;
- API token para uso pelo backend;
- politicas de trigger e correlacao inicial.

## Artefatos esperados

- checklist de pos-instalacao;
- exemplos de configuracao;
- scripts ou playbooks de ajuste;
- padrao minimo de hosts, grupos e severidade;
- rotina de backup e rollback.

## Dependencias

- banco configurado;
- `zabbix-server` funcional;
- frontend acessivel;
- usuario tecnico com permissao para token de API.

## Integracao com o backend

Antes da integracao real, este bloco precisa entregar:

- URL da API JSON-RPC;
- token de API;
- validacao de consulta de eventos e hosts;
- criterio minimo de nomenclatura para facilitar correlacao por host ou servico.
