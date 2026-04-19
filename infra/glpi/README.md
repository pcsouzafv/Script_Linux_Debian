# GLPI Infra

## Escopo

Esta pasta deve concentrar os artefatos de implantacao e operacao do GLPI como sistema oficial de ITSM da plataforma.

## Responsabilidades

- configuracao de banco e aplicacao;
- pos-instalacao e hardening;
- backup e restauracao;
- configuracao de API para integracao com o backend;
- proxy reverso, TLS e politicas de exposicao, se necessario.

## Nao colocar aqui

- regras do backend FastAPI;
- automacoes genericas sem relacao com GLPI;
- segredos reais.

## Artefatos esperados

- `README.md` operacional por ambiente;
- exemplos de parametros ou variaveis;
- scripts ou playbooks de pos-instalacao;
- checklist de habilitacao da API REST;
- rotina de backup.

## Dependencias

- MariaDB funcional;
- GLPI instalado e acessivel;
- usuario tecnico com permissao para gerar tokens de API.

## Integracao com o backend

Antes de ligar o backend em modo real, este bloco precisa entregar:

- URL base da API;
- `app_token`;
- `user_token`;
- validacao de permissao para criar e consultar tickets.
