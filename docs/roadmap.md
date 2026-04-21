# Roadmap Inicial da Plataforma

## Objetivo

Organizar a evolução do projeto em fases pragmáticas, entregando valor cedo e reduzindo risco antes de liberar automações mais sensíveis.

## Fase 0: Fundação

Objetivo:
Consolidar o provisionamento base e organizar o repositório.

Entregas:

- Script de instalação isolado do README.
- Documentação de visão, arquitetura e automações.
- Definição inicial de stack para backend, agentes e execução.

Critério de saída:
Repositório pronto para iniciar implementação dos serviços de integração.

## Fase 1: Integração GLPI e Zabbix

Objetivo:
Criar o núcleo de integração entre chamados e monitoração.

Entregas:

- Cliente de API do GLPI.
- Cliente de integração com Zabbix.
- Modelo de correlação entre ticket, host, trigger e serviço.
- Auditoria básica das chamadas externas.

Critério de saída:
Sistema capaz de criar e consultar tickets e eventos de forma programática.

## Fase 2: Canal WhatsApp

Objetivo:
Abrir o primeiro canal conversacional para usuários e técnicos.

Entregas:

- Webhook do WhatsApp Business API.
- Validação de identidade por número e perfil.
- Fluxo de abertura e consulta de ticket.
- Templates de notificação para técnico e usuário.

Critério de saída:
Usuário consegue abrir chamado e técnico recebe notificação operacional.

## Fase 3: IA para triagem e conhecimento

Objetivo:
Usar agentes apenas para sugerir, resumir e classificar, sem execução operacional.

Entregas:

- Base de conhecimento inicial.
- RAG para FAQ, runbooks e histórico.
- Agente de triagem.
- Agente de comunicação.

Critério de saída:
O sistema classifica melhor os tickets e entrega contexto útil para o atendimento humano.

## Fase 4: Automação assistida

Status atual: concluida no escopo atual do repositorio.

Objetivo:
Executar apenas tarefas controladas de baixo risco com aprovação quando necessário.

Entregas:

- Catálogo de playbooks homologados.
- Integração com Ansible, AWX ou Rundeck.
- Política de aprovação por tipo de ação.
- Trilha completa de auditoria.

Critério de saída:
Técnicos conseguem disparar automações seguras a partir do contexto do ticket.

Situação observada hoje:

- catálogo homologado inicial implementado;
- worker seguro com fila, aprovação, retry e dead-letter implementado;
- execução homologada por Ansible Runner validada no laboratório.

## Fase 5: Operação avançada

Status atual: parcial.

Objetivo:
Expandir para correlação avançada, métricas operacionais e melhoria contínua.

Entregas:

- Detecção de incidentes em massa.
- Relatórios de fila, backlog e eficiência operacional.
- Pós-mortem semi-automático.
- Recomendações por histórico e recorrência.

Critério de saída:
Plataforma operando como camada de apoio real para service desk e infraestrutura.

Situação observada hoje:

- parte da base já existe via auditoria durável, snapshot analítico e trilha operacional;
- ainda faltam relatórios operacionais completos, detecção de incidentes em massa, pós-mortem semi-automático e recomendações por recorrência.

## Backlog priorizado

1. Estruturar backend principal de integração.
2. Modelar usuários, técnicos, filas e permissões.
3. Implementar fluxo de abertura de chamado por WhatsApp.
4. Implementar notificação técnica.
5. Implementar consulta a alertas do Zabbix.
6. Adicionar correlação com tickets do GLPI.
7. Adicionar RAG com base de conhecimento.
8. Liberar primeira automação homologada de baixo risco.

## Critérios de qualidade da próxima etapa

- APIs com autenticação, idempotência e logs estruturados.
- Segredos fora do código e das mensagens trocadas com o modelo.
- Testes de integração para conectores externos.
- Ambientes separados para laboratório e produção.
- Política clara de aprovação antes de qualquer automação de impacto.
