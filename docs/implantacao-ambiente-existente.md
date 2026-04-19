# Guia de Implantacao para Empresa com GLPI e Zabbix Ja Existentes

## Objetivo

Este guia descreve o caminho mais seguro para implantar o backend deste repositorio em uma empresa que ja possui `GLPI` e `Zabbix` em producao. Nesse cenario, o foco nao e reinstalar a base, e sim conectar o backend aos sistemas oficiais ja existentes.

## Quando usar este guia

Use este roteiro quando a empresa:

- ja possui `GLPI` em producao;
- ja possui `Zabbix` em producao;
- quer habilitar atendimento por WhatsApp sem trocar a base atual;
- quer introduzir o backend de orquestracao com o menor impacto possivel.

Se a empresa ainda nao tem `GLPI` e `Zabbix`, use primeiro o guia [docs/implantacao-empresa.md](/home/ricardo/Script_Linux_Debian/docs/implantacao-empresa.md).

## Principio de implantacao

Neste cenario, a regra principal e esta:

- `GLPI` e `Zabbix` continuam sendo os sistemas oficiais da empresa;
- o backend entra como camada adicional de integracao;
- nenhuma mudanca em producao deve exigir reinstalar `GLPI` ou `Zabbix`.

## Passo 1: Levantamento do ambiente atual

Antes de implantar o backend, levante estas informacoes:

- URL publica e URL interna do `GLPI`;
- URL da API do `Zabbix`;
- versao atual de `GLPI` e `Zabbix`;
- modelo de autenticacao disponivel para cada API;
- perfis atuais do GLPI usados por usuarios, tecnicos e administradores;
- politica atual de firewall entre o host do backend e os sistemas existentes;
- se o provedor de WhatsApp sera `Evolution` ou `Meta`.

E importante registrar tambem:

- nome do grupo piloto;
- janelas de manutencao;
- equipe responsavel por `GLPI`, `Zabbix`, backend e mensageria.

Resultado esperado:

- mapa tecnico fechado;
- equipe responsavel definida;
- dependencia de rede conhecida antes do deploy.

## Passo 2: Validar os contratos de integracao

Nao conecte o backend direto em producao sem validar os contratos minimos.

Confirme estes pontos com a equipe da empresa:

1. `GLPI` tem API REST habilitada.
2. Existe `app_token` e `user_token`, ou conta de servico aprovada.
3. `Zabbix` aceita token de API ou conta de servico para integracao.
4. O cadastro do usuario no `GLPI` contem telefone em `phone`, `phone2` ou `mobile`.
5. O perfil do usuario no `GLPI` realmente representa o papel operacional esperado.

Sem esses pontos, o backend nao consegue executar o fluxo principal com previsibilidade.

## Passo 3: Higienizar o cadastro do GLPI antes do go-live

No ambiente atual, o maior risco operacional costuma ser dado inconsistente no cadastro do usuario. Antes do piloto, revise:

- usuarios sem telefone;
- telefones com mascara inconsistente;
- telefones duplicados entre pessoas diferentes;
- perfis tecnicos misturados com perfis de usuario final;
- usuarios desativados ainda com telefone em uso.

Regra pratica para o piloto:

- o numero de WhatsApp do solicitante precisa existir no `GLPI`;
- o perfil do usuario precisa ser mapeavel para `user`, `technician`, `supervisor` ou `admin`.

Resultado esperado:

- base de usuarios utilizavel pelo backend;
- menos falhas de identificacao no primeiro dia.

## Passo 4: Preparar o host do backend sem tocar na base atual

Publique o backend em um host ou VM separada. Esse host precisa apenas:

- acessar o `GLPI` existente;
- acessar o `Zabbix` existente;
- acessar o provedor de WhatsApp;
- publicar o webhook do backend por HTTPS.

Exemplo de preparacao:

```bash
sudo mkdir -p /opt/helpdesk-orchestrator
sudo chown "$USER":"$USER" /opt/helpdesk-orchestrator
cd /opt/helpdesk-orchestrator
git clone <URL-DO-REPOSITORIO> .
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

## Passo 5: Configurar o backend para usar os servicos existentes

Preencha o arquivo `.env` com os endpoints e segredos corporativos.

Exemplo base:

```env
HELPDESK_ENVIRONMENT=production
HELPDESK_API_HOST=127.0.0.1
HELPDESK_API_PORT=18001
HELPDESK_API_PORT_MAX=18001
HELPDESK_API_PORT_STRICT=true

HELPDESK_IDENTITY_PROVIDER=glpi
HELPDESK_IDENTITY_GLPI_USER_PROFILES=Self-Service
HELPDESK_IDENTITY_GLPI_TECHNICIAN_PROFILES=Technician
HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES=Supervisor
HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES=Super-Admin,Admin,Administrator

