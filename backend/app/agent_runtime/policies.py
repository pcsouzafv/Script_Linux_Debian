from __future__ import annotations


def build_shadow_read_only_policy(
    *,
    priority: str | None,
    ticket_status: str | None,
    correlated_event_count: int,
    candidate_automations: list[str],
) -> dict[str, object]:
    normalized_priority = (priority or "").strip().lower()
    normalized_status = (ticket_status or "").strip().lower()

    notes = [
        "O runtime opera apenas em shadow mode nesta fase e nao executa automacoes.",
        "Qualquer acao com efeito colateral continua exigindo aprovacao humana explicita.",
    ]

    if correlated_event_count > 0:
        rationale = (
            f"Foram encontrados {correlated_event_count} evento(s) correlacionado(s); "
            "o agente pode reunir evidencias, mas nao fechar, reconhecer nem alterar alertas."
        )
    elif normalized_status in {"new", "processing", "pending"}:
        rationale = (
            "O chamado ainda esta em andamento e a investigacao pode enriquecer o contexto "
            "antes de qualquer acao operacional."
        )
    else:
        rationale = (
            "A investigacao ficou restrita a leitura porque ainda estamos na fase inicial "
            "de implantacao do runtime LangGraph."
        )

    if normalized_priority in {"high", "critical"}:
        notes.append(
            "Chamados de alta prioridade recebem apenas recomendacoes; execucao automatica segue bloqueada."
        )

    if candidate_automations:
        notes.append(
            "Automacoes candidatas foram apenas sugeridas a partir do catalogo homologado."
        )

    return {
        "mode": "shadow-read-only",
        "can_read_data": True,
        "can_execute_write_actions": False,
        "approval_required_for_write": True,
        "rationale": rationale,
        "notes": notes,
    }
