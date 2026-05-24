# Evolucao NOC, ITSM e Operacao Padronizada

## Objetivo

Evoluir a plataforma para apoiar a operacao do NOC com processos padronizados, gestao de incidentes e problemas, monitoracao assertiva, indicadores executivos, controle de mudancas, governanca de demandas e melhoria continua.

Esta frente complementa a automacao tecnica ja prevista no projeto. O foco deixa de ser apenas integrar GLPI, Zabbix, WhatsApp e backend, e passa a organizar a rotina operacional do NOC como um sistema auditavel, mensuravel e treinavel.

## Escopo funcional

### 1. POPs, instrucoes tecnicas e capacitacao

Entregas esperadas:

- inventario de Procedimentos Operacionais Padrao por atividade, fila, sistema, severidade e horario;
- inventario de Instrucoes Tecnicas por ferramenta, servico, painel e rotina critica;
- revisao, otimizacao e versionamento dos procedimentos existentes;
- matriz de aderencia entre POP, IT, fila operacional e categoria ITSM;
- trilha de treinamento para Help Desk, Gestao de Incidentes, Gestao de Problemas e NOC;
- registro de capacitacao por colaborador, turma, data, conteudo e evidencias;
- avaliacao periodica de aderencia operacional por amostragem de tickets e incidentes.

Requisitos de controle:

- todo procedimento precisa ter dono, aprovador, data de revisao e versao;
- toda instrucao critica precisa ter criterio de acionamento, pre-condicoes, rollback e registro esperado no ITSM;
- mudancas em POP ou IT devem gerar historico de aprovacao e comunicacao para equipes impactadas.

### 2. Help Desk, incidentes, problemas e ITSM

Entregas esperadas:

- fluxo padronizado para abertura, triagem, atendimento, escalonamento, solucao e fechamento;
- acompanhamento e fechamento de incidentes e problemas em ITSM, GLPI, Ivanti ou Pratica, conforme ambiente do cliente;
- vinculacao entre incidente, problema, causa raiz, impacto, workaround e solucao definitiva;
- relatorio de incidente com linha do tempo, impacto, causa raiz, acoes tomadas e prevencao de recorrencia;
- participacao, elaboracao e apresentacao nas reunioes semanais de Gestao de Incidentes;
- processo de comunicacao com equipes impactadas e registro de tratativas no historico do NOC.

Indicadores minimos:

- backlog por fila, status, prioridade e responsavel;
- aging por severidade;
- SLA violado e em risco;
- taxa de reabertura;
- recorrencia por servico, ativo, categoria e causa raiz;
- tempo ate reconhecimento, tempo ate contencao e tempo ate resolucao;
- tickets sem categoria, sem atribuicao, sem historico ou sem causa raiz quando obrigatoria.

### 3. Monitoracao, alertas e correlacao

Entregas esperadas:

- revisao de todos os alertas mal configurados ou com baixa assertividade para o NOC;
- revisao periodica das monitoracoes existentes para reduzir redundancias e falsos positivos;
- revisao e padronizacao das configuracoes do Zabbix implementadas nas monitoracoes;
- revisao da criacao de dashboards e paineis inteligentes em Grafana e Zabbix com visao executiva e tecnica;
- revisao da implementacao de novas metricas e gatilhos com base em incidentes, problemas e tendencias;
- acompanhamento de incidentes em tempo real, com correlacao entre eventos e sistemas em regime 8 x 5;
- integracao de ferramentas de monitoracao com ITSM para abertura, atualizacao e correlacao de tickets.

Classificacao dos alertas:

| Classe | Descricao | Acao esperada |
| --- | --- | --- |
| assertivo | Alerta indica falha real ou risco claro | manter, documentar runbook e SLA |
| ruidoso | Alerta aciona sem impacto operacional frequente | ajustar trigger, janela, dependencia ou severidade |
| redundante | Alerta duplica outro melhor ou de camada superior | consolidar ou desativar com aprovacao |
| incompleto | Alerta nao tem contexto, dono ou acao clara | enriquecer mensagem, tags e procedimento |
| ausente | Incidente recorrente sem monitoracao equivalente | criar metrica, item, trigger ou correlacao |

