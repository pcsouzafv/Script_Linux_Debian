# Ativacao do Canal WhatsApp

## Objetivo

Este guia consolida o que falta para colocar o canal WhatsApp em operacao no backend atual, sem perder o vinculo com identidade, GLPI e trilha de auditoria.

O backend ja suporta:

- webhook oficial da Meta em `GET /api/v1/webhooks/whatsapp/verify` e `POST /api/v1/webhooks/whatsapp/meta`;
- webhook da Evolution API em `POST /api/v1/webhooks/whatsapp/evolution`;
- ingestao interna de mensagem normalizada em `POST /api/v1/webhooks/whatsapp/messages`;
- resposta de confirmacao por Meta, Evolution ou `mock`, conforme configuracao;
- abertura de ticket no GLPI a partir da mensagem recebida;
- fluxo diferente para usuario final e para tecnico ou supervisor;
- auditoria operacional minima do atendimento.

## Decisao inicial: qual provedor usar

O repositorio ja suporta dois caminhos.

### Caminho A: Meta WhatsApp Cloud API

Use quando o objetivo for:

- canal oficial;
- operacao mais aderente a producao;
- validacao por assinatura HMAC;
- menor dependencia de uma instancia propria intermediaria.

### Caminho B: Evolution API

Use quando o objetivo for:

- ativacao mais rapida no host atual;
- aproveitar uma Evolution ja existente;
- operar via instancia dedicada do helpdesk;
- laboratorio ou piloto controlado com mais autonomia local.

### Regra importante de envio

Quando `HELPDESK_WHATSAPP_DELIVERY_PROVIDER=auto`, o backend hoje prioriza:

1. `evolution`, se a Evolution estiver configurada;
2. `meta`, se a Evolution nao estiver configurada e a Meta estiver;
3. `mock`, se nenhum provedor estiver pronto.

Se voce quiser evitar ambiguidade, defina explicitamente:

- `HELPDESK_WHATSAPP_DELIVERY_PROVIDER=meta`, ou
- `HELPDESK_WHATSAPP_DELIVERY_PROVIDER=evolution`.

## Pre-requisitos comuns

Antes de ativar qualquer provedor, confirme estes pontos:

- o backend esta no ar e responde `GET /health`;
- o backend esta publicado por HTTPS em URL alcancavel pelo provedor;
- `HELPDESK_API_ACCESS_TOKEN` esta definido;
- o GLPI esta configurado no backend com credenciais reais;
- `HELPDESK_IDENTITY_PROVIDER=glpi` esta mantido como fonte autoritativa de identidade;
- o numero do solicitante existe no GLPI se `HELPDESK_IDENTITY_PROVIDER=glpi`;
- os perfis do GLPI estao mapeados corretamente para usuario, tecnico, supervisor e admin;
- `HELPDESK_WHATSAPP_PUBLIC_NUMBER` reflete o numero que os usuarios vao enxergar.

Sem identidade resolvida, o webhook ate pode entrar, mas a abertura do ticket e o roteamento operacional ficam comprometidos.
Em producao, o recomendado e nao usar `mock-file`; o numero precisa existir no GLPI para que o backend autorize o atendimento.

## Variaveis comuns no `backend/.env`

Independentemente do provedor, estas variaveis devem ser revisadas:

```env
HELPDESK_API_ACCESS_TOKEN=troque-isto
HELPDESK_IDENTITY_PROVIDER=glpi
HELPDESK_WHATSAPP_PUBLIC_NUMBER=+55DDDNUMERO
HELPDESK_GLPI_BASE_URL=https://SEU-GLPI/apirest.php
HELPDESK_GLPI_APP_TOKEN=...
HELPDESK_GLPI_USER_TOKEN=...
HELPDESK_GLPI_QUEUE_GROUP_MAP={"ServiceDesk-N1":"TI > Service Desk > N1","ServiceDesk-Acessos":"TI > Service Desk > Acessos","Infraestrutura-N1":"TI > Infraestrutura > N1","NOC-Critico":"TI > NOC > Critico"}
```

## Caminho A: ativacao com Meta

### Variaveis obrigatorias

```env
HELPDESK_WHATSAPP_DELIVERY_PROVIDER=meta
HELPDESK_WHATSAPP_VERIFY_TOKEN=troque-isto
HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=true
HELPDESK_WHATSAPP_ACCESS_TOKEN=...
HELPDESK_WHATSAPP_PHONE_NUMBER_ID=...
HELPDESK_WHATSAPP_APP_SECRET=...
HELPDESK_WHATSAPP_PUBLIC_NUMBER=+55DDDNUMERO
```

### Endpoints que a Meta precisa enxergar

- verificacao: `GET /api/v1/webhooks/whatsapp/verify`
- entrada de mensagens: `POST /api/v1/webhooks/whatsapp/meta`

### Validacao local minima

Com o backend publicado, teste a verificacao:

```bash
curl "https://SEU-BACKEND/api/v1/webhooks/whatsapp/verify?hub.mode=subscribe&hub.challenge=123456&hub.verify_token=troque-isto"
```

Se estiver correto, a resposta deve ser `123456`.

