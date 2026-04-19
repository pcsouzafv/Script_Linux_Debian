# Aplicacoes e Estrategia de Implantacao

## Visao geral

Esta plataforma nao e uma unica aplicacao. O resultado final depende de varias pecas, algumas instaladas localmente, outras externas, e outras ainda nao provisionadas pelo repositorio atual.

Hoje o repositorio cobre bem duas frentes:

- instalacao base de host dedicado com GLPI e Zabbix;
- backend MVP de orquestracao para integrar WhatsApp, GLPI e Zabbix.

As automacoes completas exigem uma topologia por camadas.

## Aplicacoes da solucao

| Camada | Aplicacao ou servico | Papel na plataforma | Situacao no repositorio |
| --- | --- | --- | --- |
| Base do host | Debian 12 + Apache + PHP + MariaDB + Docker + Kubernetes | Fundacao do ambiente | Instalador pronto em `install_debian12_full_stack.sh` |
| ITSM | GLPI | Tickets, filas, SLA, usuarios, inventario e ativos | Instalado pelo script base |
| Observabilidade | Zabbix Server + Frontend + Agent | Eventos, triggers, hosts e correlacao inicial | Instalado pelo script base |
| Orquestracao | Backend FastAPI | Regras, RBAC, webhooks, correlacao e integracoes | Ja implementado em `backend/` |
| Canal conversacional | WhatsApp Business API ou provedor homologado | Entrada e saida de mensagens | Integracao prevista; depende de credenciais externas |
| Execucao segura | Ansible, AWX ou Rundeck | Rodar playbooks homologados com auditoria | Arquitetura definida, provisionamento ainda pendente |
| Persistencia operacional | PostgreSQL | Sessao, auditoria, estado, cache funcional | Recomendado, ainda nao provisionado |
| Fila e tarefas | Redis ou RabbitMQ + worker | Jobs assincronos, retentativas, fila de automacoes | Recomendado, ainda nao provisionado |
| IA e conhecimento | LangGraph + RAG + indice vetorial | Triagem, resumo, classificacao e busca contextual | Planejado para fases futuras |
| Observabilidade da propria plataforma | Prometheus, Grafana, Loki ou equivalente | Monitorar o backend e os jobs | Planejado para fases futuras |

## O que o instalador atual faz e o que ele nao faz

O script [install_debian12_full_stack.sh](/home/ricardo/Script_Linux_Debian/install_debian12_full_stack.sh) prepara um host dedicado com:

- Apache e PHP para o frontend do GLPI e do Zabbix;
- MariaDB para bancos locais;
- Docker e Kubernetes como base de operacao futura;
- Zabbix Server, Agent e frontend;
- GLPI.

Ele nao faz hoje:

- deploy do backend FastAPI;
- configuracao da API oficial do WhatsApp;
- provisionamento de PostgreSQL para o backend;
- provisionamento de Redis ou RabbitMQ;
- instalacao de AWX, Rundeck ou esteira de automacoes homologadas;
- configuracao de LangGraph, RAG, banco vetorial ou monitoracao do proprio backend.

## Ordem recomendada de implantacao

### Fase 1: Plataforma base

Subir o host dedicado e instalar GLPI e Zabbix.

- Executar o instalador base apenas em host onde as portas criticas estejam livres.
- Concluir o setup web do GLPI.
- Concluir o setup do frontend do Zabbix.
- Validar acesso web, banco e servicos locais.

### Fase 2: Backend de orquestracao

Subir o backend separadamente, com preferencia fora do Apache do host legado.

- Preparar Python e ambiente virtual em `backend/`.
- Rodar o backend primeiro em modo mock.
- Validar `GET /health`, abertura de ticket mock e webhook local.
- Depois preencher credenciais reais de GLPI, Zabbix e WhatsApp no `.env`.

### Fase 3: Integracoes externas

Ligar o backend aos sistemas oficiais.

- Habilitar API REST do GLPI e gerar `app_token` e `user_token`.
- Habilitar acesso a API do Zabbix e gerar token.
- Configurar webhook e credenciais do WhatsApp Business.
- Publicar o backend via reverse proxy ou tunel controlado, sem expor servicos internos desnecessarios.

### Fase 4: Camada de automacao segura

Adicionar o motor de execucao de runbooks.

- Escolher entre Ansible direto, AWX ou Rundeck.
- Definir catalogo inicial de playbooks homologados.
- Amarrar cada automacao a papel, aprovacao, auditoria e rollback.
- Integrar o backend a essa camada sem liberar shell arbitrario.

### Fase 5: IA operacional e conhecimento

Acrescentar a camada de triagem e assistencia.

- Subir banco operacional proprio do backend.
- Subir fila para jobs assincronos.
- Indexar FAQ, artigos e runbooks.
- Adicionar agentes apenas para resumo, classificacao e sugestao antes de liberar automacoes mais sensiveis.

## Topologia minima recomendada para o MVP

Para nao misturar tudo no mesmo processo, o MVP operacional deve considerar pelo menos estas aplicacoes:

1. GLPI.
2. Zabbix.
3. Backend de orquestracao.
4. Provedor oficial de WhatsApp.

Se o objetivo incluir automacao de infraestrutura de forma segura, entram mais duas pecas quase obrigatorias:

1. Motor de execucao de runbooks.
2. Banco operacional dedicado ao backend.

## Separacao de responsabilidade por aplicacao

- GLPI guarda o ciclo de vida do ticket.
- Zabbix guarda eventos e alertas.
- Backend decide fluxo, identidade, permissao e auditoria.
- WhatsApp so transporta mensagens.
- AWX, Rundeck ou Ansible executa automacoes aprovadas.
- Banco operacional guarda estado proprio da plataforma, sem acoplamento ao banco do GLPI.

## Estrutura inicial criada no repositorio

O repositorio agora ja conta com a pasta `infra/` como ponto unico para evoluir os deploys por componente:

```text
.
├── backend/
├── docs/
├── infra/
│   ├── README.md
│   ├── glpi/
│   ├── zabbix/
│   ├── backend/
│   ├── automation-runner/
│   └── observability/
├── install_debian12_full_stack.sh
```

O guia raiz fica em [infra/README.md](/home/ricardo/Script_Linux_Debian/infra/README.md) e cada subpasta delimita o escopo de implantacao do seu respectivo componente.

Para laboratorio local em desktop, o repositorio agora tambem inclui [infra/helpdesk-lab/README.md](/home/ricardo/Script_Linux_Debian/infra/helpdesk-lab/README.md), com um `Docker Compose` isolado para `GLPI + Zabbix` sem tocar nos containers ja existentes do host.

## Decisao pratica para agora

O caminho mais pragmatico e tratar o projeto em dois blocos imediatos:

- bloco 1: host base + GLPI + Zabbix;
- bloco 2: backend de orquestracao com integracoes e webhook.

Depois disso, a implantacao das automacoes deve entrar como um terceiro bloco separado, com ferramenta propria de execucao e politica de aprovacao.
