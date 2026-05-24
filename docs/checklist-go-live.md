# Checklist Executivo de Go-Live

## Objetivo

Este checklist serve para conduzir a implantacao final com controle operacional. Ele foi pensado para uso em reuniao de liberacao, janela de mudanca ou war room.

Ele pressupoe que os bloqueadores de entrega listados no [Checklist de fechamento do MVP](fechamento-mvp.md) ja foram resolvidos ou aceitos explicitamente.

## Identificacao da mudanca

- empresa:
- ambiente:
- data da implantacao:
- janela aprovada:
- responsavel tecnico:
- responsavel de negocio:
- aprovador:

## Escopo do go-live

Marcar o que entra nesta liberacao:

- backend de orquestracao
- integracao com GLPI
- integracao com Zabbix
- webhook do WhatsApp
- envio de mensagens de resposta
- camada de IA ligada
- piloto controlado
- liberacao geral

## Checklist de pre-go-live

- DNS do backend criado e resolvendo corretamente.
- Certificado TLS emitido e valido.
- Proxy reverso configurado para o backend.
- Servico do backend sobe automaticamente.
- `.env` preenchido com segredos corretos.
- Segredos armazenados fora do Git.
- `GLPI` acessivel pelo backend.
- `Zabbix` acessivel pelo backend.
- Provedor de WhatsApp acessivel pelo backend.
- Telefones dos usuarios piloto revisados no `GLPI`.
- Perfis do GLPI revisados para `user`, `technician`, `supervisor` e `admin`.
- Equipe do piloto avisada.
- Plano de rollback aprovado.
- Backup ou ponto de retorno validado.

## Validacao tecnica antes da abertura

- `GET /health` retorna sucesso.
- Consulta de identidade por telefone retorna usuario correto.
- Abertura de ticket de teste cria ticket no GLPI.
- Consulta de ticket retorna os dados esperados.
- Comentario de tecnico no ticket gera interacao para o solicitante.
- Fechamento pelo usuario funciona apenas para ticket elegivel.
- Correlacao com Zabbix responde sem erro.

## Checklist do canal WhatsApp

- Numero oficial do bot conectado.
- Webhook configurado no provedor correto.
- Endpoint do webhook apontando para producao.
- Segredo ou assinatura habilitada em producao.
- Mensagem de teste recebida pelo backend.
- Mensagem de resposta entregue ao usuario.

## Checklist operacional do piloto

- Grupo piloto definido.
- Supervisor do piloto identificado.
- Tecnicos de plantao instruidos sobre `/comment`, `/status` e `/assign`.
- Time sabe que o telefone do usuario precisa existir no `GLPI`.
- Procedimento de contingencia comunicado.

## Checklist de prontidao operacional do NOC

- POPs criticos revisados, aprovados e versionados.
- Instrucoes Tecnicas criticas revisadas, aprovadas e versionadas.
- Matriz de responsaveis por fila, sistema, severidade e horario definida.
- Procedimento de abertura, triagem, escalonamento, solucao e fechamento validado.
- Criterios de registro de tratativas no historico do ITSM comunicados.
- Processo de comunicacao com equipes impactadas definido.
- Fluxo de incidentes e problemas validado no ITSM adotado pelo ambiente.
- Pauta da reuniao semanal de Gestao de Incidentes publicada.
- Relatorio semanal do NOC com modelo aprovado.
- Relatorio executivo mensal com modelo aprovado.
- Trilhas de capacitacao de Help Desk, incidentes, problemas e monitoracao definidas.
- Evidencias de treinamento dos operadores do piloto registradas.

## Checklist de monitoracao e alertas

- Alertas criticos revisados quanto a assertividade.
- Alertas ruidosos, redundantes ou sem dono mapeados.
- Triggers do Zabbix com severidade, tags, grupo responsavel e acao esperada.
- Runbook ou POP associado aos alertas criticos.
- Dashboards tecnicos do Zabbix ou Grafana revisados.
- Dashboard executivo publicado ou planejado com data e responsavel.
- Monitoracoes redundantes revisadas para evitar falsos positivos.
- Lacunas de monitoracao identificadas a partir de incidentes recorrentes.
- Regras de correlacao entre eventos e tickets validadas.
- Escopo de acompanhamento 8 x 5 definido para incidentes em tempo real.

## Checklist de demandas, mudancas e auditoria

- Fluxo de demandas no Azure DevOps definido.
- Criterio de precificacao por esforco, risco, urgencia, beneficio e recorrencia definido.
- Mudancas com impacto no NOC possuem aprovador, janela e rollback.
- Comunicacao de mudanca para equipes impactadas validada.
- Configuracoes criticas de Zabbix, Grafana, backend e playbooks versionadas.
- Backup de configuracoes criticas validado.
- Auditoria de alteracoes criticas habilitada ou documentada.
- Rotina de restauracao ou reversao testada para configuracoes principais.

## Criticos a acompanhar nas primeiras horas

- falhas 401, 403, 404, 500 e 502 no backend
- falhas de autenticacao com `GLPI`
- falhas de autenticacao com `Zabbix`
- erros de entrega no provedor de WhatsApp
- tickets abertos para usuario errado por cadastro inconsistente
- tickets nao encontrados no momento do comentario ou fechamento

## Go-live

Horario real de ativacao:

- webhook habilitado em:
- backend reiniciado em:
- proxy publicado em:
- primeiro teste validado em:

## Criterios de aceite

- usuario piloto abriu ticket com sucesso
- tecnico comentou ticket e usuario recebeu a mensagem
- consulta de ticket respondeu corretamente
- logs ficaram estaveis na primeira hora
- nenhuma autenticacao critica falhou
- incidente critico possui POP, responsavel, comunicacao e registro no ITSM
- alerta critico possui dono, severidade, runbook e criterio de escalonamento
- relatorio operacional inicial do NOC foi gerado ou possui responsavel e data
- mudancas da janela ficaram registradas com aprovador, evidencias e rollback

## Condicoes de rollback

Executar rollback se ocorrer qualquer um destes pontos:

- indisponibilidade persistente do backend
- erro sistematico na abertura de tickets
- comentarios dos tecnicos nao chegam aos solicitantes
- integracao com `GLPI` ou `Zabbix` falha sem recuperacao rapida
- webhook do provedor gera volume de erro continuo

## Passos de rollback

1. Desabilitar o webhook no provedor de WhatsApp.
2. Retirar o backend do proxy reverso.
3. Preservar `GLPI` e `Zabbix` operando normalmente.
4. Reverter versao ou configuracao do backend.
5. Validar `health` antes de qualquer nova tentativa.

## Encerramento da janela

- go-live concluido com sucesso
- go-live concluido com restricoes
- rollback executado

Observacoes finais:

-
-
-