### Observacoes praticas

- mantenha `HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=true` fora do desenvolvimento;
- o `APP_SECRET` e usado para validar `X-Hub-Signature-256`;
- o backend rejeita o webhook da Meta com `503` se a validacao estiver habilitada, mas o segredo nao tiver sido configurado.

## Caminho B: ativacao com Evolution API

### Variaveis obrigatorias

```env
HELPDESK_WHATSAPP_DELIVERY_PROVIDER=evolution
HELPDESK_WHATSAPP_PUBLIC_NUMBER=+55DDDNUMERO
HELPDESK_EVOLUTION_BASE_URL=http://SEU-HOST:8080
HELPDESK_EVOLUTION_API_KEY=...
HELPDESK_EVOLUTION_INSTANCE_NAME=helpdeskAutomacao
HELPDESK_EVOLUTION_WEBHOOK_SECRET=troque-isto
HELPDESK_EVOLUTION_LID_PHONE_MAP=
```

### Endpoint que a Evolution precisa enxergar

- entrada de mensagens: `POST /api/v1/webhooks/whatsapp/evolution`

### Configuracao pratica

Se a instancia ja existir, configure o webhook com o script do repositorio:

```bash
cd infra/evolution
./configure_webhook.sh https://SEU-BACKEND/api/v1/webhooks/whatsapp/evolution
```

Se ainda nao existir, primeiro crie ou conecte a instancia dedicada do helpdesk e depois aponte o webhook.

### Observacoes praticas

- o backend exige `HELPDESK_EVOLUTION_WEBHOOK_SECRET` e valida o header `X-Evolution-Webhook-Secret`;
- se a versao da Evolution nao repassar headers customizados, o backend tambem aceita o mesmo segredo em `?secret=...` no webhook;
- em `auto`, se a Evolution estiver configurada, ela sera usada para envio antes da Meta;
- se a Evolution entregar o remetente como `220095666237694@lid`, cadastre um mapa explicito em `HELPDESK_EVOLUTION_LID_PHONE_MAP`, por exemplo `{"220095666237694":"+5521972008679"}`;
- `@lid` sem mapa explicito continua ignorado, para manter a regra de que apenas telefones cadastrados no GLPI podem acionar o assistente;
- para producao, prefira uma instancia separada do bot atual para nao misturar atendimento e automacao.

## Validacao ponta a ponta recomendada

Execute nesta ordem.

### 1. Validar o backend e as integracoes base

- `GET /health`
- `GET /ops`, se o painel operacional estiver habilitado
- acesso do backend ao GLPI
- resolucao de identidade pelo numero do telefone

### 2. Validar a mensagem normalizada sem depender do provedor

Isso isola o fluxo do backend antes de ligar o webhook real:

```bash
curl -X POST http://127.0.0.1:18001/api/v1/webhooks/whatsapp/messages \
  -H "Content-Type: application/json" \
  -H "X-Helpdesk-API-Key: troque-isto" \
  -d '{
    "sender_phone": "5511999999999",
    "sender_name": "Teste WhatsApp",
    "text": "Nao consigo acessar o ERP",
    "external_message_id": "msg-local-001"
  }'
```

Resultado esperado:

- o backend processa a mensagem;
- identifica o remetente;
- abre ou continua o fluxo adequado;
- cria ticket no GLPI quando for usuario final;
- devolve resposta de confirmacao.

### 3. Validar o webhook bruto do provedor

Depois valide o endpoint real:

- Meta: `POST /api/v1/webhooks/whatsapp/meta`
- Evolution: `POST /api/v1/webhooks/whatsapp/evolution`

Resultado esperado:

- o payload e normalizado;
- eventos nao suportados sao ignorados com nota;
- a mensagem util vira atendimento no backend.

### 4. Validar o GLPI

No GLPI, confirme:

- ticket criado com assunto iniciado por `WhatsApp:`;
- `externalid` presente;
- grupo executor atribuido conforme `HELPDESK_GLPI_QUEUE_GROUP_MAP`;
- descricao contendo origem do canal;
- followups e solution seguindo o fluxo normal do backend.

### 5. Validar a resposta ao usuario

Confirme se a resposta sai pelo provedor esperado:

- `meta`, se o envio estiver fixado em Meta;
- `evolution`, se o envio estiver fixado em Evolution;
- `mock`, apenas em desenvolvimento.

## Checklist curto de go-live

- URL publica do backend com HTTPS
- GLPI acessivel pelo backend
- identidade por telefone validada
- numero publico configurado
- segredo do webhook configurado
- provedor de entrega definido explicitamente
- teste de mensagem normalizada aprovado
- teste de webhook bruto aprovado
- teste real com usuario aprovado
- ticket visivel no GLPI com fila correta

## Leitura pratica para o proximo passo

Depois que o canal estiver de pe, os proximos blocos de valor sao:

1. endurecer a identidade por telefone e perfis do GLPI;
2. padronizar respostas de confirmacao e acompanhamento;
3. publicar notificacoes tecnicas para fila operacional;
4. conectar o canal WhatsApp com os proximos fluxos de assistencia e autonomia controlada.