Campos obrigatorios para alertas produtivos:

- servico afetado;
- ativo, host ou componente;
- severidade;
- janela de validacao;
- criterio de recuperacao;
- grupo responsavel;
- runbook ou POP associado;
- regra de abertura ou correlacao no ITSM;
- acao esperada do NOC;
- criterio para escalonamento.

### 4. Gestao de demandas, precificacao e Azure DevOps

Entregas esperadas:

- fluxo de entrada, classificacao, priorizacao e aceite de demandas no Azure DevOps;
- avaliacao de precificacao por esforco, complexidade, risco, dependencia, urgencia e recorrencia;
- separacao entre demanda operacional, melhoria, projeto estrategico, problema recorrente e mudanca;
- rastreabilidade entre demanda, incidente motivador, problema, mudanca, release e evidencia de conclusao;
- relatorio consolidado de demandas por status, area, prioridade, custo estimado e beneficio esperado.

Modelo inicial de precificacao:

| Dimensao | Escala sugerida | Evidencia |
| --- | --- | --- |
| esforco | P, M, G, GG | horas, perfis envolvidos e complexidade tecnica |
| risco | baixo, medio, alto | impacto potencial, rollback e dependencia |
| urgencia | baixa, normal, alta, critica | SLA, regulatorio, cliente ou operacao |
| beneficio | operacional, financeiro, risco, experiencia | indicador esperado antes e depois |
| recorrencia | pontual, recorrente, estrutural | volume historico e tendencia |

### 5. Gestao de mudancas no NOC

Entregas esperadas:

- alinhamento de mudancas que afetem monitoracao, ITSM, comunicacao ou rotina operacional;
- criterio para mudanca padrao, normal e emergencial;
- checklist de impacto no NOC antes da execucao;
- comunicacao previa para equipes impactadas;
- registro de janela, aprovador, responsavel tecnico, rollback e validacao pos-mudanca;
- atualizacao de POP, IT, dashboard, alerta e base de conhecimento quando aplicavel.

### 6. Pessoas, processos e indicadores

Entregas esperadas:

- geracao de informacao para gestao das atividades operacionais e taticas dos colaboradores do NOC;
- matriz de responsabilidades por processo, fila, sistema e severidade;
- indicadores de produtividade, qualidade, aderencia a processo e necessidade de capacitacao;
- planejamento estrategico da estruturacao da equipe de monitoracao;
- visao de capacidade por turno, perfil, demanda, criticidade e backlog.

Indicadores de gestao:

- volumetria por colaborador, fila e categoria;
- tempo medio de primeira acao;
- tempo medio de resolucao;
- tickets reabertos por colaborador, categoria e causa;
- tickets sem registro adequado de tratativa;
- treinamentos concluidos e pendentes;
- aderencia ao procedimento por amostragem;
- carga operacional por turno e por fila.

### 7. Relatorios e apresentacoes consolidadas do NOC

Entregas esperadas:

- relatorio semanal de incidentes, problemas, alertas, demandas e mudancas;
- relatorio mensal executivo com tendencias, riscos, ganhos e plano de melhoria;
- apresentacao consolidada do NOC para gestao;
- painel tecnico para acompanhamento diario;
- painel executivo para disponibilidade, SLA, recorrencia, risco e capacidade.

Estrutura recomendada para relatorio consolidado:

1. resumo executivo;
2. incidentes relevantes;
3. problemas e causas raiz;
4. alertas revisados, ajustados e pendentes;
5. demandas e mudancas;
6. indicadores operacionais;
7. riscos e bloqueios;
8. acoes concluidas;
9. proximas acoes;
10. decisoes solicitadas.

### 8. Controle de versoes, backup e auditoria

Entregas esperadas:

- controle de versao para configuracoes criticas de Zabbix, Grafana, backend, playbooks, POPs e ITs;
- backup periodico das configuracoes e dashboards;
- trilha de auditoria para alteracoes em monitoracao, integracoes, alertas, paineis e procedimentos;
- rotina de restauracao testada;
- evidencia de quem alterou, quando alterou, por que alterou e qual mudanca aprovou.

