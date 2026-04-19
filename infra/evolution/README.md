# Evolution API

Artefatos de apoio para operar uma instância adicional da `Evolution API` já existente no host, sem subir outro container e sem mexer na instância atual em produção.

## Objetivo

Criar uma nova instância lógica dentro da `Evolution API` já rodando em `http://localhost:8080`, separando o número usado pela automação de helpdesk do assistente virtual que já existe hoje.

## Estado atual

No host atual existe um container `evolution-api` com a `Evolution API 2.2.3` exposta em `http://localhost:8080`.

O repositório agora inclui um script para:

- localizar a chave global da API via `.env` local ou `docker inspect`;
- verificar se a instância já existe;
- criar uma nova instância `WHATSAPP-BAILEYS` com QR Code;
- registrar localmente o nome conectado, o JID do Evolution e o número público usado pelo helpdesk;
- manter o fluxo repetível sem depender só do Manager web.

## Arquivos

```text
infra/evolution/
├── .env.example
├── README.md
├── configure_webhook.sh
└── create_instance.sh
```

## Uso

Opcionalmente copie o exemplo de ambiente:

```bash
cd /home/ricardo/Script_Linux_Debian/infra/evolution
cp .env.example .env
```

Depois rode:

```bash
./create_instance.sh
```

Para configurar o webhook da instância no backend:

```bash
./configure_webhook.sh https://SEU-BACKEND/api/v1/webhooks/whatsapp/evolution
```

Por padrão o script cria a instância:

- nome: `helpdeskAutomacao`
- integração: `WHATSAPP-BAILEYS`
- QR Code: habilitado
- grupos: ignorados
- chamadas: rejeitadas automaticamente

Campos opcionais no `.env` local:

- `EVOLUTION_CONNECTED_NAME`: nome exibido na sessão já conectada
- `EVOLUTION_CONNECTED_JID`: JID conectado no Evolution, por exemplo `553299384534@s.whatsapp.net`
- `EVOLUTION_PUBLIC_NUMBER`: número público apresentado aos usuários, por exemplo `+553299384534`
- `EVOLUTION_WEBHOOK_URL`: URL completa e alcançável pelo processo/container da Evolution apontando para `.../api/v1/webhooks/whatsapp/evolution`
- `EVOLUTION_WEBHOOK_SECRET`: segredo opcional enviado no header configurado abaixo
- `EVOLUTION_WEBHOOK_SECRET_HEADER`: header usado para o segredo, por padrão `X-Evolution-Webhook-Secret`
- `EVOLUTION_WEBHOOK_EVENTS`: lista CSV de eventos; por padrão `MESSAGES_UPSERT`
- `EVOLUTION_WEBHOOK_BY_EVENTS`: `true` ou `false`; por padrão `false`
- `EVOLUTION_WEBHOOK_BASE64`: `true` ou `false`; por padrão `false`

Esses campos são apenas metadata operacional local. Eles não substituem o `HELPDESK_WHATSAPP_PUBLIC_NUMBER` do backend nem o `HELPDESK_WHATSAPP_PHONE_NUMBER_ID` da Meta.

## Próximo passo operacional

Abra o Manager para escanear o QR Code e conectar o número:

- `http://localhost:8080/manager/`

Se a sessão já estiver conectada, grave o nome e o JID no `.env` local para manter o inventário operacional alinhado.

O backend agora aceita webhook bruto da Evolution em:

- `POST /api/v1/webhooks/whatsapp/evolution`

O script `configure_webhook.sh` usa a rota nativa da Evolution `POST /webhook/set/:instanceName` e grava a configuração por instância.

Para ligar a instância ao backend, o webhook fica configurado com:

- evento: `MESSAGES_UPSERT`
- `base64`: `false`
- URL: endpoint público do backend apontando para `/api/v1/webhooks/whatsapp/evolution`
- header opcional: `X-Evolution-Webhook-Secret: <segredo>` quando `HELPDESK_EVOLUTION_WEBHOOK_SECRET` estiver configurado no backend

Se preferir, grave a URL no `.env` local e rode sem argumentos:

```bash
./configure_webhook.sh
```

O importante é que a URL seja alcançável a partir do processo da Evolution. Se a Evolution estiver em container e o backend no host, use um endereço roteável a partir do container em vez de `127.0.0.1`.

## Observação importante

O backend deste repositório já consome o payload bruto do webhook da Evolution API para mensagens de entrada e também pode responder pela própria Evolution API quando o backend tiver `HELPDESK_EVOLUTION_BASE_URL`, `HELPDESK_EVOLUTION_API_KEY` e `HELPDESK_EVOLUTION_INSTANCE_NAME` configurados.

Hoje ele já aceita:

- webhook da Meta em `/api/v1/webhooks/whatsapp/meta`
- webhook da Evolution em `/api/v1/webhooks/whatsapp/evolution`
- mensagem normalizada em `/api/v1/webhooks/whatsapp/messages`

O próximo passo operacional passa a ser apenas apontar o webhook da instância para o backend e preencher a `API key` da Evolution no `backend/.env` se você quiser que as confirmações também saiam por esse canal.
