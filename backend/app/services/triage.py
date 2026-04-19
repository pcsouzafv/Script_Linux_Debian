from app.schemas.helpdesk import TicketPriority, TicketTriageRequest, TicketTriageResponse
from app.services.exceptions import IntegrationError
from app.services.llm import LLMClient


ACCESS_CATEGORIES = {"acesso", "identidade", "senha"}
INFRA_CATEGORIES = {"infra", "rede", "servidor"}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "senha": ("senha", "reset", "expirada", "expirou", "bloqueada"),
    "identidade": (
        "permiss",
        "perfil",
        "grupo",
        "mfa",
        "duplo fator",
        "identidade",
    ),
    "acesso": (
        "acesso",
        "login",
        "autentic",
        "credencial",
        "usuario sem acesso",
        "usuário sem acesso",
    ),
    "rede": (
        "rede",
        "vpn",
        "dns",
        "internet",
        "wifi",
        "wi-fi",
        "latencia",
        "latência",
        "conect",
        "link",
        "firewall",
    ),
    "servidor": (
        "servidor",
        "host",
        "vm",
        "maquina",
        "máquina",
        "node",
    ),
    "infra": (
        "cpu",
        "memoria",
        "memória",
        "disco",
        "container",
        "docker",
        "apache",
        "nginx",
        "zabbix",
        "mysql",
        "postgres",
        "banco",
        "servico",
        "serviço",
        "api",
    ),
}

CRITICAL_PRIORITY_KEYWORDS = (
    "fora do ar",
    "produção parada",
    "producao parada",
    "todos os usuarios",
    "todos os usuários",
    "indisponivel total",
    "indisponível total",
    "sem resposta total",
)

HIGH_PRIORITY_KEYWORDS = (
    "indisponivel",
    "indisponível",
    "nao responde",
    "não responde",
    "sem acesso",
    "erro de autentic",
    "intermitente",
    "urgente",
    "falha",
)


def resolve_helpdesk_queue(category: str | None, priority: TicketPriority) -> str:
    normalized_category = (category or "").strip().lower()
    if priority is TicketPriority.CRITICAL:
        return "NOC-Critico"
    if normalized_category in ACCESS_CATEGORIES:
        return "ServiceDesk-Acessos"
    if normalized_category in INFRA_CATEGORIES:
        return "Infraestrutura-N1"
    return "ServiceDesk-N1"


