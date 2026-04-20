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

O arquivo [backend/.env](/home/ricardo/Script_Linux_Debian/backend/.env) já foi criado com todas as chaves de credenciais esperadas. Enquanto os valores continuarem como placeholder, o backend trata essas integrações como não configuradas e permanece em modo mock. Quando quiser ativar chamadas externas, voce pode usar tanto tokens quanto usuario e senha, dependendo da integracao.

As rotas internas sob `/api/v1/helpdesk/*` e o endpoint bruto `/api/v1/webhooks/whatsapp/messages` exigem um token de acesso enviado em `X-Helpdesk-API-Key` ou `Authorization: Bearer <token>`. Configure `HELPDESK_API_ACCESS_TOKEN` antes de consumir ou publicar essas rotas.

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

O arquivo local de identidades em [backend/data/identities.json](/home/ricardo/Script_Linux_Debian/backend/data/identities.json) continua disponivel apenas para modo mock e testes, controlado por `HELPDESK_IDENTITY_PROVIDER=mock-file` e `HELPDESK_IDENTITY_STORE_PATH`.

Para o laboratorio isolado, [backend/data/identities.lab.json](/home/ricardo/Script_Linux_Debian/backend/data/identities.lab.json) continua sendo gerado como apoio para testes, enquanto o seed do laboratorio grava os telefones diretamente no GLPI para usuarios como:

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
cd /home/ricardo/Script_Linux_Debian/infra/helpdesk-lab
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
cd /home/ricardo/Script_Linux_Debian/backend
./run_dev.sh
```

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
