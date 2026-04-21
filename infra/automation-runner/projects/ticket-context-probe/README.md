# Ticket Context Probe Runner

Projeto homologado read-only para validar jobs ligados a `ticket_id` sem executar shell arbitrario.

## Estrutura

- `inventory/hosts.yml`: limita o alvo a `localhost` com conexao local.
- `project/ticket_context_probe.yml`: recebe o contexto minimo do ticket por extravars e o devolve via `ansible.builtin.set_stats`.

## Objetivo

- provar que o backend consegue vincular uma automacao homologada a um ticket real;
- devolver um artefato estruturado e sanitizado para o `job_request`;
- servir de base para proximos playbooks read-only com contexto operacional.