## Backlog priorizado

### Onda 1: governanca operacional minima

- Criar catalogo de POPs e ITs.
- Criar matriz de processos do NOC.
- Definir modelo de relatorio semanal e mensal.
- Definir campos obrigatorios de registro de tratativa no ITSM.
- Criar checklist de mudanca com impacto no NOC.

### Onda 2: assertividade da monitoracao

- Inventariar alertas produtivos.
- Classificar alertas por assertivo, ruidoso, redundante, incompleto e ausente.
- Padronizar templates, severidades, tags e grupos no Zabbix.
- Revisar dashboards tecnicos e executivos.
- Criar rotina mensal de revisao de falsos positivos e lacunas de monitoracao.

### Onda 3: indicadores, relatorios e reunioes

- Consolidar indicadores de incidentes, problemas, demandas e mudancas.
- Criar relatorio operacional semanal do NOC.
- Criar apresentacao executiva mensal.
- Criar pauta fixa para reuniao semanal de Gestao de Incidentes.
- Criar painel de acompanhamento 8 x 5 para incidentes em tempo real.

### Onda 4: demandas, precificacao e Azure DevOps

- Criar taxonomia de work items.
- Definir criterio de precificacao e priorizacao.
- Integrar demandas com incidentes, problemas, mudancas e projetos.
- Criar relatorio de demandas por custo, risco, urgencia e beneficio.
- Criar fluxo de aceite e encerramento com evidencias.

### Onda 5: integracao estrategica e melhoria continua

- Integrar ferramentas de monitoracao com ITSM.
- Automatizar correlacao entre eventos, incidentes, problemas e mudancas.
- Implementar pos-incidente semi-automatico.
- Gerar recomendacoes de novas metricas e gatilhos com base em tendencias.
- Usar historico para priorizar reducao de recorrencia e falsos positivos.

## Criterios de aceite da evolucao

- POPs e ITs criticos inventariados, versionados e com donos definidos.
- Fluxo de incidente e problema padronizado e evidenciado no ITSM.
- Alertas criticos revisados, classificados e associados a runbook ou POP.
- Dashboards tecnicos e executivos publicados e validados pelo NOC.
- Relatorio semanal e mensal do NOC gerados com dados rastreaveis.
- Reuniao semanal de Gestao de Incidentes com pauta, evidencias e acoes.
- Demandas controladas no Azure DevOps com criterio de precificacao.
- Mudancas com impacto no NOC registradas, aprovadas e comunicadas.
- Configuracoes criticas com backup, versao e auditoria.
- Indicadores de pessoas, processos e operacao disponiveis para gestao.

## Modelos operacionais incluidos

- [Modelo de POP ou Instrucao Tecnica](templates/pop-instrucao-tecnica.md)
- [Modelo de Relatorio de Incidente](templates/relatorio-incidente.md)
- [Modelo de Revisao de Alertas do NOC](templates/revisao-alertas-noc.md)
- [Modelo de Relatorio Consolidado do NOC](templates/relatorio-consolidado-noc.md)
- [Modelo de Precificacao de Demanda no Azure DevOps](templates/precificacao-demanda-azure-devops.md)

## Relacao com a plataforma tecnica

Esta evolucao deve ser implementada sem substituir as ferramentas oficiais do cliente. O backend de orquestracao deve atuar como camada de integracao, auditoria, correlacao e apoio inteligente.

Fontes oficiais recomendadas:

- ITSM, GLPI, Ivanti ou Pratica para chamados, incidentes, problemas, mudancas e historico;
- Zabbix para eventos, triggers, hosts e metricas;
- Grafana para paineis tecnicos e executivos;
- Azure DevOps para demandas, projetos, backlog e evidencias de entrega;
- repositorio Git para versionamento de configuracoes, POPs, ITs, dashboards e automacoes;
- banco operacional do backend para auditoria, correlacao, snapshots analiticos e recomendacoes.
