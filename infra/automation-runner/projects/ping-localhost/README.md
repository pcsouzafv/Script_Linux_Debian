# Ping Localhost Runner

Projeto homologado minimo para validar a camada de Ansible Runner sem efeito colateral.

## Estrutura

- `inventory/hosts.yml`: limita o alvo a `localhost` com conexao local.
- `project/ping_localhost.yml`: executa `ansible.builtin.ping`.

## Objetivo

- provar o encaixe entre `job_request`, worker e Ansible Runner;
- manter uma automacao de baixissimo risco para smoke test operacional;
- servir de modelo para os proximos projetos homologados.