class TriageAgent:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def triage(self, payload: TicketTriageRequest) -> TicketTriageResponse:
        text = self._build_analysis_text(payload)
        suggested_category, category_score = self._suggest_category(text)
        suggested_priority = self._suggest_priority(text, payload.current_priority)
        resolved_category = payload.current_category or suggested_category
        resolved_priority = payload.current_priority or suggested_priority
        summary = self._build_summary(payload, resolved_category, resolved_priority)
        next_steps = self._build_next_steps(resolved_category)
        confidence = self._estimate_confidence(
            text=text,
            category_score=category_score,
            payload=payload,
        )
        notes = self._build_notes(payload, suggested_category, suggested_priority)
        mode = "rules"

        llm_summary, llm_steps, llm_notes = await self._try_llm_assist(
            payload=payload,
            suggested_category=suggested_category,
            suggested_priority=suggested_priority,
        )
        notes.extend(llm_notes)
        if llm_summary or llm_steps:
            summary = llm_summary or summary
            next_steps = llm_steps or next_steps
            mode = "hybrid"

        return TicketTriageResponse(
            current_category=payload.current_category,
            current_priority=payload.current_priority,
            suggested_category=suggested_category,
            suggested_priority=suggested_priority,
            resolved_category=resolved_category,
            resolved_priority=resolved_priority,
            suggested_queue=resolve_helpdesk_queue(resolved_category, resolved_priority),
            confidence=confidence,
            summary=summary,
            next_steps=next_steps,
            mode=mode,
            notes=notes,
        )

    def _build_analysis_text(self, payload: TicketTriageRequest) -> str:
        return "\n".join(
            value
            for value in (
                payload.subject,
                payload.description,
                payload.asset_name,
                payload.service_name,
                payload.current_category,
            )
            if value
        ).lower()

    def _suggest_category(self, text: str) -> tuple[str | None, int]:
        scores: dict[str, int] = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score > 0:
                scores[category] = score

        if not scores:
            return None, 0

        ranked_categories = sorted(
            scores.items(),
            key=lambda item: (item[1], item[0] in {"acesso", "rede", "infra"}),
            reverse=True,
        )
        return ranked_categories[0][0], ranked_categories[0][1]

    def _suggest_priority(
        self,
        text: str,
        current_priority: TicketPriority | None,
    ) -> TicketPriority:
        suggested = TicketPriority.MEDIUM
        if any(keyword in text for keyword in CRITICAL_PRIORITY_KEYWORDS):
            suggested = TicketPriority.CRITICAL
        elif any(keyword in text for keyword in HIGH_PRIORITY_KEYWORDS):
            suggested = TicketPriority.HIGH

        if current_priority is None:
            return suggested

        priority_order = {
            TicketPriority.LOW: 1,
            TicketPriority.MEDIUM: 2,
            TicketPriority.HIGH: 3,
            TicketPriority.CRITICAL: 4,
        }
        if priority_order[current_priority] >= priority_order[suggested]:
            return current_priority
        return suggested

    def _build_summary(
        self,
        payload: TicketTriageRequest,
        category: str | None,
        priority: TicketPriority,
    ) -> str:
        focus = payload.service_name or payload.asset_name or "serviço não identificado"
        base_summary = self._first_sentence(payload.description) or payload.subject.strip()
        normalized_category = category or "geral"
        return (
            f"Triagem inicial indica incidente de {normalized_category} com prioridade "
            f"{priority.value} relacionado a {focus}. Resumo: {base_summary}"
        )

    def _build_next_steps(self, category: str | None) -> list[str]:
        normalized_category = (category or "").strip().lower()
        if normalized_category in ACCESS_CATEGORIES:
            return [
                "Confirmar usuario afetado, horario de inicio e escopo do bloqueio.",
                "Validar autenticacao, credenciais e vinculo de perfil no sistema alvo.",
                "Anexar evidencias de erro e encaminhar para ServiceDesk-Acessos se persistir.",
            ]
        if normalized_category == "rede":
            return [
                "Validar conectividade basica, DNS, VPN e alcance do ativo ou servico.",
                "Cruzar o incidente com alertas do Zabbix para delimitar escopo e impacto.",
                "Registrar horario, localidade e usuarios afetados antes de escalar.",
            ]
        if normalized_category in {"infra", "servidor"}:
            return [
                "Verificar disponibilidade do host e alertas recentes no Zabbix.",
                "Coletar sinais basicos de CPU, memoria, disco e logs do servico afetado.",
                "Direcionar para Infraestrutura-N1 com o ativo e impacto ja resumidos.",
            ]
        return [
            "Confirmar impacto, urgencia e ativo ou servico relacionado.",
            "Coletar evidencias minimas para evitar retriagem manual.",
            "Encaminhar para ServiceDesk-N1 para classificacao assistida.",
        ]

    def _estimate_confidence(
        self,
        text: str,
        category_score: int,
        payload: TicketTriageRequest,
    ) -> str:
        if category_score >= 2 and (payload.asset_name or payload.service_name):
            return "high"
        if category_score >= 1 or any(keyword in text for keyword in HIGH_PRIORITY_KEYWORDS):
            return "medium"
        return "low"

    def _build_notes(
        self,
        payload: TicketTriageRequest,
        suggested_category: str | None,
        suggested_priority: TicketPriority,
    ) -> list[str]:
        notes: list[str] = ["Triagem executada com heuristicas locais seguras."]
        if payload.current_category:
            notes.append(f"Categoria informada foi preservada: {payload.current_category}.")
        elif suggested_category:
            notes.append(f"Categoria sugerida automaticamente: {suggested_category}.")

        if payload.current_priority is not None:
            notes.append(f"Prioridade informada foi preservada: {payload.current_priority.value}.")
        else:
            notes.append(f"Prioridade sugerida automaticamente: {suggested_priority.value}.")
        return notes

    async def _try_llm_assist(
        self,
        payload: TicketTriageRequest,
        suggested_category: str | None,
        suggested_priority: TicketPriority,
    ) -> tuple[str | None, list[str], list[str]]:
        status = self.llm_client.get_status()
        if status.status != "configured":
            return None, [], ["Camada LLM indisponivel para enriquecer a triagem; mantendo modo por regras."]

        prompt = (
            "Voce e um agente de triagem de helpdesk. "
            "Responda com no maximo 4 linhas. "
            "A primeira linha deve comecar com 'resumo:'. "
            "As proximas linhas devem comecar com 'passo:'. "
            "Nao proponha execucao automatica, shell, SSH ou mudancas destrutivas.\n\n"
            f"Assunto: {payload.subject}\n"
            f"Descricao: {payload.description}\n"
            f"Ativo: {payload.asset_name or 'n/a'}\n"
            f"Servico: {payload.service_name or 'n/a'}\n"
            f"Categoria sugerida: {suggested_category or 'n/a'}\n"
            f"Prioridade sugerida: {suggested_priority.value}"
        )

        try:
            result = await self.llm_client.generate_text(
                user_prompt=prompt,
                system_prompt=(
                    "Voce faz apenas triagem segura para helpdesk. "
                    "Resuma o incidente e proponha proximos passos de baixo risco."
                ),
                max_tokens=220,
                temperature=0.1,
            )
        except IntegrationError as exc:
            return None, [], [f"Falha ao enriquecer triagem com LLM: {exc}"]

        summary: str | None = None
        next_steps: list[str] = []
        for raw_line in result.content.splitlines():
            line = raw_line.strip()
            lowered = line.lower()
            if lowered.startswith("resumo:") and summary is None:
                summary = line.split(":", maxsplit=1)[1].strip()
            elif lowered.startswith("passo:"):
                step = line.split(":", maxsplit=1)[1].strip()
                if step:
                    next_steps.append(step)

        if not summary and not next_steps:
            return None, [], ["LLM retornou formato fora do contrato de triagem; usando heuristicas locais."]

        return summary, next_steps[:3], [f"Triagem enriquecida pelo provider {result.provider}."]

    def _first_sentence(self, text: str) -> str:
        sanitized = " ".join(text.split())
        if not sanitized:
            return ""
        for separator in (". ", "! ", "? "):
            if separator in sanitized:
                return sanitized.split(separator, maxsplit=1)[0].strip()
        return sanitized[:180].strip()