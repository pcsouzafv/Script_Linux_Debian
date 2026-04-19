# Guia Passo a Passo de Implantacao em Empresa

## Objetivo

Este guia descreve como implantar a solucao deste repositorio em um ambiente corporativo, saindo de homologacao e chegando a um go-live controlado. O foco atual do projeto e colocar em operacao estes blocos:

- GLPI como plataforma oficial de chamados e identidade.
- Zabbix como fonte oficial de eventos e monitoracao.
- Backend FastAPI como camada de orquestracao entre GLPI, Zabbix, WhatsApp e IA.
- Canal de mensagens por Evolution API ou Meta WhatsApp Cloud API.
- Camada de IA opcional, com `ollama` como padrao do projeto.

Documentos complementares:

- ambiente com `GLPI` e `Zabbix` ja existentes: [docs/implantacao-ambiente-existente.md](/home/ricardo/Script_Linux_Debian/docs/implantacao-ambiente-existente.md)
- checklist executivo de liberacao: [docs/checklist-go-live.md](/home/ricardo/Script_Linux_Debian/docs/checklist-go-live.md)

## Quando usar este guia

Use este documento quando a empresa quiser implantar o MVP atual com controle de risco. Ele cobre implantacao nova e tambem o caso em que a empresa ja possui `GLPI` e `Zabbix` em producao.

Se a empresa ja tiver `GLPI` e `Zabbix`, nao execute o instalador completo do host. Reaproveite os servicos existentes e comece a partir do passo de integracao do backend.

## Visao de implantacao

No estado atual do repositorio, a abordagem mais segura e separar a implantacao em quatro trilhas:

1. Homologacao em laboratorio isolado.
2. Preparacao do ambiente corporativo.
3. Publicacao do backend e das integracoes.
4. Validacao assistida e entrada em producao.

## Topologia recomendada para empresa

Para o MVP, a topologia minima recomendada e esta:

- `Host 1`: GLPI e Zabbix.
- `Host 2`: backend FastAPI e proxy reverso.
- `Servico externo`: Evolution API ja existente ou Meta WhatsApp Cloud API.
- `Host opcional`: Ollama, se a empresa quiser IA local separada do backend.

O script [install_debian12_full_stack.sh](/home/ricardo/Script_Linux_Debian/install_debian12_full_stack.sh) instala `GLPI` e `Zabbix` no mesmo host. Ele deve ser usado apenas em host dedicado Debian 12, com as portas obrigatorias livres.

## Passo 1: Definir escopo, responsaveis e janelas

Antes de instalar qualquer componente, feche estas decisoes com a empresa:

- qual ambiente sera usado para homologacao e qual sera usado para producao;
- qual numero de WhatsApp sera exposto aos usuarios;
- quais equipes vao operar `GLPI`, `Zabbix`, backend e webhook;
- quais perfis do GLPI representam `user`, `technician`, `supervisor` e `admin`;
- quais FQDNs serao publicados, por exemplo `glpi.empresa.local`, `zabbix.empresa.local` e `bot.empresa.local`;
- qual sera a janela de implantacao e o plano de rollback.

Criterio de saida deste passo:

- topologia aprovada;
- responsaveis nomeados;
- DNS e certificados definidos;
- firewall e acessos de rede aprovados.

## Passo 2: Homologar primeiro no laboratorio do repositorio

Antes de tocar producao, valide o fluxo no laboratorio isolado do proprio projeto.

Suba o laboratorio:

```bash
cd /home/ricardo/Script_Linux_Debian/infra/helpdesk-lab
cp .env.example .env
./scripts/prepare.sh
./scripts/preflight.sh
./scripts/pull.sh full
./scripts/up.sh full
./scripts/seed-test-data.sh
```

Depois suba o backend:

```bash
cd /home/ricardo/Script_Linux_Debian/backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
./run_dev.sh --dry-run
./run_dev.sh
```

Valide estes pontos em homologacao:

