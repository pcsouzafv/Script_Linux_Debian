# Automações Sugeridas e Catálogo Inicial de Agentes

## Objetivo

Este documento organiza ideias de automação para helpdesk e infraestrutura em ordem prática de implementação. A prioridade é automatizar o que reduz esforço operacional sem criar risco desnecessário.

## Automações de atendimento ao usuário

- Abrir chamado por texto, áudio ou imagem enviada no WhatsApp.
- Consultar andamento do chamado por número, assunto ou último atendimento.
- Solicitar segunda via de instrução, procedimento ou tutorial.
- Responder perguntas frequentes com base em FAQ validada.
- Coletar evidências padronizadas antes de encaminhar ao técnico.
- Atualizar o usuário automaticamente quando o status do ticket mudar.

## Automações para técnicos

- Receber notificação automática de novo chamado com resumo executivo.
- Consultar histórico do usuário, ativo, host ou serviço afetado.
- Sugerir categoria, prioridade, grupo executor e runbook inicial.
- Coletar diagnósticos básicos como ping, DNS, portas, uso de CPU, memória e disco.
- Reunir logs recentes e anexar ao ticket.
- Sugerir causa provável com base em histórico semelhante.
- Produzir resumo técnico de passagem de turno.

## Automações de infraestrutura

- Criar chamado automático a partir de trigger crítica do Zabbix.
- Correlacionar múltiplos alertas em um único incidente pai.
- Identificar incidentes em massa por serviço, localidade ou cluster.
- Executar coleta de evidências em servidor, container ou namespace.
- Reiniciar serviço previamente homologado com aprovação quando necessário.
- Abrir problema recorrente quando um tipo de incidente ultrapassar limite definido.
- Fechar ticket automaticamente quando o alarme normalizar e houver validação técnica.

## Classificação por risco

### Baixo risco

- Consultas de status.
- Coleta de logs.
- Testes de conectividade.
- Geração de resumos.
- Classificação e roteamento de chamados.

### Médio risco

- Reinício de serviço não crítico.
- Limpeza de cache.
- Reexecução de job conhecido.
- Alteração de configuração homologada.

### Alto risco

- Alteração de firewall.
- Mudança em banco de dados.
- Escala de cluster.
- Rollback ou deploy.
- Ações destrutivas em produção.

## Catálogo inicial de agentes

### Agente de triagem

Responsabilidades:
Classificar o chamado, identificar urgência, coletar contexto mínimo e sugerir a fila correta.

### Agente de correlação

Responsabilidades:
Cruzar dados entre GLPI, Zabbix, CMDB e histórico para identificar impacto, recorrência e possíveis vínculos.

### Agente de diagnóstico

Responsabilidades:
Interpretar logs, eventos e sintomas; sugerir testes e priorizar hipóteses técnicas.

### Agente de execução assistida

Responsabilidades:
Traduzir uma solicitação aprovada em runbook específico, validar pré-condições e acompanhar o retorno da automação.

### Agente de comunicação

Responsabilidades:
Gerar mensagens claras para usuário, técnico, supervisor e passagem de turno sem perder contexto operacional.

### Agente de conhecimento

Responsabilidades:
Buscar procedimentos, artigos, históricos parecidos e runbooks relevantes a partir do contexto do ticket.

## Ideias de fluxos de alto valor

- Usuário relata lentidão no sistema; o agente identifica serviço, consulta status no Zabbix, verifica incidentes abertos e cria ticket já enriquecido.
- Zabbix detecta indisponibilidade de host; o sistema cria incidente no GLPI, notifica o técnico e sugere checklist inicial.
- Técnico pede “coletar logs do Apache do servidor X”; o agente chama um playbook homologado e anexa o resultado ao ticket.
- Supervisor pede resumo dos incidentes críticos das últimas quatro horas; o agente compila eventos, responsáveis, impacto e pendências.

## Métricas para acompanhar valor

- Tempo médio para abertura correta do chamado.
- Tempo médio de triagem.
- Taxa de roteamento correto no primeiro atendimento.
- Taxa de resolução com automação assistida.
- Redução de tempo de resposta para incidentes críticos.
- Quantidade de chamados evitados por FAQ e autosserviço.

## Regras que devem existir desde o início

- Toda tool de automação precisa ter dono técnico e documentação.
- Toda automação precisa informar escopo, impacto e rollback.
- Toda execução precisa ficar associada a ticket, usuário e contexto.
- Toda resposta do agente precisa deixar claro quando é sugestão e quando houve ação executada.
