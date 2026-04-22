# Fase 5: Operacao Avancada

## Objetivo

Esta fase existe para transformar o backend de orquestracao em uma camada real de apoio operacional para service desk e infraestrutura.

Na pratica, isso significa sair de uma base boa de integracao e chegar a quatro capacidades visiveis:

- deteccao de incidentes em massa;
- relatorios de fila, backlog e eficiencia operacional;
- pos-mortem semi-automatico;
- recomendacoes por historico e recorrencia com base duravel.

## Leitura objetiva do estado atual

Status atual: parcial.

Leitura curta:

- a base de dados operacionais e analiticos ja existe;
- a plataforma ja consegue enriquecer triagem com historico e sugerir resolucao por ticket;
- a fila administrativa ja tem resumo operacional proprio;
- a primeira fatia de relatorio operacional de tickets ja existe via resumo agregado de `ticket_analytics_snapshot`;
- a parte mais visivel da Fase 5 ainda nao foi fechada: relatorios completos, incidentes em massa, pos-mortem e camada real de conhecimento.

## O que ja existe hoje

### 1. Base operacional duravel

Ja existe store operacional com sessao, auditoria e estado administrativo.

Arquivos principais:

- `backend/app/services/operational_store.py`
- `backend/app/api/routes/helpdesk.py`

Capacidades ja entregues:

- gravacao de eventos de auditoria;
- consulta protegida de eventos por filtros fechados;
- persistencia em PostgreSQL com fallback em memoria;
- politicas de retencao e sanitizacao de payload administrativo.

### 2. Base analitica de tickets

Ja existe snapshot analitico para tickets do GLPI, com sincronizacao e backfill historico.

Arquivos principais:

- `backend/app/services/ticket_analytics_store.py`
- `backend/app/services/glpi_analytics.py`
- `backend/app/services/glpi_backfill.py`

Capacidades ja entregues:

- materializacao de `ticket_analytics_snapshot`;
- enriquecimento com `ticket_opened` vindo da auditoria duravel;
- armazenamento de `category_name`, `service_name`, `source_channel`, `external_id` e correlacao basica.

### 3. Assistencia de resolucao por ticket

Ja existe advice de resolucao por ticket, com heuristica local, historico do GLPI, snapshot analitico e LLM seguro quando configurado.

Arquivos principais:

- `backend/app/orchestration/helpdesk.py`
- `backend/app/services/triage.py`
- `backend/app/api/routes/helpdesk.py`
- `backend/tests/test_triage_resolution.py`

Capacidades ja entregues:

- endpoint `GET /api/v1/helpdesk/ai/tickets/{ticket_id}/resolution`;
- `resolution_hints` e `similar_incidents` por categoria e contexto;
- resumo e proximas acoes sugeridas para `/ticket`, `/comment` e `/status`;
- reaproveitamento de `solution` estruturada quando o ticket vai para `solved`.

### 4. Resumo operacional da fila administrativa

Ja existe visibilidade operacional da fila de automacao homologada.

Arquivos principais:

- `backend/app/orchestration/helpdesk.py`
- `backend/app/schemas/helpdesk.py`
- `backend/tests/test_health.py`

Capacidades ja entregues:

- contagem por `approval_status` e `execution_status`;
- profundidade da fila principal e da dead-letter;
- idade dos jobs pendentes, enfileirados, em execucao e com retry.

## O que esta parcial

### 1. Relatorios operacionais

Hoje existe relatorio operacional da automacao e um primeiro resumo operacional de tickets, mas ainda nao relatorio operacional completo da operacao.

Ja cobre:

- fila administrativa de jobs;
- resumo agregado de tickets com backlog, distribuicao e sinais basicos de eficiencia;
- retry, dead-letter e envelhecimento da fila.

Ainda nao cobre de forma completa:

- backlog de tickets por fila;
- aging por status;
- volume por categoria e servico;
- taxa de atendimento, reabertura e encaminhamento;
- eficiencia operacional por janela, fila ou time.