- acesso ao GLPI em `http://127.0.0.1:8088`;
- acesso ao Zabbix em `http://127.0.0.1:8089`;
- resposta do backend em `GET /health`;
- abertura de ticket pelo endpoint;
- resolucao de identidade por telefone;
- consulta de ticket;
- fluxo de comentario do atendente para o solicitante.

Criterio de saida deste passo:

- equipe validou o fluxo ponta a ponta em homologacao;
- categorias, perfis e papeis do GLPI estao claros;
- roteiro de producao foi revisado sem improviso.

## Passo 3: Preparar o ambiente corporativo

No ambiente da empresa, confirme estes pre-requisitos antes da instalacao:

- `Debian 12` para o host que usara o instalador completo;
- acesso de saida do backend para `GLPI`, `Zabbix`, `Evolution` ou `Meta`;
- certificados TLS para o dominio publico do backend;
- backup do banco do GLPI e do Zabbix, se a empresa ja possuir esses sistemas;
- politica de segredos definida fora do Git;
- usuarios de servico criados para `GLPI`, `Zabbix` e provedor de WhatsApp.

Se `GLPI` e `Zabbix` ja existirem em producao, pule o passo 4 e siga para o passo 5.

## Passo 4: Instalar a base GLPI + Zabbix em host dedicado

Se a empresa ainda nao possui `GLPI` e `Zabbix`, use o instalador do repositorio em um host dedicado.

No servidor Debian 12:

```bash
sudo bash /CAMINHO/DO/REPOSITORIO/install_debian12_full_stack.sh
```

O instalador faz estas acoes principais:

- atualiza o sistema;
- instala Apache, PHP e MariaDB;
- instala Docker e Kubernetes;
- instala Zabbix Server, frontend e agent;
- instala GLPI;
- cria as bases de dados;
- grava as senhas geradas em `/root/credentials.txt`.

Depois da execucao, finalize o setup web:

- GLPI em `http://SEU_HOST/glpi`;
- Zabbix em `http://SEU_HOST/zabbix`.

Ao terminar o setup inicial, faca imediatamente:

- troca das senhas padrao e administrativas;
- revisao das permissoes no GLPI;
- revisao do usuario `Admin` do Zabbix;
- configuracao de backup da base e da aplicacao;
- revisao das regras de firewall.

Criterio de saida deste passo:

- GLPI operacional;
- Zabbix operacional;
- credenciais iniciais trocadas;
- backup e acesso administrativo controlados.

## Passo 5: Configurar o GLPI para o backend

O backend depende do GLPI como fonte de identidade e tickets. Configure o GLPI com estas regras:

1. Habilite a API REST.
2. Crie um `app_token` e um `user_token`, ou uma conta de servico com `username/password`.
3. Garanta que os usuarios tenham telefone preenchido em `phone`, `phone2` ou `mobile`.
4. Defina os perfis que serao usados para `Self-Service`, `Technician`, `Supervisor` e `Admin`.
5. Revise categorias, grupos de atendimento e entidades antes do go-live.

As variaveis do backend que refletem essa configuracao sao estas:

```env
HELPDESK_IDENTITY_PROVIDER=glpi
HELPDESK_IDENTITY_GLPI_USER_PROFILES=Self-Service
HELPDESK_IDENTITY_GLPI_TECHNICIAN_PROFILES=Technician
HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES=Supervisor
HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES=Super-Admin,Admin,Administrator
```

Criterio de saida deste passo:

- API do GLPI habilitada e autenticavel;
- usuarios com telefones corporativos cadastrados;
- perfis alinhados ao modelo operacional do WhatsApp.

## Passo 6: Configurar o Zabbix para correlacao

No Zabbix, deixe pronta a integracao usada pelo backend:

1. Crie um token de API ou uma conta de servico.
2. Confirme a URL da API JSON-RPC.
3. Organize hosts, grupos e triggers que serao correlacionados com tickets.
4. Revise nomes de ativos para manter coerencia com o inventario do GLPI.

O endpoint normalmente usado pelo backend e semelhante a este:

```text
https://zabbix.empresa.local/api_jsonrpc.php
```

