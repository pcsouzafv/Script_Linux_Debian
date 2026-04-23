# Backend MVP

Backend inicial em FastAPI para orquestrar o fluxo entre WhatsApp, GLPI e Zabbix.

## Objetivo

Nesta fase o backend entrega:

- API para saúde da aplicação.
- Endpoint para triagem segura de chamados com sugestão de categoria, prioridade, fila e próximos passos.
- Endpoint para abertura direta de ticket.
- Endpoint para consulta de ticket por ID.
- Endpoint para consulta de identidade por número de telefone.
- Endpoint oficial para webhook da Meta com assinatura HMAC opcional.
- Endpoint para ingestão normalizada de mensagem do WhatsApp, com separação entre abertura de chamado e comando operacional para técnico.
- Fluxo de WhatsApp sensível ao papel do remetente: usuário final abre chamado por texto livre; técnico, supervisor e admin entram em fluxo operacional assistido e usam `/open` quando quiserem abrir chamado explicitamente.
- Endpoint para correlação simples com eventos do Zabbix.
- Endpoint para inspecionar o status da camada de IA do bot e o provider ativo.
- Modo local sem integrações configuradas, retornando respostas mock úteis para desenvolvimento.

O roteamento inicial de fila agora não depende apenas de categoria e prioridade. Quando houver contexto operacional claro, a triagem também pode considerar papel e time do solicitante para evitar retriagem desnecessária. Exemplo: incidente de acesso relacionado a VPN, bastion, API, backend ou outro alvo de infraestrutura, aberto por técnico de `infraestrutura` ou `plataforma`, pode cair direto em `Infraestrutura-N1` em vez de `ServiceDesk-Acessos`.

O backend é uma aplicação separada dentro da plataforma. Ele não instala GLPI, Zabbix, WhatsApp, AWX ou Rundeck; ele orquestra essas integrações quando as credenciais e os serviços externos estiverem disponíveis.

## Estrutura

```text
backend/
├── app/
│   ├── api/routes/
│   ├── core/
│   ├── orchestration/
│   ├── schemas/
│   └── services/
├── data/
├── tests/
├── .env.example
├── pyproject.toml
└── README.md
```

## Requisitos

- Python 3.11 ou superior.
- Ambiente virtual recomendado.