### 2. Recomendacoes por historico e recorrencia

Hoje a plataforma ja usa casos similares para enriquecer triagem e resolucao, mas ainda no nivel de advice por ticket.

Ja cobre:

- comparacao com snapshots parecidos;
- sugestao de fila e primeira acao baseada em historico recente.

Ainda falta:

- consolidar recorrencia por servico, ativo, categoria e fila;
- produzir recomendacao operacional agregada, nao so contextual ao ticket atual;
- expor isso como relatorio, dashboard ou endpoint administrativo especifico.

### 3. Enriquecimento analitico do GLPI

O snapshot existe, mas a entrada ainda e mais pobre do que deveria.

Gap principal atual:

- tickets do backend ainda nem sempre persistem bem `externalid`, `itilcategories_id`, `requesttypes_id` e vinculo com item de inventario.

Sem isso, a Fase 5 fica com boa estrutura de leitura, mas com qualidade de dado abaixo do ideal para BI, recorrencia e correlacao mais forte.

### 4. Observabilidade da plataforma

Hoje existe o escopo e a direcao em `infra/observability/README.md`, mas ainda nao ha stack versionada de observabilidade da propria plataforma.

Ainda faltam:

- metricas do backend e worker;
- dashboards versionados;
- regras de alerta;
- logs estruturados consolidados fora do Zabbix.

## O que ainda nao apareceu no repositorio

### 1. Deteccao de incidentes em massa

Nao ha implementacao dedicada em `backend/app/` ou `backend/tests/` para agrupar incidentes por servico, localidade, cluster, ativo ou janela de tempo e sugerir incidente pai.

### 2. Pos-mortem semi-automatico

Nao ha fluxo dedicado para consolidar historico de ticket, auditoria, followups, solution e eventos em um resumo de pos-incidente reutilizavel.

### 3. Camada real de conhecimento

Nao ha ainda base de conhecimento indexada, `RAG`, indice vetorial nem servico proprio de consulta a FAQ e runbooks.

## Sequencia recomendada de implementacao

### Bloco 1: fechar a base de dados da fase

1. Enriquecer os tickets originados do backend com `externalid`, categoria, tipo de origem e item vinculado.
2. Evoluir o snapshot analitico para guardar medidas operacionais reutilizaveis em relatorios.
3. Popular melhor o laboratorio com categorias, grupos, localizacoes, solucoes, tarefas e satisfacao.

### Bloco 2: entregar visibilidade operacional real

1. Criar endpoint ou job de relatorio operacional de tickets.
2. Expor backlog, aging, throughput, reabertura e distribuicao por fila e categoria.
3. Versionar dashboards e alertas minimos da propria plataforma.

### Bloco 3: entregar inteligencia operacional

1. Implementar deteccao de incidentes em massa por heuristica inicial.
2. Consolidar recorrencia por servico, categoria, ativo e fila.
3. Gerar recomendacoes operacionais agregadas, nao apenas por ticket isolado.

### Bloco 4: fechar conhecimento e pos-incidente

1. Indexar FAQ, runbooks e artigos tecnicos.
2. Implementar `RAG` para consulta controlada.
3. Gerar pos-mortem semi-automatico com base em ticket, auditoria e solution.

## Criterios de saida da Fase 5

Considere a Fase 5 fechada quando estes pontos estiverem juntos:

- existe relatorio operacional completo de tickets e fila, nao apenas da automacao;
- existe deteccao minima de incidente em massa com regra clara e evidenciavel;
- existe recomendacao por recorrencia baseada em historico duravel, nao so em heuristica local;
- existe pos-mortem semi-automatico reutilizando os dados do ticket e da auditoria;
- existe camada real de conhecimento para alimentar resumo, classificacao e sugestao.

## Leitura executiva

- base tecnica: boa;
- dados operacionais: ja sustentam a fase;
- inteligencia operacional agregada: ainda incompleta;
- fase atual: pronta para continuar, mas nao pronta para ser considerada concluida.