HELPDESK_GLPI_BASE_URL=https://glpi.empresa.local/apirest.php
HELPDESK_GLPI_APP_TOKEN=
HELPDESK_GLPI_USER_TOKEN=

HELPDESK_ZABBIX_BASE_URL=https://zabbix.empresa.local/api_jsonrpc.php
HELPDESK_ZABBIX_API_TOKEN=

HELPDESK_WHATSAPP_DELIVERY_PROVIDER=evolution
HELPDESK_EVOLUTION_BASE_URL=http://evolution-interna:8080
HELPDESK_EVOLUTION_API_KEY=
HELPDESK_EVOLUTION_INSTANCE_NAME=helpdeskAutomacao
HELPDESK_EVOLUTION_WEBHOOK_SECRET=

HELPDESK_LLM_ENABLED=false
```

Recomendacao de risco para producao inicial:

- entrar primeiro sem IA;
- ativar `HELPDESK_LLM_ENABLED=true` apenas depois de estabilizar o piloto.

## Passo 6: Criar processo permanente e proxy reverso

Suba o backend como servico do sistema e publique via proxy reverso. O backend deve continuar em `127.0.0.1`.

Exemplo de `systemd`:

```ini
[Unit]
Description=Helpdesk Orchestrator Backend
After=network.target

[Service]
User=helpdesk
Group=helpdesk
WorkingDirectory=/opt/helpdesk-orchestrator/backend
EnvironmentFile=/opt/helpdesk-orchestrator/backend/.env
ExecStart=/opt/helpdesk-orchestrator/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Exemplo de publicacao via `Nginx`:

```nginx
server {
    listen 443 ssl http2;
    server_name bot.empresa.local;

    ssl_certificate /etc/letsencrypt/live/bot.empresa.local/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.empresa.local/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:18001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Passo 7: Validar conectividade antes do webhook

Antes de apontar o canal de mensagens para producao, valide o backend isoladamente:

```bash
curl -f https://bot.empresa.local/health
curl -f https://bot.empresa.local/api/v1/helpdesk/identities/5521997775269
```

Tambem valide com a equipe da empresa:

- abertura de ticket via endpoint;
- consulta de ticket ja existente;
- acesso do backend ao `GLPI` e `Zabbix` sem timeout;
- log do backend sem erro de autenticacao.

## Passo 8: Configurar o provedor de WhatsApp

### Se a empresa usar Evolution

Use o apoio operacional do repositorio:

```bash
cd /home/ricardo/Script_Linux_Debian/infra/evolution
cp .env.example .env
./configure_webhook.sh https://bot.empresa.local/api/v1/webhooks/whatsapp/evolution
```

### Se a empresa usar Meta

Configure o webhook da Meta para:

```text
https://bot.empresa.local/api/v1/webhooks/whatsapp/meta
```

E mantenha em producao:

```env
HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=true
```

## Passo 9: Fazer piloto controlado

Nao abra para toda a empresa de imediato. Comece com um grupo pequeno.

Piloto recomendado:

- 5 a 20 usuarios finais;
- 1 ou 2 tecnicos;
- 1 supervisor;
- chamados reais, mas com janela de suporte acompanhada.

Durante o piloto, acompanhe:

- erros de identificacao de telefone;
- tickets abertos com categoria errada;
- comentarios do tecnico nao entregues ao solicitante;
- casos em que o usuario nao deveria fechar um ticket e tentou fechar;
- tickets correlacionados incorretamente com o `Zabbix`.

## Passo 10: Liberar producao gradual

Depois que o piloto estabilizar, amplie em ondas:

1. primeira equipe ou unidade;
2. um grupo maior de tecnicos;
3. liberacao total.

Entre uma onda e outra, revise:

- qualidade do cadastro do GLPI;
- taxa de erros do backend;
- latencia de resposta no canal de mensagens;
- aderencia do processo operacional dos tecnicos.

## Passo 11: Plano de rollback especifico para este cenario

Como `GLPI` e `Zabbix` ja existem e continuam oficiais, o rollback do backend e simples:

1. retire o webhook do provedor de WhatsApp;
2. remova a publicacao do backend do proxy reverso;
3. preserve `GLPI` e `Zabbix` sem alteracao;
4. corrija o `.env` ou reverta a versao do backend;
5. repita os testes de conectividade antes de religar o canal.

## Resultado esperado

Ao fim desse roteiro, a empresa deve conseguir:

- adicionar WhatsApp ao fluxo de atendimento sem reinstalar `GLPI` ou `Zabbix`;
- manter `GLPI` e `Zabbix` como plataformas oficiais;
- introduzir o backend com baixo impacto na operacao atual;
- preparar o ambiente para evoluir depois para IA e automacoes homologadas.
