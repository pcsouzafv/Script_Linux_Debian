# Helpdesk Lab

Laboratorio isolado para subir `GLPI + Zabbix` em `Docker Compose` sem tocar nos containers, imagens, volumes e portas dos servicos ja existentes no host.

## Objetivo

Fornecer um ambiente de laboratorio para a plataforma de helpdesk com estas regras:

- nao mexer nos containers atuais do host;
- nao reutilizar portas de servicos ja em producao;
- nao exigir limpeza de imagens existentes;
- permitir subir apenas `GLPI`, apenas `Zabbix` ou o conjunto completo.

## Escopo

Este laboratorio sobe:

- `db`: MySQL compartilhado apenas pelo laboratorio;
- `glpi`: interface e aplicacao do GLPI;
- `zabbix-server`: backend do Zabbix;
- `zabbix-web`: frontend web do Zabbix.

Tudo fica isolado em:

- rede Docker `helpdesk_lab`;
- volumes `helpdesk_lab_*`;
- portas locais `127.0.0.1:8088` e `127.0.0.1:8089` por padrao.

## Nao toca nos servicos atuais

Este laboratorio foi desenhado para nao interferir nos containers informados como protegidos, incluindo:

- `idiomasbr-*`
- `shadowing-*`
- `portainer`
- `kubementor-academy`
- `evolution-*`

Tambem evita as portas ja ocupadas por esses servicos.

## Estrutura

```text
infra/helpdesk-lab/
├── .env.example
├── .gitignore
├── compose.yaml
├── README.md
├── scripts/
│   ├── bootstrap-integrations.sh
│   ├── common.sh
│   ├── down.sh
│   ├── preflight.sh
│   ├── prepare.sh
│   ├── pull.sh
│   ├── seed-glpi.sh
│   ├── seed-test-data.sh
│   ├── seed-zabbix.sh
│   ├── seed-zabbix-runtime.sh
│   └── up.sh
└── templates/
    └── initdb/
        └── 01-bootstrap.sql.template
```

## Perfis

- `glpi`: sobe `db + glpi`
- `zabbix`: sobe `db + zabbix-server + zabbix-web`
- `full`: sobe tudo

## Fluxo recomendado

1. Copiar `.env.example` para `.env`.
2. Ajustar senhas e portas se quiser.
3. Rodar o preflight.
4. Fazer `pull` apenas do perfil que vai usar.
5. Subir o perfil escolhido.

## Comandos

Preparar arquivos locais do laboratorio:

```bash
cd /home/ricardo/Script_Linux_Debian/infra/helpdesk-lab
./scripts/prepare.sh
```

Validar portas e ambiente:

```bash
./scripts/preflight.sh
```

Baixar imagens de um perfil especifico:

```bash
./scripts/pull.sh glpi
./scripts/pull.sh zabbix
./scripts/pull.sh full
```

Subir o laboratorio:

```bash
./scripts/up.sh glpi
./scripts/up.sh zabbix
./scripts/up.sh full
```

Bootstrap das integracoes do laboratorio:

```bash
./scripts/bootstrap-integrations.sh
```

Semear usuarios, ativos e chamados de laboratorio no GLPI:

```bash
./scripts/seed-glpi.sh
```

Semear problemas reais no Zabbix para testes de correlacao:

```bash
./scripts/seed-zabbix.sh
```

Mapear o runtime do host no Zabbix com containers Docker, bancos/servicos de dados, maquina local e descoberta LAN:

```bash
./scripts/seed-zabbix-runtime.sh
```

Executar o fluxo completo de bootstrap + seed:

```bash
./scripts/seed-test-data.sh
```

O fluxo completo agora:

- habilita a API do GLPI e valida a autenticacao do Zabbix;
- alinha o `backend/.env` para apontar para o laboratorio;
- cadastra usuarios operacionais e usuarios finais no GLPI;
- grava o telefone diretamente no usuario do GLPI para validacao do WhatsApp;
- cadastra ativos como `erp-web-01`, `vpn-edge-01`, `auth-01` e `printer-matriz-01`;
- cria tickets de exemplo vinculados a solicitantes e ativos;
- reescreve `backend/data/identities.lab.json` com os IDs reais do laboratorio;
- abre problemas no Zabbix alinhados aos mesmos ativos para correlacao.
- cadastra no Zabbix os containers Docker acessiveis pelo host, agrupando bancos/servicos de dados;
- inclui a maquina local com checks de portas publicadas no IP LAN;
- cria ou atualiza uma regra de descoberta `Descoberta LAN local` para a sub-rede atual.

O seed de runtime conecta o `zabbix-server` do laboratorio nas redes Docker dos stacks em execucao apenas para permitir monitoramento por IP interno. Os containers existentes nao sao recriados nem reiniciados.

Parar o laboratorio sem remover dados:

```bash
./scripts/down.sh
```

Parar e remover apenas os volumes do laboratorio:

```bash
./scripts/down.sh --volumes
```

## Links esperados

Com o perfil `glpi` ativo:

- GLPI: `http://127.0.0.1:8088`

Com o perfil `zabbix` ativo:

- Zabbix: `http://127.0.0.1:8089`

## Credenciais iniciais

### GLPI

O banco e inicializado pelo compose. O acesso web inicial segue o fluxo padrao do GLPI.

Para o laboratorio deste repositório, a automacao de bootstrap assume:

- usuario: `glpi`
- senha: `glpi`

### Zabbix

O login inicial padrao do frontend costuma ser:

- usuario: `Admin`
- senha: `zabbix`

Altere isso no primeiro acesso.

## Observacoes de espaco

Como voce pediu para nao tocar nas imagens e volumes atuais, este laboratorio nao executa `prune`, `cleanup` nem remocao automatica.

Por isso, o fluxo foi dividido em `prepare`, `pull` e `up` para voce controlar quando baixar as imagens.

## Referencias oficiais

- GLPI Docker images: <https://github.com/glpi-project/docker-images>
- GLPI REST API V1: <https://help.glpi-project.org/documentation/modules/configuration/general/api/api>
- Zabbix containers: <https://www.zabbix.com/documentation/current/en/manual/installation/containers>
- Zabbix API 7.4: <https://www.zabbix.com/documentation/7.4/en/manual/api>
