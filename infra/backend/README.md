# Backend Infra

## Escopo

Esta pasta deve concentrar a publicacao do backend FastAPI como aplicacao separada da stack legado do host.

## Responsabilidades

- definicao de runtime do backend;
- service unit, container spec ou processo equivalente;
- proxy reverso ou tunel controlado para webhooks;
- variaveis de ambiente por ambiente;
- healthcheck, logs e estrategia de restart.

## Artefatos esperados

- template de service unit;
- exemplo de proxy reverso;
- checklist de deploy;
- `.env.example` de infraestrutura, se o deploy sair do diretorio `backend/`;
- comandos de rollback e validacao.

## Dependencias

- Python 3.11+ ou runtime definido para container;
- acesso de saida a GLPI, Zabbix e WhatsApp;
- segredos injetados por ambiente;
- caminho de publicacao definido.

## Validacao minima

Depois do deploy, este bloco precisa validar:

- `GET /health`;
- abertura de ticket em modo mock;
- abertura de ticket em modo real;
- recebimento de webhook do WhatsApp;
- logs suficientes para auditoria basica.
