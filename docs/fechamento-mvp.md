# Checklist de Fechamento do MVP

## Objetivo

Este documento consolida o que ainda precisa estar resolvido para considerar o MVP pronto para piloto controlado ou producao assistida.

Ele nao substitui o [checklist executivo de go-live](checklist-go-live.md). A ideia aqui e separar o que ainda e lacuna de entrega do que ja e rotina de liberacao.

## Definicao pratica de pronto

Para este repositorio, o MVP deve ser considerado fechado quando estas condicoes forem verdadeiras ao mesmo tempo:

- o backend estiver publicado de forma reprodutivel, com processo permanente e rollback claro;
- GLPI, Zabbix e provedor de WhatsApp estiverem integrados com credenciais reais e validacao ponta a ponta;
- persistencia operacional e worker estiverem rodando fora do modo apenas laboratorio;
- equipe de operacao tiver checklist, responsaveis e validacao minima para abrir piloto sem improviso.

## Bloqueadores de fechamento do MVP

### 1. Publicacao do backend

- [ ] Versionar um artefato real de execucao do backend em `infra/backend/`, como `systemd` unit, container spec ou equivalente.
- [ ] Versionar exemplo real de proxy reverso para publicar o backend com `TLS`.
- [ ] Versionar checklist de deploy e comandos de rollback do backend fora da pasta `docs/`.
- [ ] Definir layout de configuracao por ambiente para o backend, sem depender apenas de ajuste manual do `.env` local.

### 2. Ambiente de homologacao final ou producao

- [ ] Definir `FQDN`, `DNS`, `TLS` e regras de firewall do backend.
- [ ] Garantir segredos fora do Git e rotacao minima das credenciais principais.
- [ ] Validar backup ou ponto de retorno de `GLPI`, `Zabbix` e configuracao do backend.
- [ ] Nomear responsavel tecnico, aprovador e equipe de acompanhamento do piloto.

### 3. Integracoes reais obrigatorias

- [ ] Habilitar autenticacao real do `GLPI` para identidade e tickets.
- [ ] Habilitar autenticacao real do `Zabbix` para correlacao operacional.
- [ ] Configurar provedor real do WhatsApp, com webhook publicado e segredo ou assinatura ativos.
- [ ] Revisar perfis do `GLPI` para `user`, `technician`, `supervisor` e `admin`.
- [ ] Revisar telefones dos usuarios piloto no `GLPI`, para evitar vinculo incorreto de identidade.

### 4. Persistencia operacional e worker

- [ ] Subir `PostgreSQL` operacional do backend fora do fluxo apenas laboratorio.
- [ ] Subir `Redis` operacional da fila administrativa fora do fluxo apenas laboratorio.
- [ ] Publicar o worker de automacao como processo permanente, com restart e observacao basica.
- [ ] Validar criacao, aprovacao, execucao e consulta de jobs homologados no ambiente alvo.

### 5. Validacao funcional integrada

- [ ] `GET /health` respondendo no endpoint publicado.
- [ ] Resolucao de identidade por telefone retornando o usuario correto.
- [ ] Abertura de ticket real no `GLPI` funcionando pelo backend.
- [ ] Consulta de ticket funcionando com os dados esperados.
- [ ] Comentario operacional chegando ao solicitante pelo canal de mensagens.
- [ ] Fechamento elegivel pelo usuario funcionando sem quebrar regra de permissao.
- [ ] Correlacao com `Zabbix` respondendo sem erro para um caso conhecido.

### 6. Operacao de piloto

- [ ] Definir grupo piloto e janela de acompanhamento.
- [ ] Instruir tecnicos sobre `/comment`, `/status`, `/assign` e fluxo de contingencia.
- [ ] Definir criterios objetivos de aceite para primeira hora e primeiro dia.
- [ ] Definir condicoes objetivas de rollback e quem decide executa-lo.

## Nao bloqueia o MVP, mas ainda falta para a visao completa

### IA e conhecimento

- [ ] Indexar FAQ, artigos e runbooks em uma camada real de conhecimento.
- [ ] Implementar `RAG` e agentes de resumo ou classificacao sobre essa base.

### Observabilidade da plataforma

- [ ] Publicar metricas, logs e alertas da propria plataforma em `infra/observability/`.
- [ ] Versionar dashboards e regras de alerta para backend e worker.

### Analytics e enriquecimento do GLPI

- [ ] Persistir melhor `externalid`, `itilcategories_id` e `requesttypes_id` para tickets originados do backend.
- [ ] Popular melhor o laboratorio para analise, incluindo categorias, grupos, localizacoes, solucoes e satisfacao.
- [ ] Evoluir o snapshot analitico para relatórios operacionais mais completos.

### Operacao avancada

- [ ] Entregar relatorios de fila, backlog e eficiencia operacional.
- [ ] Adicionar deteccao de incidentes em massa.
- [ ] Adicionar pos-mortem semi-automatico e recomendacoes por recorrencia.

Detalhamento recomendado de execucao: [Fase 5: Operacao Avancada](fase-5-operacao-avancada.md).

### Entrega continua

- [ ] Versionar pipeline minima de `CI` para testes e validacao de mudancas.
- [ ] Definir estrategia padrao de release do backend e do worker.

## Ordem recomendada de fechamento

1. Fechar publicacao do backend em `infra/backend/`.
2. Fechar ambiente alvo com `DNS`, `TLS`, segredos e rollback.
3. Validar integracoes reais de `GLPI`, `Zabbix` e WhatsApp.
4. Publicar persistencia operacional e worker fora do laboratorio.
5. Executar o [checklist executivo de go-live](checklist-go-live.md).

## Leitura objetiva do estado atual

- codigo do backend: pronto para piloto tecnico, com suite local validada;
- deploy operacional: ainda incompleto no repositorio;
- integracoes reais: dependem de ambiente e credenciais externas;
- observabilidade propria, `RAG` e relatorios avancados: ainda sao evolucao posterior ao fechamento do MVP.
