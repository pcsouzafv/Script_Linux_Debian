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
| Execucao segura | Worker seguro + runner externo homologado | Rodar automacoes homologadas com auditoria, aprovacao por risco e sem shell arbitrario | Worker inicial ja implementado no backend; runner externo homologado ja validado para smoke test local e probe vinculado a ticket |
| Persistencia operacional | PostgreSQL | Sessao, auditoria, estado, cache funcional | Provisionamento de laboratorio em `infra/helpdesk-lab` e integracao inicial no backend para sessao e auditoria minima |
| Fila e tarefas | Redis ou RabbitMQ + worker | Jobs assincronos, retentativas, fila de automacoes | Redis provisionado para laboratorio em `infra/helpdesk-lab`, worker inicial conectado no backend e ciclo de retry com backoff persistido + dead-letter ja implementado |
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

- Manter o worker atual restrito a automacoes homologadas e read-only onde possivel.
- Manter politica de risco no catalogo, com autoaprovacao apenas para low-risk e aprovacao explicita para jobs moderados ou superiores.
- Evoluir do catalogo inicial do backend para Ansible direto, AWX ou Rundeck, partindo dos playbooks homologados atuais de `ping_localhost` e `ticket_context_probe`.
- Amarrar cada automacao a papel, aprovacao, auditoria e rollback.
- Integrar o backend a essa camada sem liberar shell arbitrario.

### Fase 5: IA operacional e conhecimento

Acrescentar a camada de triagem e assistencia.

- Consolidar o banco operacional proprio do backend, incluindo politicas de retencao e consultas de auditoria.
- Evoluir a fila ja existente, que hoje ja tem retry com backoff persistido, dead-letter e runner externo homologado, para observabilidade operacional mais rica.
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
- Backend ja expõe consulta interna minima da auditoria operacional, protegida por credencial administrativa separada e sem texto livre sensivel.
- O endurecimento atual tambem suporta rotacao sem downtime para tokens internos e administrativos, mantendo token atual e anterior por janela curta.
- Criacao de job administrativo, leitura de job e aprovacao de job ja nao precisam compartilhar o mesmo segredo: leitura e aprovacao podem ficar em escopos proprios, reforcando menor privilegio.
- A aprovacao de automacoes deixou de compartilhar o mesmo segredo de criacao e consulta de jobs; agora existe um escopo credencial proprio para `approve/reject`, reforcando separacao de funcao.
- Backend ja consegue registrar e executar jobs administrativos em fila dedicada, com dois jobs homologados em Ansible Runner: um smoke test local autoaprovado e um probe read-only vinculado a ticket que exige aprovacao explicita antes de enfileirar.
- O ciclo de falha do worker nao depende mais de reenqueue imediato: retries ficam em `retry-scheduled` com janela futura persistida no banco operacional ou no fallback em memoria, mantendo previsibilidade e trilha de auditoria.
- Jobs que ficam tempo demais em `pending/awaiting-approval` agora podem expirar automaticamente por politica configuravel, sendo rejeitados com trilha explicita de auditoria antes de qualquer nova leitura ou decisao administrativa.
- Jobs aprovados, mas ainda nao executados, agora tambem podem ser cancelados de forma protegida antes da execucao; quando ainda estao na fila primaria, o backend remove o `job_id` da fila para reduzir a janela de disparo indevido.
- Decisoes administrativas de `approve`, `reject` e `cancel` passaram a usar `reason_code` allowlisted por acao, reduzindo texto livre em trilha operacional e mantendo auditoria estruturada com codigo e rotulo padronizado.
- Identificadores administrativos declarados em `requested_by` e `acted_by` agora tambem seguem formato estruturado sem espacos, reduzindo poluicao de auditoria e risco de texto arbitrario em metadados operacionais.
- O backend passou a expor um resumo operacional protegido da automacao, sem `payload_json`, para enxergar backlog, dead-letter, retries pendentes e envelhecimento de jobs administrativos sob o mesmo escopo de leitura dedicada.
- O store operacional agora limita profundidade, volume e tamanho de strings em payloads administrativos persistidos e tambem remove jobs terminalizados antigos por retenção configurável, reduzindo risco de acúmulo e exposição desnecessária.
- WhatsApp so transporta mensagens.
- AWX, Rundeck ou Ansible seguem como proxima etapa para automacoes mais sensiveis e com rollback mais robusto.
- Banco operacional guarda estado proprio da plataforma, sem acoplamento ao banco do GLPI, e agora ja pode manter sessoes do autoatendimento e eventos minimos de auditoria.

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

Para laboratorio local em desktop, o repositorio agora tambem inclui [infra/helpdesk-lab/README.md](/home/ricardo/Script_Linux_Debian/infra/helpdesk-lab/README.md), com um `Docker Compose` isolado para `GLPI + Zabbix + PostgreSQL + Redis` sem tocar nos containers ja existentes do host.

## Decisao pratica para agora

O caminho mais pragmatico e tratar o projeto em dois blocos imediatos:

- bloco 1: host base + GLPI + Zabbix;
- bloco 2: backend de orquestracao com integracoes e webhook.

Depois disso, a implantacao das automacoes deve entrar como um terceiro bloco separado, com ferramenta propria de execucao e politica de aprovacao.
