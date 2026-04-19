# Observability Infra

## Escopo

Esta pasta deve concentrar a observabilidade da propria plataforma, nao a observabilidade dos ativos monitorados pelo Zabbix.

## Objetivo

Monitorar a saude operacional do backend e da camada de automacao.

## Itens previstos

- metricas da API;
- logs estruturados;
- trilhas de auditoria;
- alertas sobre falhas de integracao;
- dashboards operacionais da plataforma.

## Ferramentas candidatas

- Prometheus;
- Grafana;
- Loki;
- Alertmanager ou equivalente.

## Artefatos esperados

- manifests ou compose files;
- dashboards versionados;
- regras de alerta;
- convencoes de labels e correlacao com tickets.