## Execução local

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./run_dev.sh
```

O arquivo [backend/.env](.env) já foi criado com todas as chaves de credenciais esperadas. Enquanto os valores continuarem como placeholder, o backend trata essas integrações como não configuradas e permanece em modo mock. Quando quiser ativar chamadas externas, voce pode usar tanto tokens quanto usuario e senha, dependendo da integracao.

As rotas internas sob `/api/v1/helpdesk/*` e o endpoint bruto `/api/v1/webhooks/whatsapp/messages` exigem um token de acesso enviado em `X-Helpdesk-API-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_API_ACCESS_TOKEN` antes de consumir ou publicar essas rotas.

Seguranca do roteamento:

- a rota de abertura de ticket nao confia em `role`, `team` nem `glpi_user_id` informados pelo cliente para decidir fila real;
- o backend resolve a identidade novamente no servidor e so entao aplica o ajuste de fila por contexto operacional;
- a rota `POST /api/v1/helpdesk/triage` aceita `requester_role` e `requester_team` apenas como contexto de simulacao ou apoio operacional, sem efeito direto no GLPI;
- o enriquecimento por LLM nao pode contradizer a fila final prevista quando a triagem ja decidiu o encaminhamento.

Leitura pratica de fila inicial atual:

- `critical` continua indo para `NOC-Critico`;
- categorias de acesso sem contexto operacional seguem para `ServiceDesk-Acessos`;
- categorias de infra, rede e servidor seguem para `Infraestrutura-N1`;
- casos de acesso com solicitante operacional validado no servidor e alvo claro de infraestrutura tambem podem seguir direto para `Infraestrutura-N1`.

A rota administrativa `GET /api/v1/helpdesk/audit/events` usa credencial separada, enviada em `X-Helpdesk-Audit-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_AUDIT_ACCESS_TOKEN`; sem isso, a consulta de auditoria responde como indisponível por seguranca.

A rota administrativa `POST /api/v1/helpdesk/automation/jobs` usa um terceiro escopo dedicado, enviado em `X-Helpdesk-Automation-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_AUTOMATION_ACCESS_TOKEN`; sem isso, o backend bloqueia a criacao de jobs administrativos.

As rotas administrativas de leitura `GET /api/v1/helpdesk/automation/jobs`, `GET /api/v1/helpdesk/automation/jobs/{job_id}` e `GET /api/v1/helpdesk/automation/summary` aceitam um escopo proprio em `X-Helpdesk-Automation-Read-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_AUTOMATION_READ_ACCESS_TOKEN` para separar leitura de escrita; se ele nao estiver definido, o backend faz fallback controlado para `HELPDESK_AUTOMATION_ACCESS_TOKEN`.

As rotas de aprovacao `POST /api/v1/helpdesk/automation/jobs/{job_id}/approve`, `POST /api/v1/helpdesk/automation/jobs/{job_id}/reject` e `POST /api/v1/helpdesk/automation/jobs/{job_id}/cancel` usam um quarto escopo separado, enviado em `X-Helpdesk-Automation-Approval-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN`; sem isso, o backend bloqueia decisoes de aprovacao, rejeicao e cancelamento.

Os campos administrativos `requested_by` e `acted_by` aceitam apenas identificadores operacionais estruturados, sem espacos, como `ops-ana`, `supervisor-ana` ou `ops.ana@example.com`. Isso evita texto livre nesses metadados de auditoria.

Para rotacao sem downtime, voce pode manter simultaneamente o token atual e o imediatamente anterior usando `HELPDESK_API_ACCESS_TOKEN_PREVIOUS`, `HELPDESK_AUDIT_ACCESS_TOKEN_PREVIOUS`, `HELPDESK_AUTOMATION_ACCESS_TOKEN_PREVIOUS`, `HELPDESK_AUTOMATION_READ_ACCESS_TOKEN_PREVIOUS` e `HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN_PREVIOUS`.

Modos aceitos no backend:

- GLPI: `user_token` ou `username/password`
- Zabbix 7.4: `api token` ou `username/password`

No laboratorio local, o repositório ja vem preparado para usar:

- `HELPDESK_GLPI_BASE_URL=http://127.0.0.1:8088/apirest.php`
- `HELPDESK_GLPI_USERNAME=glpi`
- `HELPDESK_GLPI_PASSWORD=glpi`
- `HELPDESK_ZABBIX_BASE_URL=http://127.0.0.1:8089/api_jsonrpc.php`
- `HELPDESK_ZABBIX_USERNAME=Admin`
- `HELPDESK_ZABBIX_PASSWORD=zabbix`
- `HELPDESK_IDENTITY_STORE_PATH=data/identities.lab.json`

Para que a fila logica sugerida pela triagem vire um grupo real do GLPI, configure `HELPDESK_GLPI_QUEUE_GROUP_MAP`. O backend aceita JSON ou pares `fila=grupo` e usa o `Group_Ticket` com `type=2` para gravar o grupo responsavel no chamado. Exemplo:

```env
HELPDESK_GLPI_QUEUE_GROUP_MAP={"ServiceDesk-N1":"TI > Service Desk > N1","ServiceDesk-Acessos":"TI > Service Desk > Acessos","Infraestrutura-N1":"TI > Infraestrutura > N1","NOC-Critico":"TI > NOC > Critico"}
```

Se o nome da fila ja for exatamente igual ao nome do grupo no GLPI, o backend tenta usar esse mesmo nome como fallback mesmo sem mapa explicito. Para hierarquias com `completename`, o recomendado continua sendo preencher o mapa acima.

Persistencia operacional ja suportada no backend:

- `HELPDESK_OPERATIONAL_POSTGRES_DSN`
- `HELPDESK_OPERATIONAL_POSTGRES_SCHEMA`
- `HELPDESK_OPERATIONAL_AUDIT_RETENTION_DAYS`
- `HELPDESK_OPERATIONAL_JOB_RETENTION_DAYS`
- `HELPDESK_AUTOMATION_APPROVAL_TIMEOUT_MINUTES`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_DEPTH`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_LIST_ITEMS`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_OBJECT_KEYS`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_STRING_LENGTH`
- `HELPDESK_REDIS_URL`
- `HELPDESK_AUTOMATION_WORKER_MAX_ATTEMPTS`
- `HELPDESK_AUTOMATION_RETRY_BASE_SECONDS`
- `HELPDESK_AUTOMATION_RETRY_MAX_SECONDS`

Segregacao de acesso administrativo:

- `HELPDESK_AUDIT_ACCESS_TOKEN`
- `HELPDESK_AUTOMATION_ACCESS_TOKEN`
- `HELPDESK_AUTOMATION_READ_ACCESS_TOKEN`
- `HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN`
- `HELPDESK_API_ACCESS_TOKEN_PREVIOUS`
- `HELPDESK_AUDIT_ACCESS_TOKEN_PREVIOUS`
- `HELPDESK_AUTOMATION_ACCESS_TOKEN_PREVIOUS`
- `HELPDESK_AUTOMATION_READ_ACCESS_TOKEN_PREVIOUS`
- `HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN_PREVIOUS`

Quando `HELPDESK_OPERATIONAL_POSTGRES_DSN` estiver configurado, o backend passa a persistir sessoes do autoatendimento, eventos minimos de auditoria e `job_request` de automacao em PostgreSQL. Sem esse DSN, o comportamento continua funcional com fallback em memoria local do processo.

`HELPDESK_OPERATIONAL_POSTGRES_SCHEMA` controla o schema usado pelo backend e aceita apenas letras, numeros e underscore. O valor padrao e `helpdesk_platform`.

`HELPDESK_OPERATIONAL_AUDIT_RETENTION_DAYS` define por quantos dias os eventos de auditoria ficam retidos no banco operacional. O padrao e `30`. Use `0` para desabilitar a limpeza automatica.

`HELPDESK_OPERATIONAL_JOB_RETENTION_DAYS` define por quantos dias jobs administrativos em estado terminal (`completed`, `dead-letter` e `rejected`) ficam retidos no store operacional. O padrao e `30`. Use `0` para desabilitar a limpeza automatica.

`HELPDESK_AUTOMATION_APPROVAL_TIMEOUT_MINUTES` define por quantos minutos um job manual pode permanecer em `pending/awaiting-approval` antes de ser rejeitado automaticamente. O padrao e `1440` minutos. Use `0` para desabilitar essa expiração.

Os payloads operacionais persistidos em `audit_event.payload_json` e `job_request.payload_json` passam por truncamento e sanitizacao centralizados antes de serem gravados. Os limites atuais sao configurados por:

- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_DEPTH`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_LIST_ITEMS`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_OBJECT_KEYS`
- `HELPDESK_OPERATIONAL_PAYLOAD_MAX_STRING_LENGTH`

Isso reduz risco de abuso por JSON excessivamente profundo, listas grandes e strings muito longas em trilhas administrativas e resultados de automacao.

O backend grava apenas metadados operacionais nesses eventos de auditoria. Conteudo livre de mensagem, comentarios completos e transcricoes detalhadas continuam fora da trilha de auditoria para reduzir exposicao de dados sensiveis.

Tambem existe a rota interna `GET /api/v1/helpdesk/audit/events`, protegida por token administrativo proprio. Ela permite apenas filtros fechados por `event_type`, `ticket_id` e `actor_external_id`, com limite maximo de `100` registros por consulta.

O backend bloqueia configuracoes inseguras nesta area:

- token atual e token anterior nao podem ser iguais no mesmo escopo;
- tokens de auditoria nao podem ser reutilizados no escopo interno geral da API;
- tokens de automacao nao podem ser reutilizados nem no escopo interno geral da API nem no escopo administrativo de auditoria;
- tokens de leitura de automacao nao podem ser reutilizados nos escopos de API, auditoria, criacao de automacao ou aprovacao;
- tokens de aprovacao de automacao nao podem ser reutilizados em nenhum dos outros escopos administrativos ou internos;
- token anterior nao pode existir sem token atual no mesmo escopo.

`HELPDESK_REDIS_URL` agora alimenta a fila dos jobs administrativos de automacao. Sem Redis, o backend e o worker seguem funcionais apenas com fallback em memoria no processo atual, adequado para desenvolvimento e testes, nao para producao.

`HELPDESK_AUTOMATION_WORKER_MAX_ATTEMPTS` define quantas execucoes totais um job pode receber antes de ir para dead-letter. O padrao e `3`, com limite maximo de `10` para evitar retentativas excessivas.

`HELPDESK_AUTOMATION_RETRY_BASE_SECONDS` define o atraso base da primeira retentativa agendada. `HELPDESK_AUTOMATION_RETRY_MAX_SECONDS` limita o teto do backoff exponencial persistido no `job_request`. Os padroes atuais sao `5` e `300` segundos, respectivamente.

`HELPDESK_AUTOMATION_RUNNER_BASE_DIR` define onde ficam os projetos homologados do Ansible Runner. O padrao atual aponta para `../infra/automation-runner/projects`.

`HELPDESK_AUTOMATION_RUNNER_TIMEOUT_SECONDS` define o timeout maximo de cada playbook homologado disparado pelo runner. O padrao e `120` segundos.

## Jobs de automacao

O backend agora disponibiliza um worker assíncrono minimo para jobs administrativos seguros.

Guardrails atuais:

- somente automacoes homologadas entram na fila;
- automacoes de risco moderado ficam em `awaiting-approval` e so entram na fila apos aprovacao explicita;
- jobs manuais pendentes alem da janela configurada sao rejeitados automaticamente antes de novas leituras ou tentativas de approve/reject, com evento `automation_job_approval_expired` na trilha de auditoria;
- nenhuma execucao livre de shell ou comando arbitrario;
- toda execucao fica vinculada a `job_request` e trilha minima de auditoria;
- o worker faz aquisicao atomica do job ao transicionar de `queued` para `running`, evitando consumo duplicado por mais de um processo.
- falhas transitorias nao deixam o job em loop infinito: cada job carrega `attempt_count` e `max_attempts` persistidos no proprio `job_request`.
- falhas abaixo do limite nao voltam imediatamente para a fila principal: o job entra em `retry-scheduled`, com `retry_scheduled_at` e `retry_delay_seconds` persistidos no store operacional.
- retentativas vencidas sao adquiridas primeiro pelo worker direto do store operacional, reduzindo churn na fila principal e mantendo o proximo disparo auditavel.
- jobs ja aprovados ainda nao executados podem ser encerrados em `cancelled`; quando estavam em `queued`, o backend tambem remove o `job_id` da fila primaria para reduzir a janela de execucao indevida.
- ao atingir o limite de tentativas, o job sai da fila principal e vai para dead-letter dedicado, preservando o ultimo erro operacional.
- payloads de request, resultado, notas e artefatos persistidos no `job_request` passam por limites de profundidade, quantidade de itens/chaves e tamanho de string antes de serem gravados.
- jobs terminalizados antigos sao removidos automaticamente conforme `HELPDESK_OPERATIONAL_JOB_RETENTION_DAYS`, reduzindo exposicao desnecessaria de historico administrativo.
- se um `job_id` aparecer na fila sem aprovacao valida, o worker bloqueia a execucao e registra auditoria `automation_job_blocked`.

As respostas de `GET /api/v1/helpdesk/automation/jobs` e `GET /api/v1/helpdesk/automation/jobs/{job_id}` agora expõem tambem `retry_scheduled_at` e `retry_delay_seconds` quando houver nova tentativa pendente.

O resumo protegido `GET /api/v1/helpdesk/automation/summary` entrega somente metadados operacionais agregados: contagem por `approval_status` e `execution_status`, profundidade da fila principal e da dead-letter, alem das marcas de tempo mais antigas para jobs aguardando aprovacao, enfileirados, em execucao e com retry agendado. O endpoint nao devolve `payload_json`, reduzindo exposicao de contexto operacional sensivel durante troubleshooting.

Tambem existe agora o resumo protegido `GET /api/v1/helpdesk/reports/tickets/summary`, sob o mesmo escopo administrativo de auditoria. Esse endpoint agrega os snapshots analiticos dos tickets para devolver um recorte inicial da operacao: total de tickets, backlog em aberto, backlog atribuido e nao atribuido, backlog de alta prioridade, taxa simples de cobertura de atribuicao, taxa simples de resolucao, media de correlacao e distribuicao por status, prioridade, canal, categoria e fila.

Catalogo inicial:

- `ansible.ping_localhost`: `low` + `auto`, executa um playbook homologado de baixo risco via Ansible Runner para validar o runner local.
- `ansible.ticket_context_probe`: `moderate` + `manual`, executa um playbook homologado read-only via Ansible Runner, exige `ticket_id` e devolve um artefato estruturado com contexto operacional minimo do chamado.
- `noop.healthcheck`: `low` + `auto`, valida o caminho fila -> worker -> persistencia sem efeito colateral.
- `glpi.ticket_snapshot`: `moderate` + `manual`, consulta somente leitura de metadados operacionais do ticket no GLPI ou no mock local.

Os projetos homologados atuais do runner ficam em:

- `infra/automation-runner/projects/ping-localhost/`
- `infra/automation-runner/projects/ticket-context-probe/`

Subir o worker localmente:

```bash
cd backend
./run_automation_worker.sh
```

Criar um job de smoke test:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs \
  -H "X-Helpdesk-Automation-Key: $HELPDESK_AUTOMATION_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requested_by": "ops-ana",
    "automation_name": "noop.healthcheck",
    "reason": "smoke local da fila",
    "parameters": {"probe_label": "lab"}
  }'
```

Criar um job homologado vinculado a ticket:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs \
  -H "X-Helpdesk-Automation-Key: $HELPDESK_AUTOMATION_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requested_by": "ops-ana",
    "automation_name": "ansible.ticket_context_probe",
    "ticket_id": "GLPI-LOCAL-123",
    "reason": "coletar contexto minimo do chamado para diagnostico",
    "parameters": {"context_label": "diagnostico-local"}
  }'
```

Esse segundo exemplo retorna o job em `awaiting-approval`, sem enfileirar automaticamente.

Aprovar um job pendente:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs/<job_id>/approve \
  -H "X-Helpdesk-Automation-Approval-Key: $HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "acted_by": "supervisor-ana",
    "reason_code": "read_only_diagnostic_authorized"
  }'
```

Rejeitar um job pendente:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs/<job_id>/reject \
  -H "X-Helpdesk-Automation-Approval-Key: $HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "acted_by": "supervisor-ana",
    "reason_code": "outside_change_window"
  }'
```

Cancelar um job aprovado que ainda nao executou:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs/<job_id>/cancel \
  -H "X-Helpdesk-Automation-Approval-Key: $HELPDESK_AUTOMATION_APPROVAL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "acted_by": "supervisor-ana",
    "reason_code": "change_revoked"
  }'
```

As decisoes administrativas nao aceitam mais texto livre em `approve`, `reject` ou `cancel`. Use `reason_code` padronizado:

- `approve`: `change_window_validated`, `read_only_diagnostic_authorized`, `risk_review_completed`, `rollback_plan_confirmed`
- `reject`: `outside_change_window`, `risk_not_authorized`, `missing_prerequisites`, `insufficient_evidence`
- `cancel`: `change_revoked`, `scope_changed`, `duplicate_request`, `manual_intervention_completed`

Consultar jobs recentes:

```bash
curl http://127.0.0.1:18001/api/v1/helpdesk/automation/jobs \
  -H "X-Helpdesk-Automation-Read-Key: $HELPDESK_AUTOMATION_READ_ACCESS_TOKEN"
```

Consultar o resumo operacional protegido da fila:

```bash
curl http://127.0.0.1:18001/api/v1/helpdesk/automation/summary \
  -H "X-Helpdesk-Automation-Read-Key: $HELPDESK_AUTOMATION_READ_ACCESS_TOKEN"
```

Cada resposta de job agora devolve tambem:

- `risk_level`
- `approval_mode`
- `approval_required`
- `attempt_count`
- `max_attempts`
- `last_error`
- `dead_lettered_at`
- `approval_reason_code`
- `cancelled_by`
- `cancellation_reason_code`
- `cancellation_reason`
- `cancelled_at`
- `approval_acted_by`
- `approval_reason`
- `approval_updated_at`

Isso permite diferenciar rapidamente jobs ainda reexecutaveis de jobs descartados para analise posterior.

Nos jobs baseados em Ansible Runner, o resultado agora tambem pode incluir:

- `artifact_data`: retorno estruturado e sanitizado exportado pelo playbook homologado;
- `stdout_excerpt`: trecho curto do stdout do runner, util para troubleshooting sem vazar comandos arbitrarios.

## IA do bot

O backend agora aceita uma camada de IA configurável por provider, com `ollama` local como padrão do projeto.

Providers suportados na configuração:

- `ollama`
- `openai`
- `groq`
- `gemini`
- `claude`

Aliases aceitos:

- `openia` é normalizado para `openai`
- `anthropic` é normalizado para `claude`
- `local` é normalizado para `ollama`

Variáveis principais:

- `HELPDESK_LLM_ENABLED`
- `HELPDESK_LLM_PROVIDER`
- `HELPDESK_LLM_BASE_URL`
- `HELPDESK_LLM_MODEL`
- `HELPDESK_LLM_REQUEST_TIMEOUT`
- `HELPDESK_LLM_TEMPERATURE`
- `HELPDESK_API_ACCESS_TOKEN`

Credenciais opcionais por provider:

- `HELPDESK_OPENAI_API_KEY`
- `HELPDESK_GROQ_API_KEY`
- `HELPDESK_GEMINI_API_KEY`
- `HELPDESK_ANTHROPIC_API_KEY`

Exemplo padrão com Ollama local:

```env
HELPDESK_LLM_ENABLED=true
HELPDESK_LLM_PROVIDER=ollama
HELPDESK_LLM_BASE_URL=http://127.0.0.1:11434
HELPDESK_LLM_MODEL=llama3.1
```

O endpoint de status da IA não executa prompt real; ele mostra apenas o estado da configuração ativa:

```bash
curl http://127.0.0.1:18001/api/v1/helpdesk/ai/status \
  -H "X-Helpdesk-API-Key: $HELPDESK_API_ACCESS_TOKEN"
```

Para validar geração real com o provider configurado:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/ai/generate \
  -H "X-Helpdesk-API-Key: $HELPDESK_API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Responda em uma frase: qual é o objetivo deste backend?",
    "system_prompt": "Você é um assistente técnico objetivo.",
    "max_tokens": 120,
    "temperature": 0.2
  }'
```

## Identidade e validacao de usuario

Neste momento, a fonte principal de identidade do backend e o proprio GLPI.

- `HELPDESK_IDENTITY_PROVIDER=glpi` ativa a validacao pelo GLPI.
- O numero do usuario deve existir no cadastro do GLPI, em `phone`, `phone2` ou `mobile`.
- Se o numero nao existir no GLPI, o fluxo do WhatsApp rejeita a abertura do chamado.
- Para producao, mantenha `HELPDESK_IDENTITY_PROVIDER=glpi`; `mock-file` deve ficar restrito a laboratorio, testes e demonstracoes controladas.
- O papel operacional passa a ser resolvido por um mapeamento configuravel de perfis do GLPI.
- `HELPDESK_WHATSAPP_PUBLIC_NUMBER` representa o numero publico do bot/helpdesk para os usuarios. Ele nao substitui `HELPDESK_WHATSAPP_PHONE_NUMBER_ID`, que continua sendo o identificador exigido pela Meta para envio de mensagens.
- `HELPDESK_WHATSAPP_DELIVERY_PROVIDER` escolhe o canal de saída: `auto`, `meta`, `evolution` ou `mock`.
- Com `HELPDESK_WHATSAPP_DELIVERY_PROVIDER=auto`, o backend prefere a Evolution API quando `HELPDESK_EVOLUTION_BASE_URL`, `HELPDESK_EVOLUTION_API_KEY` e `HELPDESK_EVOLUTION_INSTANCE_NAME` estiverem completos; caso contrário, usa Meta se estiver configurada; se nenhum canal estiver pronto, mantém resposta simulada em `mock`.
- `HELPDESK_EVOLUTION_WEBHOOK_SECRET` permite validar um header dedicado no webhook da Evolution API sem reutilizar a chave global da instância.

Variaveis padrao de mapeamento:

- `HELPDESK_IDENTITY_GLPI_USER_PROFILES=Self-Service`
- `HELPDESK_IDENTITY_GLPI_TECHNICIAN_PROFILES=Technician`
- `HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES=Supervisor`
- `HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES=Super-Admin,Admin,Administrator`

Se quiser tratar um perfil existente do GLPI como supervisor, basta ajustar essas variaveis. Exemplo:

```env
HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES=Super-Admin
HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES=
```

No laboratorio deste repositório, esse mapeamento é o que faz o usuário atual com perfil `Super-Admin` seguir o fluxo de `supervisor` no WhatsApp.

Os numeros operacionais informados neste ambiente ficaram assim:

- usuario exemplo: `+5521997775269`
- supervisor: `+5521972008679`
- numero publico do helpdesk/bot: `+553299384534`

O arquivo local de identidades em [backend/data/identities.json](data/identities.json) continua disponivel apenas para modo mock e testes, controlado por `HELPDESK_IDENTITY_PROVIDER=mock-file` e `HELPDESK_IDENTITY_STORE_PATH`. Ele nao deve ser a fonte autoritativa de identidade em producao.

Para o laboratorio isolado, [backend/data/identities.lab.json](data/identities.lab.json) continua sendo gerado como apoio para testes, enquanto o seed do laboratorio grava os telefones diretamente no GLPI para usuarios como:

- `Maria Santos`
- `Carlos Lima`
- `Ana Souza`
- `Paula Almeida`
- `Bruno Costa`
- `Renata Melo`

Cada identidade pode carregar `glpi_user_id`, permitindo simular o espelho local do cadastro do GLPI em modo mock.

## Portas do backend

O backend não depende mais da porta `8000`.

- Host padrão: `127.0.0.1`
- Porta inicial: `18001`
- Faixa de fallback: `18001-18010`
- Modo estrito opcional: `HELPDESK_API_PORT_STRICT=true`

As portas são controladas por `HELPDESK_API_HOST`, `HELPDESK_API_PORT`, `HELPDESK_API_PORT_MAX` e `HELPDESK_API_PORT_STRICT`.

Para o webhook oficial da Meta, mantenha o backend escutando em `127.0.0.1` e publique externamente apenas via reverse proxy ou túnel controlado.

Para os webhooks oficiais, trate estes campos como obrigatórios:

- `HELPDESK_WHATSAPP_VERIFY_TOKEN`
- `HELPDESK_WHATSAPP_APP_SECRET`
- `HELPDESK_EVOLUTION_WEBHOOK_SECRET`

Sem esses valores, os endpoints oficiais respondem como não configurados.

Para verificar a porta escolhida sem subir a API:

```bash
./run_dev.sh --dry-run
```

## Endpoints principais

- `GET /health`
- `POST /api/v1/helpdesk/tickets/open`
- `POST /api/v1/helpdesk/triage`
- `GET /api/v1/helpdesk/tickets/{ticket_id}`
- `GET /api/v1/helpdesk/audit/events`
- `POST /api/v1/helpdesk/automation/jobs`
- `GET /api/v1/helpdesk/automation/jobs`
- `GET /api/v1/helpdesk/automation/summary`
- `GET /api/v1/helpdesk/automation/jobs/{job_id}`
- `POST /api/v1/helpdesk/automation/jobs/{job_id}/approve`
- `POST /api/v1/helpdesk/automation/jobs/{job_id}/reject`
- `POST /api/v1/helpdesk/automation/jobs/{job_id}/cancel`
- `GET /api/v1/helpdesk/identities/{phone_number}`
- `POST /api/v1/helpdesk/incidents/correlate`
- `GET /api/v1/helpdesk/ai/status`
- `POST /api/v1/helpdesk/ai/generate`
- `GET /api/v1/webhooks/whatsapp/verify`
- `POST /api/v1/webhooks/whatsapp/meta`
- `POST /api/v1/webhooks/whatsapp/evolution`
- `POST /api/v1/webhooks/whatsapp/messages` (uso interno, protegido por token)

## Bootstrap do laboratorio

Com `GLPI` e `Zabbix` ja de pe em `infra/helpdesk-lab`, rode:

```bash
cd infra/helpdesk-lab
./scripts/bootstrap-integrations.sh
./scripts/seed-glpi.sh
./scripts/seed-zabbix.sh
```

Ou tudo de uma vez:

```bash
./scripts/seed-test-data.sh
```

Isso faz cinco coisas:

- habilita a API do GLPI no banco do laboratorio;
- cria o cliente de API do GLPI para a faixa Docker local;
- alinha o `backend/.env` para usar o lab;
- semeia usuarios, ativos, tickets e identidades coerentes no GLPI, incluindo telefone no cadastro do usuario;
- abre problemas reais no Zabbix para correlacao com esses mesmos ativos.

Depois disso, reinicie o backend:

```bash
cd backend
./run_dev.sh
```

## Backfill analitico do GLPI

Para enriquecer tickets historicos que foram abertos antes da persistencia de `externalid`, `requesttypes_id`, `itilcategories_id` e vinculo com inventario, rode primeiro em `dry-run`:

```bash
cd backend
./run_glpi_backfill.sh --limit 25
```

O comando prioriza o evento operacional `ticket_opened` gravado no PostgreSQL e usa a triagem local por regras apenas como fallback para sugerir categoria. Quando quiser aplicar as atualizacoes no GLPI, repita com `--apply`:

```bash
./run_glpi_backfill.sh --limit 25 --apply
```

Para inspecionar tickets especificos, repita `--ticket-id`:

```bash
./run_glpi_backfill.sh --ticket-id 20 --ticket-id 22 --apply
```

Observacao: tickets `closed` podem aparecer no `dry-run`, mas a API do GLPI costuma bloquear a atualizacao analitica desses registros. No laboratorio, se voce realmente precisar materializar esse backfill em tickets ja encerrados, faca isso por ajuste controlado no banco do lab e nao pelo endpoint do GLPI.

Para materializar uma camada analitica simples em PostgreSQL com os tickets enriquecidos do GLPI, sincronize snapshots para `${HELPDESK_OPERATIONAL_POSTGRES_SCHEMA}.ticket_analytics_snapshot`:

```bash
./run_glpi_analytics_sync.sh --limit 25
```

O sync usa detalhes do ticket no GLPI e, quando existir, o evento durável `ticket_opened` para completar `asset_name`, `service_name`, `source_channel`, `routed_to` e `correlation_event_count`.

## Exemplo de abertura direta de ticket

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/tickets/open \
  -H "X-Helpdesk-API-Key: $HELPDESK_API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Usuário sem acesso ao ERP",
    "description": "O usuário informa erro de autenticação desde 08:10.",
    "category": "acesso",
    "asset_name": "erp-web-01",
    "service_name": "erp",
    "priority": "high",
    "requester": {
      "external_id": "u123",
      "display_name": "João Silva",
      "phone_number": "+5511999999999",
      "role": "user"
    }
  }'
```

## Exemplo de triagem inicial

```bash
curl -X POST http://127.0.0.1:18001/api/v1/helpdesk/triage \
  -H "X-Helpdesk-API-Key: $HELPDESK_API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Usuarios sem acesso ao ERP",
    "description": "Time financeiro relata erro de autenticacao e nenhum usuario consegue entrar no ERP.",
    "service_name": "erp"
  }'
```

Esse endpoint nao executa nenhuma acao operacional. Ele apenas devolve uma triagem estruturada com:

- categoria sugerida;
- prioridade sugerida ou preservada;
- fila recomendada;
- resumo inicial;
- proximos passos seguros para atendimento humano.

## Exemplo de mensagem normalizada do WhatsApp

```bash
curl -X POST http://127.0.0.1:18001/api/v1/webhooks/whatsapp/messages \
  -H "X-Helpdesk-API-Key: $HELPDESK_API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sender_phone": "+5511999999999",
    "sender_name": "João Silva",
    "text": "Estou sem acesso ao ERP",
    "requester_role": "user",
    "asset_name": "erp-web-01",
    "service_name": "erp",
    "priority": "high"
  }'
```

Se o número resolvido pertencer a técnico, supervisor ou admin e a mensagem começar com `/`, o backend trata o conteúdo como comando operacional, não como abertura de chamado.

Quando `HELPDESK_IDENTITY_PROVIDER=glpi`, uma mensagem livre de número desconhecido nao gera fallback para usuário interno: o número precisa existir no GLPI.

Comandos iniciais suportados:

- `/help`
- `/me`
- `/open <texto>`
- `/ticket <ticket_id>`
- `/correlate <ativo-ou-servico>`
- `/comment <ticket_id> <texto>`
- `/status <ticket_id> <new|processing|planned|waiting|solved|closed>`
- `/assign <ticket_id> <telefone-ou-external_id>`

Regras de papel no MVP:

- `user`: mensagem livre continua abrindo chamado normalmente. Se enviar `finalizar chamado`, `encerrar chamado` ou `fechar chamado`, o backend lista tickets abertos do próprio solicitante, inclusive quando o problema já foi resolvido pelo próprio usuário antes do suporte concluir o atendimento.
- `technician`: mensagem livre entra no assistente operacional e não abre ticket automaticamente; comandos permitidos: `/help`, `/me`, `/open`, `/ticket`, `/correlate`, `/comment`, `/status`
- `supervisor`: mesmo fluxo operacional assistido, com permissão adicional de `/assign`.
- `admin`: mesmo fluxo operacional assistido, com permissão adicional de `/assign`.

Com `HELPDESK_IDENTITY_PROVIDER=glpi`, esses papéis vêm do mapeamento configurado para os perfis do usuário no GLPI. Se no futuro vocês criarem um perfil específico de supervisão ou integrarem com AD/API interna, o backend pode mudar a origem de identidade sem reverter o fluxo do WhatsApp.

Restrições de status no MVP:

- `technician`: `processing`, `planned`, `waiting`, `solved`
- `supervisor` e `admin`: `new`, `processing`, `planned`, `waiting`, `solved`, `closed`

Fluxo de finalização pelo usuário:

- o usuário envia `finalizar chamado`, `encerrar chamado` ou `fechar chamado`
- o backend lista até 5 tickets abertos do próprio solicitante, em ordem de atualização mais recente
- isso inclui chamados em `new`, `planned`, `waiting`, `processing` ou `solved`, para cobrir casos em que o próprio usuário já resolveu a demanda e só quer encerrar o registro
- o usuário responde com o número da opção ou com o ID do ticket
- o backend altera o status do ticket escolhido para `closed` e registra um comentário de auditoria via WhatsApp

Fluxo de interação do atendente:

- quando técnico, supervisor ou admin usa `/comment <ticket_id> <texto>`, o backend registra a interação como follow-up no GLPI
- depois disso, o backend tenta localizar o solicitante do ticket e envia a mesma atualização para o WhatsApp do usuário, referenciando o chamado
- se o ticket estiver sem solicitante resolvido ou sem telefone cadastrado, o comentário continua sendo salvo no GLPI e o atendente recebe esse aviso no retorno do comando

Entendimento de contexto durante a interação:

- se o usuário mudar claramente de assunto no meio da coleta, o backend reinicia o contexto com a nova descrição em vez de prender a conversa na categoria anterior
- se o usuário sair da coleta e passar a pedir finalização de chamado, o backend troca para o fluxo de finalização
- se o usuário abandonar a seleção de finalização e voltar a descrever um novo incidente, o backend abandona a lista pendente e retoma a abertura inteligente de chamado
- quando a camada LLM está habilitada, essa mudança de contexto também pode ser inferida em mensagens ambíguas; sem LLM, o fallback continua funcionando por regras seguras

## Exemplo de webhook oficial da Meta

O endpoint oficial aceita o payload bruto da Meta e pode validar a assinatura `X-Hub-Signature-256` quando `HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=true`.

```bash
curl -X POST http://127.0.0.1:18001/api/v1/webhooks/whatsapp/meta \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=<assinatura>" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [
      {
        "changes": [
          {
            "value": {
              "contacts": [
                {
                  "wa_id": "5511999999999",
                  "profile": {"name": "João Silva"}
                }
              ],
              "messages": [
                {
                  "from": "5511999999999",
                  "id": "wamid.HBgLTESTE123",
                  "type": "text",
                  "text": {"body": "Estou sem acesso ao ERP"}
                }
              ]
            }
          }
        ]
      }
    ]
  }'
```

## Exemplo de webhook da Evolution API

O endpoint dedicado da Evolution recebe o payload bruto de eventos e normaliza mensagens de `MESSAGES_UPSERT` para o fluxo interno. Se `HELPDESK_EVOLUTION_WEBHOOK_SECRET` estiver configurado, envie o header `X-Evolution-Webhook-Secret` com o mesmo valor.

```bash
curl -X POST http://127.0.0.1:18001/api/v1/webhooks/whatsapp/evolution \
  -H "Content-Type: application/json" \
  -H "X-Evolution-Webhook-Secret: <segredo>" \
  -d '{
    "event": "MESSAGES_UPSERT",
    "data": {
      "key": {
        "remoteJid": "5521997775269@s.whatsapp.net",
        "fromMe": false,
        "id": "EVO-123"
      },
      "pushName": "Maria Santos",
      "message": {
        "conversation": "Estou sem acesso ao ERP"
      },
      "messageType": "conversation"
    }
  }'
```

## Saída por Evolution API

Para enviar respostas do backend pela Evolution API, configure no `backend/.env`:

```env
HELPDESK_WHATSAPP_DELIVERY_PROVIDER=auto
HELPDESK_EVOLUTION_BASE_URL=http://localhost:8080
HELPDESK_EVOLUTION_API_KEY=<apikey-da-evolution>
HELPDESK_EVOLUTION_INSTANCE_NAME=helpdeskAutomacao
```

Se quiser forçar a Meta mesmo com Evolution disponível, use `HELPDESK_WHATSAPP_DELIVERY_PROVIDER=meta`.

## Exemplo de consulta de ticket

```bash
curl http://127.0.0.1:18001/api/v1/helpdesk/tickets/GLPI-LOCAL-1713456789
```

## Exemplo de consulta de identidade

```bash
curl http://127.0.0.1:18001/api/v1/helpdesk/identities/%2B5511912345678
```

## Integrações reais

As integrações reais já têm pontos de entrada separados, mas ainda estão em estágio inicial. O comportamento desta fase é:

- GLPI: cria ticket em modo mock quando não há credenciais.
- GLPI: permite consultar tickets locais em memória no modo mock durante o desenvolvimento.
- Zabbix: retorna correlação vazia em modo mock quando não há credenciais.
- WhatsApp: confirma envio em modo mock quando não há credenciais.
- WhatsApp: o endpoint oficial da Meta pode validar assinatura HMAC; em desenvolvimento isso pode ser desligado com `HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=false`.
- Diretório de identidades: resolve número de telefone para usuário, técnico, supervisor ou admin antes de montar o ticket vindo do WhatsApp.
- Diretório de identidades: também pode carregar `glpi_user_id` para vincular o solicitante ao usuário correto na criação do ticket no GLPI.

Esse comportamento permite desenvolver o orquestrador antes de acoplar o ambiente externo.
