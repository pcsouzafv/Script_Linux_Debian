# GLPI para Analytics e Machine Learning

## Objetivo

Este documento consolida o entendimento atual dos dados do GLPI usados pelo projeto e propõe uma estratégia prática de enriquecimento para três frentes:

- relatórios operacionais e gerenciais;
- preparação de base histórica para machine learning;
- aumento de contexto útil para a IA do orquestrador.

## Leitura real do laboratório

O laboratório ativo em `infra/helpdesk-lab` foi inspecionado diretamente no MySQL do GLPI.

Tabelas centrais confirmadas:

- `glpi_tickets`
- `glpi_tickets_users`
- `glpi_itilfollowups`
- `glpi_items_tickets`
- `glpi_users`
- `glpi_itilcategories`
- `glpi_requesttypes`
- `glpi_entities`
- `glpi_locations`

Volume atual observado no lab:

- `glpi_tickets`: 23
- `glpi_itilfollowups`: 33
- `glpi_users`: 13
- `glpi_items_tickets`: 16
- `glpi_itilcategories`: 0
- `glpi_tickettasks`: 0
- `glpi_ticketvalidations`: 0
- `glpi_ticketsatisfactions`: 0
- `glpi_groups`: 0
- `glpi_locations`: 0
- `glpi_entities`: 1
- `glpi_requesttypes`: 6
- `glpi_itilsolutions`: 0

Isso mostra que o GLPI já carrega o núcleo transacional suficiente para analytics, mas o laboratório ainda está pobre em dimensões e em trilha operacional rica.

## O que cada tabela entrega

### `glpi_tickets`

É a tabela fato principal do ITSM.

Campos mais úteis para analytics:

- `id`
- `entities_id`
- `name`
- `content`
- `date`
- `date_mod`
- `solvedate`
- `closedate`
- `takeintoaccountdate`
- `status`
- `urgency`
- `impact`
- `priority`
- `itilcategories_id`
- `requesttypes_id`
- `type`
- `time_to_resolve`
- `time_to_own`
- `users_id_lastupdater`
- `locations_id`
- `externalid`

Leitura prática:

- `status`, `urgency`, `impact` e `priority` já permitem métricas operacionais básicas.
- `date`, `solvedate` e `closedate` já permitem tempos de ciclo e aging.
- `externalid` é chave excelente para idempotência, integração e rastreabilidade.
- `itilcategories_id` e `locations_id` existem, mas hoje estão zerados no laboratório.

### `glpi_tickets_users`

Representa participantes do ticket.

Campos principais:

- `tickets_id`
- `users_id`
- `type`
- `alternative_email`

Leitura prática:

- `type = 1` representa solicitante.
- `type = 2` representa atendente atribuído.

Esse relacionamento é importante porque o payload do endpoint `/Ticket/{id}` nem sempre devolve corretamente solicitante e responsável. O próprio backend já foi ajustado para usar esse vínculo quando necessário.

### `glpi_itilfollowups`

É a linha do tempo textual do ticket.

Campos principais:

- `itemtype`
- `items_id`
- `users_id`
- `content`
- `is_private`
- `requesttypes_id`
- `date`
- `date_mod`

Leitura prática:

- é a principal fonte para NLP, resumo, classificação posterior e detecção de padrão de atendimento;
- também é a melhor fonte para construir features temporais como quantidade de interações, tempo entre interações e participação por papel.

### `glpi_items_tickets`

Liga tickets a itens do inventário.

Campos principais:

- `tickets_id`
- `itemtype`
- `items_id`

Leitura prática:

- já existe valor real nessa tabela no laboratório;
- ela conecta incidentes a `Computer`, `Printer` e `NetworkEquipment`;
- isso é crítico para relatório por ativo e para ML orientado a contexto de infraestrutura.

### `glpi_users`

Dimensão mestre de pessoas.

Campos principais:

- `id`
- `name`
- `firstname`
- `realname`
- `phone`
- `phone2`
- `mobile`
- `locations_id`
- `entities_id`
- `groups_id`
- `is_active`
- `users_id_supervisor`

Leitura prática:

- serve para identidade, segmentação por área e perfil operacional;
- hoje o laboratório usa telefone com valor analítico alto, porque ele amarra WhatsApp, GLPI e identidade local.

### `glpi_itilcategories`

Dimensão hierárquica de classificação.

Campos principais:

- `id`
- `name`
- `completename`
- `itilcategories_id`
- `level`
- `code`
- `groups_id`
- `users_id`
- `is_helpdeskvisible`