Criterio de saida deste passo:

- token ou credencial de servico gerado;
- API do Zabbix alcancavel pelo backend;
- ativos e alertas coerentes para correlacao.

## Passo 7: Publicar o backend em producao

Publique o repositorio em um host ou VM separada do GLPI, sempre que possivel.

Exemplo de preparo do runtime:

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

Preencha o `.env` com os valores corporativos. Exemplo minimo:

```env
HELPDESK_ENVIRONMENT=production
HELPDESK_API_HOST=127.0.0.1
HELPDESK_API_PORT=18001
HELPDESK_API_PORT_MAX=18001
HELPDESK_API_PORT_STRICT=true
HELPDESK_IDENTITY_PROVIDER=glpi

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

HELPDESK_LLM_ENABLED=true
HELPDESK_LLM_PROVIDER=ollama
HELPDESK_LLM_BASE_URL=http://ollama-interno:11434
HELPDESK_LLM_MODEL=llama3.1
```

Se a empresa quiser entrar em producao sem IA no primeiro momento, use:

```env
HELPDESK_LLM_ENABLED=false
```

## Passo 8: Criar o processo permanente do backend

O repositorio ainda nao entrega unit file pronta, entao a implantacao corporativa deve criar um processo permanente. O caminho mais simples e usar `systemd`.

Exemplo de service unit:

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

Depois habilite o servico:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now helpdesk-backend.service
sudo systemctl status helpdesk-backend.service
```

Criterio de saida deste passo:

- backend sobe automaticamente no boot;
- processo tem restart automatico;
- logs ficam disponiveis via `journalctl`.

## Passo 9: Publicar o backend por proxy reverso

Mantenha o backend escutando em `127.0.0.1` e publique externamente por `Nginx` ou proxy equivalente.

Exemplo minimo com `Nginx`:

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

Validacoes minimas apos publicar:

```bash
curl -f https://bot.empresa.local/health
curl -f https://bot.empresa.local/api/v1/helpdesk/ai/status
```

## Passo 10: Configurar o canal WhatsApp

Escolha um destes caminhos.

### Caminho A: Evolution API

Use este caminho se a empresa ja opera uma `Evolution API` interna.

```bash
cd /home/ricardo/Script_Linux_Debian/infra/evolution
cp .env.example .env
./create_instance.sh
./configure_webhook.sh https://bot.empresa.local/api/v1/webhooks/whatsapp/evolution
```

Confirme no backend estas variaveis:

```env
HELPDESK_WHATSAPP_DELIVERY_PROVIDER=evolution
HELPDESK_EVOLUTION_BASE_URL=http://evolution-interna:8080
HELPDESK_EVOLUTION_API_KEY=
HELPDESK_EVOLUTION_INSTANCE_NAME=helpdeskAutomacao
HELPDESK_EVOLUTION_WEBHOOK_SECRET=
```

Se a Evolution rodar em container e o backend no host, a URL do webhook precisa ser alcancavel de dentro do container da Evolution.

### Caminho B: Meta WhatsApp Cloud API

Use este caminho quando a empresa operar diretamente com a API oficial da Meta.

Configure no backend:

```env
HELPDESK_WHATSAPP_DELIVERY_PROVIDER=meta
HELPDESK_WHATSAPP_VERIFY_TOKEN=
HELPDESK_WHATSAPP_VALIDATE_SIGNATURE=true
HELPDESK_WHATSAPP_ACCESS_TOKEN=
HELPDESK_WHATSAPP_PHONE_NUMBER_ID=
HELPDESK_WHATSAPP_APP_SECRET=
HELPDESK_WHATSAPP_PUBLIC_NUMBER=
```

Depois configure o webhook da Meta apontando para:

```text
https://bot.empresa.local/api/v1/webhooks/whatsapp/meta
```

Criterio de saida deste passo:

- mensagens entram no backend;
- respostas saem pelo mesmo provedor;
- validacao de assinatura ou segredo esta ativa em producao.

## Passo 11: Ativar a camada de IA, se necessario

Se a empresa quiser usar a camada de IA desde o primeiro dia, deixe o provider pronto antes do go-live.

Com `ollama`, a estrategia recomendada e usar um host proprio ou uma VM separada quando houver carga relevante. Depois ajuste o `.env` do backend e valide:

```bash
curl -f https://bot.empresa.local/api/v1/helpdesk/ai/status
curl -X POST https://bot.empresa.local/api/v1/helpdesk/ai/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Explique em uma frase o objetivo do backend.",
    "system_prompt": "Seja objetivo.",
    "max_tokens": 80,
    "temperature": 0.2
  }'
