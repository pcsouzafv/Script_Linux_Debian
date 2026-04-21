# Automation Runner Infra

## Escopo

Esta pasta deve concentrar a camada de execucao segura das automacoes operacionais.

O repositorio ainda nao fixou a implementacao final, mas este bloco existe para separar claramente:

- orquestracao conversacional;
- execucao tecnica;
- controle de aprovacao;
- auditoria de automacao.

No estado atual do backend, a etapa de aprovacao ja esta isolada por credencial propria, separada do escopo que cria e consulta jobs administrativos.

## Opcoes previstas

- Ansible direto;
- AWX;
- Rundeck.

## Direcao pratica para a proxima fase

O repositório agora já possui um degrau inicial dentro do backend: `job_request` persistido, fila via Redis e worker seguro que executa apenas um catálogo mínimo homologado.

Catalogo inicial atual:

- `ansible.ping_localhost`
- `ansible.ticket_context_probe`
- `noop.healthcheck`
- `glpi.ticket_snapshot`

O degrau atual ja inclui:

- um primeiro projeto homologado em `projects/ping-localhost/`, executado via Ansible Runner e sem shell arbitrario;
- aprovacao automatica apenas para jobs `low-risk` e aprovacao explicita para jobs `moderate` antes do enqueue;
- fila principal para jobs homologados;
- retentativas finitas controladas por `HELPDESK_AUTOMATION_WORKER_MAX_ATTEMPTS`;
- dead-letter dedicado para jobs que excederem o limite;
- persistencia do ultimo erro e da contagem de tentativas no proprio `job_request`;
- bloqueio auditado de qualquer item que apareca na fila sem aprovacao valida.

O proximo incremento pragmatico continua sendo plugar `Ansible Runner` ou `Ansible` controlado por catalogo, consumindo pedidos vindos do backend por fila e gravando auditoria em banco operacional dedicado.

No laboratorio local, a base para isso passa a existir com:

- Redis para fila e retentativas;
- PostgreSQL para auditoria, estado e histórico de execução.

Isto significa que a proxima fase nao precisa reinventar o controle de fila nem o `job_request`; ela precisa apenas trocar o executor interno simples por um runner externo homologado, preservando os guardrails ja definidos.

AWX ou Rundeck continuam opções válidas para a fase seguinte, quando a plataforma já tiver catálogo homologado e necessidade de aprovação multiusuário mais robusta.

## Projetos homologados iniciais

O repositório agora inclui dois `private_data_dir` homologados de Ansible Runner:

- `projects/ping-localhost/`, com:
  - `inventory/hosts.yml` restrito a `localhost`;
  - `project/ping_localhost.yml` usando `ansible.builtin.ping` em conexao local.
- `projects/ticket-context-probe/`, com:
  - `inventory/hosts.yml` tambem restrito a `localhost`;
  - `project/ticket_context_probe.yml` exportando via `ansible.builtin.set_stats` um contexto minimo e sanitizado do ticket recebido do backend.

Esses projetos existem para validar a esteira end-to-end do runner sem introduzir efeito colateral na infraestrutura. O segundo ja prova o padrao de retorno estruturado (`artifact_data`) para jobs vinculados a ticket e atualmente fica sob aprovacao explicita antes de entrar na fila.

## Responsabilidades

- executar somente playbooks homologados;
- respeitar RBAC e aprovacao;
- registrar entrada, saida, operador, horario e resultado;
- permitir rollback quando aplicavel;
- impedir shell arbitrario vindo do modelo.

## Artefatos esperados

- catalogo inicial de automacoes;
- matriz de risco por playbook;
- templates de inventario ou credenciais indiretas;
- fluxos de aprovacao;
- padrao de retorno para o backend.

## Guardrails obrigatorios

- nenhuma execucao livre de comando;
- nenhuma credencial hardcoded;
- toda automacao vinculada a ticket ou contexto operacional;
- toda automacao com dono tecnico definido.
