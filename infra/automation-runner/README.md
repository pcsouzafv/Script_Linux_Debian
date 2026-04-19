# Automation Runner Infra

## Escopo

Esta pasta deve concentrar a camada de execucao segura das automacoes operacionais.

O repositorio ainda nao fixou a implementacao final, mas este bloco existe para separar claramente:

- orquestracao conversacional;
- execucao tecnica;
- controle de aprovacao;
- auditoria de automacao.

## Opcoes previstas

- Ansible direto;
- AWX;
- Rundeck.

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
