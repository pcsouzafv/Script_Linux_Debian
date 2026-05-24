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
- a plataforma já entrega assistência de resolução por ticket usando histórico do GLPI, snapshot analítico e IA para sugerir próximos passos seguros;
- comandos operacionais de consulta e atualização de ticket já conseguem devolver recomendações resumidas por recorrência e contexto recente;
- mudanças para `solved` já podem consolidar uma `solution` estruturada no GLPI, retroalimentando o histórico que a IA consulta depois;
- ainda faltam relatórios operacionais completos, detecção de incidentes em massa, pós-mortem semi-automático e recomendações por recorrência.

O detalhamento técnico desta frente fica em [Fase 5: Operacao Avancada](fase-5-operacao-avancada.md).

## Fase 6: Governanca operacional do NOC

Status atual: planejada.

Objetivo:
Transformar a plataforma em apoio direto para gestao do NOC, conectando processos, pessoas, monitoracao, ITSM, demandas, mudancas, relatorios e melhoria continua.

Entregas:

- revisao, otimizacao e controle de POPs e Instrucoes Tecnicas;
- capacitacao e trilhas de treinamento para Help Desk, incidentes, problemas e NOC;
- relatorios e apresentacoes consolidadas do NOC;
- gestao de demandas e precificacao no Azure DevOps;
- gestao de mudancas com impacto no NOC;
- revisao de alertas com baixa assertividade, redundancia e falsos positivos;
- padronizacao de Zabbix, dashboards Grafana e paineis executivos;
- integracao entre monitoracao e ITSM;
- acompanhamento 8 x 5 de incidentes com correlacao entre eventos e sistemas;
- controle de versoes, backup e auditoria de configuracoes;
- indicadores para gestao operacional e tatica dos colaboradores.

Critério de saída:
NOC com processos versionados, indicadores rastreaveis, alertas revisados, dashboards validados, demandas controladas, mudancas governadas e relatorios executivos recorrentes.

O detalhamento desta frente fica em [Evolucao NOC, ITSM e operacao padronizada](evolucao-noc-operacao.md).

## Backlog priorizado

1. Fechar lacunas da Fase 5: relatorios completos, incidente em massa, pos-mortem e conhecimento.
2. Criar catalogo versionado de POPs e Instrucoes Tecnicas.
3. Criar rotina de revisao de alertas, Zabbix, dashboards e falsos positivos.
4. Criar relatorios semanais e mensais consolidados do NOC.
5. Implantar gestao de demandas, priorizacao e precificacao no Azure DevOps.
6. Padronizar gestao de mudancas com impacto no NOC.
7. Integrar monitoracao com ITSM para correlacao e registro automatico.
8. Criar indicadores de pessoas, processos, produtividade, qualidade e capacitacao.

## Critérios de qualidade da próxima etapa

- APIs com autenticação, idempotência e logs estruturados.
- Segredos fora do código e das mensagens trocadas com o modelo.
- Testes de integração para conectores externos.
- Ambientes separados para laboratório e produção.
- Política clara de aprovação antes de qualquer automação de impacto.