Leitura prática:

- é a melhor dimensão para agrupar tickets por problema, serviço ou domínio de atendimento;
- hoje está vazia no laboratório, o que limita muito relatório e treinamento supervisionado.

### `glpi_requesttypes`

Dimensão de canal/origem do chamado.

Valores observados:

- `1 = Helpdesk`
- `2 = E-Mail`
- `3 = Phone`
- `4 = Direct`
- `5 = Written`
- `6 = Other`

Leitura prática:

- isso já pode virar feature para análise de origem e produtividade por canal;
- hoje os tickets do backend estão caindo no padrão do GLPI e ainda não distinguem claramente WhatsApp como canal analítico próprio.

## Estado atual dos dados no laboratório

Há duas populações de tickets no lab:

- tickets seedados manualmente com `externalid`, vínculo de item e atribuição mais rica;
- tickets gerados pelo backend via WhatsApp, com assunto e descrição, mas sem enriquecimento forte persistido no GLPI.

Exemplos observados:

- tickets seedados possuem `externalid` como `lab-ticket-erp` e normalmente têm vínculo com solicitante, atendente e item.
- tickets originados do fluxo conversacional aparecem com assunto como `WhatsApp: ...`, mas sem `externalid`, sem item vinculado e sem categoria persistida.

Isso revela o principal gap atual.

## Gap de qualidade analítica hoje

O backend já calcula vários sinais úteis, mas quase não os grava dentro do GLPI:

- categoria inferida na triagem;
- serviço informado (`service_name`);
- ativo informado (`asset_name`);
- fila sugerida;
- origem do canal como WhatsApp;
- contexto de correlação com Zabbix.

Hoje o cliente GLPI do backend cria ticket com o mínimo:

- `name`
- `content`
- `priority`
- `_users_id_requester`

Isso resolve a operação do MVP, mas deixa pouco valor histórico para BI e ML.

## O que enriquecer no momento da abertura

O melhor ganho agora não é treinar modelo primeiro. É melhorar a qualidade estrutural do dado gravado.

Campos e relações que deveriam ser preenchidos ou derivados já na criação:

- `externalid`: id de correlação interno do backend ou do canal de origem;
- `itilcategories_id`: categoria mapeada a partir da triagem;
- `requesttypes_id`: mapear WhatsApp para um tipo controlado de origem;
- `locations_id`: quando o solicitante, ativo ou time permitir inferência confiável;
- `glpi_items_tickets`: vínculo explícito com ativo quando `asset_name` bater com inventário;
- `glpi_tickets_users`: incluir responsável inicial quando houver regra operacional;
- `content` estruturado: manter texto humano, mas adicionar bloco resumido e consistente para análise posterior.

Também vale persistir no store operacional do backend um espelho estruturado do contexto usado na abertura:

- `triage.resolved_category`
- `triage.resolved_priority`
- `triage.suggested_queue`
- `asset_name`
- `service_name`
- `identity_source`
- ids correlacionados do Zabbix

Esse espelho ajuda mesmo quando o GLPI não tiver campo nativo ideal para tudo.

## Dimensões que faltam popular no laboratório

O laboratório está funcional para integração, mas ainda ruim para analytics. Para melhorar isso, o seed deveria passar a popular:

- categorias ITIL (`glpi_itilcategories`)
- grupos (`glpi_groups`)
- localizações (`glpi_locations`)
- soluções (`glpi_itilsolutions`)
- tarefas (`glpi_tickettasks`)
- validações (`glpi_ticketvalidations`)
- satisfação (`glpi_ticketsatisfactions`)

Sem essas dimensões, o histórico fica quase só textual e temporal.

## Proposta de enriquecimento em três camadas

### Camada 1: enriquecimento transacional no GLPI

Objetivo: gravar melhor o dado na origem.

Itens prioritários:

- persistir categoria real do ticket;
- mapear item/ativo pelo inventário;
- normalizar origem do chamado;
- garantir sempre solicitante e, quando fizer sentido, atendente inicial;
- padronizar `externalid`;
- registrar followup inicial estruturado com resumo de triagem quando apropriado.

### Camada 2: camada analítica em PostgreSQL do backend

Objetivo: não sobrecarregar o modelo transacional do GLPI com necessidades de BI e ML.

Tabelas recomendadas:

- `glpi_ticket_fact`
- `glpi_ticket_user_fact`
- `glpi_ticket_item_fact`
- `glpi_ticket_followup_fact`
- `glpi_ticket_snapshot_daily`
- `glpi_ticket_feature_store`