```

Se a empresa preferir reduzir risco no go-live, deixe a IA desabilitada e ative depois da operacao estabilizar.

## Passo 12: Executar a validacao integrada

Antes de liberar para usuarios reais, rode uma bateria minima de validacao:

1. `GET /health` responde com sucesso.
2. Consulta de identidade resolve um telefone existente no GLPI.
3. Usuario abre chamado pelo canal de mensagens.
4. Tecnico ou supervisor comenta o ticket e o solicitante recebe a interacao.
5. Usuario solicita fechamento de chamado e recebe a lista correta dos seus tickets abertos.
6. Correlacao com Zabbix retorna dados do ativo esperado.

Exemplo de verificacao basica do backend:

```bash
curl -f https://bot.empresa.local/health
curl -f https://bot.empresa.local/api/v1/helpdesk/identities/5521997775269
```

Criterio de saida deste passo:

- fluxo de usuario validado;
- fluxo operacional do tecnico validado;
- notificacoes de retorno funcionando;
- logs e auditoria suficientes para suporte inicial.

## Passo 13: Executar o go-live

No dia da entrada em producao, siga esta ordem:

1. Congele mudancas de configuracao paralelas.
2. Confirme backup e ponto de retorno de `GLPI`, `Zabbix` e backend.
3. Reinicie o backend com o `.env` final.
4. Valide `health`, webhook e envio de resposta.
5. Libere primeiro um grupo piloto.
6. Acompanhe o uso por algumas horas antes de abrir para toda a empresa.

Checklist de go-live:

- `DNS` e `TLS` validos;
- numero oficial do bot conectado;
- usuarios com telefone correto no GLPI;
- perfis tecnicos revisados;
- equipe de suporte com janela de acompanhamento;
- plano de rollback pronto.

## Passo 14: Operacao assistida nos primeiros dias

Nos primeiros dias de producao, acompanhe pelo menos estes pontos:

- erros 4xx e 5xx do backend;
- falhas de autenticacao com GLPI e Zabbix;
- falhas de entrega pelo provedor de WhatsApp;
- latencia da camada de IA, se estiver ligada;
- divergencia entre telefone do WhatsApp e cadastro do GLPI;
- comportamento dos comandos operacionais usados por tecnicos.

Tambem e recomendavel revisar diariamente:

- tickets abertos por automacao;
- comentarios enviados a usuarios;
- tickets fechados por autoatendimento;
- incidentes correlacionados com Zabbix.

## Passo 15: Plano de rollback

Se o go-live apresentar instabilidade, use um rollback simples e controlado:

1. Suspenda o webhook externo do provedor de WhatsApp.
2. Retire o backend do proxy reverso ou aponte para pagina de manutencao.
3. Preserve `GLPI` e `Zabbix` operando normalmente.
4. Corrija o `.env` ou reverta a versao do backend.
5. Refaca a validacao minima antes de religar o webhook.

Como `GLPI` e `Zabbix` seguem como sistemas oficiais, o rollback do backend nao deve comprometer o registro do atendimento ja existente nesses sistemas.

## Resultado esperado

Ao concluir este roteiro, a empresa deve ter:

- um canal de atendimento corporativo por WhatsApp;
- abertura e atualizacao de chamados no GLPI por backend centralizado;
- correlacao basica com Zabbix;
- fluxo operacional por papel;
- base pronta para evoluir depois para automacoes homologadas e observabilidade mais forte.
