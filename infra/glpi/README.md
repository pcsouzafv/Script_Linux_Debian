# GLPI Infra

## Escopo

Esta pasta deve concentrar os artefatos de implantacao e operacao do GLPI como sistema oficial de ITSM da plataforma.

## Responsabilidades

- configuracao de banco e aplicacao;
- pos-instalacao e hardening;
- backup e restauracao;
- configuracao de API para integracao com o backend;
- proxy reverso, TLS e politicas de exposicao, se necessario.

## Nao colocar aqui

- regras do backend FastAPI;
- automacoes genericas sem relacao com GLPI;
- segredos reais.

## Artefatos esperados

- `README.md` operacional por ambiente;
- exemplos de parametros ou variaveis;
- scripts ou playbooks de pos-instalacao;
- checklist de habilitacao da API REST;
- rotina de backup.

## Dependencias

- MariaDB funcional;
- GLPI instalado e acessivel;
- usuario tecnico com permissao para gerar tokens de API.

## Integracao com o backend

Antes de ligar o backend em modo real, este bloco precisa entregar:

- URL base da API;
- `app_token`;
- `user_token`;
- validacao de permissao para criar e consultar tickets.

## GLPI pronto para automacao

Para esta solucao, considerar o GLPI "pronto" significa ir alem do login e da API ativa. O ambiente deve ter pelo menos:

- categorias ITIL coerentes com a triagem do backend, como `Acesso`, `Identidade`, `Senha`, `Rede`, `Servidor` e `Infra`;
- grupos de fila para atribuicao operacional, preferencialmente em hierarquia, como `TI > Service Desk > N1`, `TI > Service Desk > Acessos`, `TI > Infraestrutura > N1` e `TI > NOC > Critico`;
- grupos de time para identidade dos usuarios, mantendo nomes simples e estaveis para o backend resolver `team`, como `financeiro`, `recepcao`, `infraestrutura` e `service-desk`;
- localizacoes minimamente estruturadas para analytics e correlacao, como `Matriz`, `Datacenter`, `Recepcao`, `Financeiro` e `NOC`;
- tickets seedados ou historicos com `externalid`, item vinculado, grupo responsavel, followup, task e solution onde fizer sentido;
- usuario tecnico ou conta de servico com permissao para criar, consultar e atualizar tickets, followups, solutions e relacoes como `Item_Ticket` e `Group_Ticket`.

## Alinhamento com o backend

O backend usa fila logica para triagem e grupo real do GLPI para atribuicao. Por isso, a configuracao deve manter os dois lados alinhados:

- fila logica do backend: `ServiceDesk-N1`, `ServiceDesk-Acessos`, `Infraestrutura-N1`, `NOC-Critico`;
- grupo real no GLPI: definido por `HELPDESK_GLPI_QUEUE_GROUP_MAP`;
- identidade do usuario: resolvida a partir do primeiro grupo do usuario no GLPI, por isso grupos de time nao devem ser confundidos com grupos de fila.

Exemplo:

```env
HELPDESK_GLPI_QUEUE_GROUP_MAP={"ServiceDesk-N1":"TI > Service Desk > N1","ServiceDesk-Acessos":"TI > Service Desk > Acessos","Infraestrutura-N1":"TI > Infraestrutura > N1","NOC-Critico":"TI > NOC > Critico"}
```

## LangChain e automacao

Se a proxima etapa for explorar bastante automacao, `LangChain` ou `LangGraph` devem entrar acima do backend e nao no lugar das regras transacionais do GLPI.

Uso recomendado:

- GLPI continua como sistema oficial de tickets, atores, grupos e historico operacional;
- backend continua como camada de permissao, auditoria, fila, correlacao e execucao segura;
- `LangChain` ou `LangGraph` entram na camada de conhecimento, RAG, planejamento de ferramenta e sugestao de proximo passo;
- qualquer acao no GLPI ou em automacoes homologadas deve continuar passando pelas rotas protegidas do backend.
