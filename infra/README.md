# Infra

Esta pasta organiza os artefatos de implantacao por aplicacao, para evitar que o repositorio misture:

- bootstrap de host;
- deploy de aplicacao;
- integracoes externas;
- automacoes operacionais.

## Objetivo

Separar a implantacao da plataforma em blocos claros:

- `glpi/`: ativos de deploy, configuracao e operacao do GLPI;
- `zabbix/`: ativos de deploy, configuracao e operacao do Zabbix;
- `evolution/`: apoio operacional para instancias adicionais da Evolution API ja existente no host;
- `helpdesk-lab/`: laboratorio isolado em Docker Compose para GLPI + Zabbix, com PostgreSQL e Redis de apoio operacional, sem tocar nos containers atuais do host;
- `backend/`: publicacao do backend FastAPI, proxy reverso, variaveis e execucao;
- `automation-runner/`: camada de execucao segura das automacoes homologadas;
- `observability/`: monitoracao da propria plataforma.

## Estado atual

Neste momento a pasta e um scaffold operacional. Ela documenta onde cada bloco deve evoluir no repositorio antes de introduzirmos manifests, playbooks, services e templates reais.

## Convencoes

Cada subpasta deve concentrar apenas os artefatos daquele componente.

- incluir um `README.md` explicando escopo, dependencias e fluxo de deploy;
- incluir exemplos versionados como `.env.example`, `*.example`, manifests ou templates;
- nunca versionar segredos reais;
- manter naming simples e previsivel;
- preferir separar `dev`, `lab` e `prod` quando os manifests comecarem a divergir.

## Fluxo recomendado

1. Preparar o host base com o instalador atual.
2. Validar GLPI e Zabbix como sistemas oficiais.
3. Publicar o backend de orquestracao como aplicacao separada.
4. Adicionar a camada de automacao segura.
5. Adicionar observabilidade e operacao continua da plataforma.

## Proximos artefatos esperados

- templates de proxy reverso para o backend;
- service unit ou container spec do backend;
- playbooks de hardening e pos-instalacao para GLPI e Zabbix;
- catalogo inicial de automacoes homologadas;
- manifests ou compose files de apoio para observabilidade.