Exemplo de colunas úteis em `glpi_ticket_fact`:

- `ticket_id`
- `externalid`
- `opened_at`
- `first_assignment_at`
- `first_response_at`
- `solved_at`
- `closed_at`
- `status_name`
- `urgency`
- `impact`
- `priority`
- `category_id`
- `category_name`
- `request_type_id`
- `request_type_name`
- `entity_id`
- `location_id`
- `requester_user_id`
- `assignee_user_id`
- `main_item_type`
- `main_item_id`
- `main_item_name`
- `source_channel`
- `triage_category`
- `triage_queue`
- `zabbix_event_count`
- `followup_count`
- `is_reopened`
- `resolution_minutes`
- `close_minutes`

Essa camada deve ser tratada como a base oficial para relatórios e experimentos de ML.

### Camada 3: feature store para IA e ML

Objetivo: gerar atributos ricos sem depender só de texto bruto.

Features recomendadas:

- embeddings do assunto e da descrição inicial;
- embeddings do conjunto de followups públicos;
- categoria manual final;
- categoria sugerida pela triagem;
- ativo principal;
- criticidade do ativo;
- tipo de ativo;
- grupo e localização do solicitante;
- grupo e senioridade do atendente;
- quantidade de interações;
- tempo até primeira resposta;
- tempo total até solução;
- horário de abertura;
- dia da semana;
- recorrência do mesmo usuário;
- recorrência do mesmo ativo;
- recorrência do mesmo assunto similar;
- existência de correlação com Zabbix;
- presença de solução formal;
- satisfação final;
- indicador de reabertura.

## Casos de uso de machine learning

Com base enriquecida, os casos mais úteis são:

### Classificação automática

- prever categoria ITIL;
- prever fila de atendimento;
- prever prioridade operacional.

### Similaridade e deduplicação

- detectar tickets quase duplicados;
- sugerir incidentes correlatos por ativo, serviço e texto;
- alimentar busca semântica para atendentes e para a IA.

### Predição operacional

- prever risco de rompimento de SLA;
- prever tempo de resolução;
- prever chance de reabertura;
- prever necessidade de escalonamento.

### IA assistiva

- sugerir resumo executivo do ticket;
- sugerir próximos passos e runbooks;
- sugerir solução a partir de tickets resolvidos parecidos;
- sugerir resposta ao usuário com base em histórico semelhante.

## Onde a IA fica mais inteligente

Hoje a IA já pode ler texto, mas ainda enxerga pouco contexto estruturado. Ela melhora muito quando recebe:

- categoria histórica do ticket;
- serviço e ativo afetado;
- papel e grupo do solicitante;
- atendente atual e histórico daquele atendente;
- tickets semelhantes já resolvidos;
- solução usada antes no mesmo ativo ou serviço;
- recorrência e sazonalidade do incidente;
- relação com alertas do Zabbix.

Ou seja: a inteligência não vem só de um modelo melhor. Vem de contexto melhor.

## Recomendações práticas imediatas

### Prioridade 1

- popular `glpi_itilcategories` no laboratório;
- enriquecer o cliente GLPI do backend para persistir mais do que `name/content/priority/requester`;
- padronizar `externalid` para tickets criados pelo backend.

### Prioridade 2

- criar vínculo automático de item em `glpi_items_tickets` quando houver match confiável por `asset_name`;
- persistir `requesttypes_id` coerente com o canal;
- criar uma rotina ETL do GLPI para PostgreSQL analítico do backend.

### Prioridade 3

- passar a registrar soluções, satisfação e tarefas no laboratório;
- construir dataset histórico supervisionado a partir de tickets resolvidos;
- adicionar embeddings e busca semântica sobre tickets resolvidos e followups.

## Leitura objetiva do ponto atual

O banco do GLPI já oferece um bom núcleo de dados para analytics, mas o repositório ainda usa o GLPI mais como destino operacional mínimo do que como fonte rica de conhecimento.

O maior ganho agora é estruturar melhor o dado na origem e criar uma camada analítica própria em PostgreSQL. Só depois disso faz sentido investir mais pesado em modelos, porque a qualidade de feature hoje ainda é o gargalo principal.

## Próximo passo sugerido

Ordem recomendada:

1. enriquecer a criação de tickets no backend;
2. enriquecer o seed do laboratório com categorias, grupos, localizações e soluções;
3. montar visão analítica em PostgreSQL;
4. gerar dataset histórico para classificação, busca semântica e previsão operacional.