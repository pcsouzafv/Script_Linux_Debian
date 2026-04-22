from app.schemas.helpdesk import TicketPriority, TicketTriageRequest, TicketTriageResponse, UserRole
from app.services.exceptions import IntegrationError
from app.services.llm import LLMClient
from app.services.ticket_analytics_store import TicketAnalyticsSnapshotRecord, TicketAnalyticsStore


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

CATEGORY_ANALYTICS_LABELS = {
    "acesso": "Acesso",
    "identidade": "Identidade",
    "senha": "Senha",
    "rede": "Rede",
    "servidor": "Servidor",
    "infra": "Infra",
}

RESOLUTION_STATUS_BONUS = {"solved", "closed"}
OPERATIONAL_TEAMS = {"infraestrutura", "plataforma", "operacoes", "operações", "ops"}
SERVICE_DESK_TEAMS = {"service-desk", "service desk", "service_desk", "atendimento"}
INFRA_ROUTING_KEYWORDS = {
    "vpn",
    "dns",
    "firewall",
    "servidor",
    "host",
    "vm",
    "docker",
    "container",
    "kubernetes",
    "cluster",
    "redis",
    "postgres",
    "mysql",
    "banco",
    "api",
    "backend",
    "zabbix",
    "glpi",
    "infra",
    "infraestrutura",
    "plataforma",
    "deploy",
}


def resolve_helpdesk_queue(
    category: str | None,
    priority: TicketPriority,
    *,
    requester_role: UserRole | None = None,
    requester_team: str | None = None,
    service_name: str | None = None,
    asset_name: str | None = None,
    subject: str | None = None,
    description: str | None = None,
) -> str:
    normalized_category = (category or "").strip().lower()
    normalized_team = _normalize_team(requester_team)
    context_text = " ".join(
        value
        for value in (subject, description, service_name, asset_name, normalized_category)
        if value
    ).lower()

    if priority is TicketPriority.CRITICAL:
        return "NOC-Critico"
    if (
        normalized_category in ACCESS_CATEGORIES
        and _is_operational_requester(requester_role, normalized_team)
        and _targets_infrastructure(context_text)
    ):
        return "Infraestrutura-N1"
    if normalized_category in ACCESS_CATEGORIES:
        return "ServiceDesk-Acessos"
    if normalized_category in INFRA_CATEGORIES:
        return "Infraestrutura-N1"
    if _is_operational_requester(requester_role, normalized_team) and _targets_infrastructure(context_text):
        return "Infraestrutura-N1"
    return "ServiceDesk-N1"


def _normalize_team(team: str | None) -> str:
    return (team or "").strip().lower()


def _is_operational_requester(
    requester_role: UserRole | None,
    normalized_team: str,
) -> bool:
    if normalized_team in SERVICE_DESK_TEAMS:
        return False
    if normalized_team in OPERATIONAL_TEAMS:
        return True
    return requester_role in {UserRole.TECHNICIAN, UserRole.ADMIN}


def _targets_infrastructure(context_text: str) -> bool:
    return any(keyword in context_text for keyword in INFRA_ROUTING_KEYWORDS)


class TriageAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        analytics_store: TicketAnalyticsStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.analytics_store = analytics_store

    async def triage(self, payload: TicketTriageRequest) -> TicketTriageResponse:
        text = self._build_analysis_text(payload)
        suggested_category, category_score = self._suggest_category(text)
        suggested_priority = self._suggest_priority(text, payload.current_priority)
        resolved_category = payload.current_category or suggested_category
        resolved_priority = payload.current_priority or suggested_priority
        default_queue = resolve_helpdesk_queue(resolved_category, resolved_priority)
        suggested_queue = resolve_helpdesk_queue(
            resolved_category,
            resolved_priority,
            requester_role=payload.requester_role,
            requester_team=payload.requester_team,
            service_name=payload.service_name,
            asset_name=payload.asset_name,
            subject=payload.subject,
            description=payload.description,
        )
        summary = self._build_summary(payload, resolved_category, resolved_priority)
        next_steps = self._build_next_steps(resolved_category, suggested_queue)
        confidence = self._estimate_confidence(
            text=text,
            category_score=category_score,
            payload=payload,
        )
        notes = self._build_notes(payload, suggested_category, suggested_priority)
        notes.extend(
            self._build_queue_notes(
                payload=payload,
                resolved_category=resolved_category,
                default_queue=default_queue,
                suggested_queue=suggested_queue,
            )
        )
        resolution_hints, similar_incidents, resolution_notes = await self._build_resolution_context(
            payload=payload,
            category=resolved_category,
            priority=resolved_priority,
        )
        notes.extend(resolution_notes)
        mode = "rules"

        llm_summary, llm_steps, llm_notes = await self._try_llm_assist(
            payload=payload,
            suggested_category=suggested_category,
            suggested_priority=suggested_priority,
            suggested_queue=suggested_queue,
            resolution_hints=resolution_hints,
            similar_incidents=similar_incidents,
        )
        notes.extend(llm_notes)
        if llm_summary or llm_steps:
            summary = llm_summary or summary
            next_steps = self._align_next_steps_with_queue(llm_steps or next_steps, suggested_queue)
            mode = "hybrid"

        return TicketTriageResponse(
            current_category=payload.current_category,
            current_priority=payload.current_priority,
            suggested_category=suggested_category,
            suggested_priority=suggested_priority,
            resolved_category=resolved_category,
            resolved_priority=resolved_priority,
            suggested_queue=suggested_queue,
            confidence=confidence,
            summary=summary,
            next_steps=next_steps,
            resolution_hints=resolution_hints,
            similar_incidents=similar_incidents,
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
                payload.requester_team,
                payload.requester_role.value if payload.requester_role is not None else None,
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

    def _build_next_steps(self, category: str | None, queue: str) -> list[str]:
        normalized_category = (category or "").strip().lower()
        if normalized_category in ACCESS_CATEGORIES and queue == "Infraestrutura-N1":
            return [
                "Confirmar se o bloqueio afeta acesso operacional a VPN, bastion, host, API ou ferramenta de infraestrutura.",
                "Validar grupo, perfil tecnico, MFA e credenciais do servico ou ambiente administrativo envolvido.",
                "Direcionar para Infraestrutura-N1 com o contexto operacional do solicitante e o ativo ou servico afetado.",
            ]
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

    def _build_queue_notes(
        self,
        *,
        payload: TicketTriageRequest,
        resolved_category: str | None,
        default_queue: str,
        suggested_queue: str,
    ) -> list[str]:
        if suggested_queue == default_queue:
            return []

        role_label = payload.requester_role.value if payload.requester_role is not None else "unknown"
        team_label = payload.requester_team or "sem-time"
        category_label = resolved_category or "geral"
        return [
            "Fila ajustada pelo contexto do solicitante. "
            f"Categoria {category_label}, papel {role_label} e time {team_label} direcionaram para {suggested_queue}."
        ]

    async def _build_resolution_context(
        self,
        *,
        payload: TicketTriageRequest,
        category: str | None,
        priority: TicketPriority,
    ) -> tuple[list[str], list[str], list[str]]:
        resolution_hints = self._build_resolution_hints(category)
        if self.analytics_store is None or not category:
            return resolution_hints, [], []

        analytics_category = CATEGORY_ANALYTICS_LABELS.get(category.strip().lower())
        if analytics_category is None:
            return resolution_hints, [], []

        try:
            listing = await self.analytics_store.list_snapshots(
                limit=12,
                category_name=analytics_category,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return resolution_hints, [], [
                f"Historico analitico indisponivel para enriquecer a triagem: {exc}"
            ]

        ranked = self._rank_similar_snapshots(
            snapshots=listing.snapshots,
            payload=payload,
            priority=priority,
        )
        top_matches = ranked[:2]
        if not top_matches:
            return resolution_hints, [], []

        history_hint = self._build_history_hint(top_matches[0], payload)
        if history_hint:
            resolution_hints = [history_hint, *resolution_hints]

        deduplicated_hints: list[str] = []
        for hint in resolution_hints:
            if hint and hint not in deduplicated_hints:
                deduplicated_hints.append(hint)

        similar_incidents = [self._format_similar_incident(snapshot) for snapshot in top_matches]
        notes = [
            f"Triagem enriquecida com historico analitico ({listing.storage_mode}) e {len(top_matches)} caso(s) similar(es)."
        ]
        return deduplicated_hints[:3], similar_incidents, notes

    def _build_resolution_hints(self, category: str | None) -> list[str]:
        normalized_category = (category or "").strip().lower()
        if normalized_category == "senha":
            return [
                "Confirme se o bloqueio veio de expiracao, tentativas invalidas ou sincronizacao antes de redefinir a senha.",
                "Valide se o usuario consegue concluir o fluxo de troca sem erro adicional de MFA ou perfil.",
            ]
        if normalized_category in ACCESS_CATEGORIES:
            return [
                "Verifique bloqueio, expiracao, MFA e ultimas mudancas de perfil antes de resetar acesso.",
                "Se o impacto for coletivo, valide autenticacao central ou o servico alvo antes de tratar como caso isolado.",
            ]
        if normalized_category == "rede":
            return [
                "Compare a falha com e sem VPN para separar DNS, rota, firewall e link.",
                "Cruze o horario do erro com alertas ou mudancas recentes antes de reiniciar componentes.",
            ]
        if normalized_category in {"infra", "servidor"}:
            return [
                "Valide saude do servico e consumo de recurso antes de reiniciar componentes.",
                "Se houver degradacao geral, priorize contencao e coleta de evidencias antes de qualquer mudanca.",
            ]
        return [
            "Confirme escopo, horario e ultima mudanca conhecida antes de aplicar qualquer correcao.",
            "Colete evidencias reproduziveis para reduzir retriagem e escalonamento desnecessario.",
        ]

    def _rank_similar_snapshots(
        self,
        *,
        snapshots: list[TicketAnalyticsSnapshotRecord],
        payload: TicketTriageRequest,
        priority: TicketPriority,
    ) -> list[TicketAnalyticsSnapshotRecord]:
        normalized_service = (payload.service_name or "").strip().lower()
        normalized_asset = (payload.asset_name or "").strip().lower()
        normalized_priority = priority.value
        ranked: list[tuple[int, TicketAnalyticsSnapshotRecord]] = []

        for snapshot in snapshots:
            score = 1
            if normalized_service and (snapshot.service_name or "").strip().lower() == normalized_service:
                score += 4
            if normalized_asset and (snapshot.asset_name or "").strip().lower() == normalized_asset:
                score += 3
            if (snapshot.priority or "").strip().lower() == normalized_priority:
                score += 1
            if (snapshot.status or "").strip().lower() in RESOLUTION_STATUS_BONUS:
                score += 2
            score += min(max(snapshot.correlation_event_count, 0), 2)
            ranked.append((score, snapshot))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].source_updated_at or item[1].snapshot_updated_at,
                item[1].ticket_id,
            ),
            reverse=True,
        )
        return [snapshot for _, snapshot in ranked]

    def _build_history_hint(
        self,
        snapshot: TicketAnalyticsSnapshotRecord,
        payload: TicketTriageRequest,
    ) -> str | None:
        normalized_service = (payload.service_name or "").strip().lower()
        snapshot_service = (snapshot.service_name or "").strip().lower()
        if normalized_service and snapshot_service == normalized_service:
            return (
                f"Historico recente do servico {snapshot.service_name} mostra incidente parecido; "
                "compare sintomas, autenticacao e ultima mudanca conhecida antes de escalar."
            )
        if snapshot.routed_to:
            return (
                f"Casos parecidos recentes convergiram para {snapshot.routed_to}; "
                "valide esse caminho antes de redirecionar o atendimento."
            )
        return f"Ha caso recente semelhante ({snapshot.ticket_id}); compare escopo, prioridade e status antes de atuar."

    def _format_similar_incident(self, snapshot: TicketAnalyticsSnapshotRecord) -> str:
        return (
            f"Ticket {snapshot.ticket_id} ({snapshot.status}) servico={snapshot.service_name or 'n/a'} "
            f"fila={snapshot.routed_to or 'n/a'} prioridade={snapshot.priority or 'n/a'}"
        )

    async def _try_llm_assist(
        self,
        payload: TicketTriageRequest,
        suggested_category: str | None,
        suggested_priority: TicketPriority,
        suggested_queue: str,
        resolution_hints: list[str],
        similar_incidents: list[str],
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
            f"Prioridade sugerida: {suggested_priority.value}\n"
            f"Fila final prevista: {suggested_queue}\n"
            f"Dicas de resolucao recuperadas: {' | '.join(resolution_hints) if resolution_hints else 'n/a'}\n"
            f"Casos similares recentes: {' | '.join(similar_incidents) if similar_incidents else 'n/a'}"
        )

        try:
            result = await self.llm_client.generate_text(
                user_prompt=prompt,
                system_prompt=(
                    "Voce faz apenas triagem segura para helpdesk. "
                    "Nao contradiga a fila final prevista quando ela ja estiver definida. "
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

    def _align_next_steps_with_queue(self, steps: list[str], queue: str) -> list[str]:
        if queue == "Infraestrutura-N1":
            return [
                step.replace("ServiceDesk-Acessos", queue).replace("ServiceDesk-N1", queue)
                for step in steps
            ]
        return steps

    def _first_sentence(self, text: str) -> str:
        sanitized = " ".join(text.split())
        if not sanitized:
            return ""
        for separator in (". ", "! ", "? "):
            if separator in sanitized:
                return sanitized.split(separator, maxsplit=1)[0].strip()
        return sanitized[:180].strip()