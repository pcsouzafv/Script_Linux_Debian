import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.glpi import MOCK_TICKET_STORE


client = TestClient(app)


def test_healthcheck_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_open_ticket_works_in_mock_mode() -> None:
    payload = {
        "subject": "Falha de acesso ao GLPI",
        "description": "Usuário relata que não consegue autenticar no portal do GLPI.",
        "category": "acesso",
        "asset_name": "glpi-web-01",
        "service_name": "glpi",
        "priority": "high",
        "requester": {
            "external_id": "user-123",
            "display_name": "Maria Santos",
            "phone_number": "+5521997775269",
            "role": "user",
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["integration_mode"] in {"mock", "mixed"}
    assert body["requester_glpi_user_id"] is None
    assert body["triage"]["resolved_category"] == "acesso"
    assert body["triage"]["suggested_queue"] == "ServiceDesk-Acessos"


def test_get_ticket_returns_local_mock_ticket() -> None:
    create_payload = {
        "subject": "Erro de impressão na matriz",
        "description": "Impressora da recepção parou de responder após troca de toner.",
        "category": "infra",
        "asset_name": "printer-matriz-01",
        "service_name": "impressao",
        "priority": "medium",
        "requester": {
            "external_id": "user-456",
            "display_name": "Carlos Lima",
            "phone_number": "+5511977776666",
            "role": "user",
        },
    }

    create_response = client.post("/api/v1/helpdesk/tickets/open", json=create_payload)
    ticket_id = create_response.json()["ticket_id"]

    response = client.get(f"/api/v1/helpdesk/tickets/{ticket_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == ticket_id
    assert body["subject"] == create_payload["subject"]
    assert body["status"] == "queued-local"
    assert body["integration_mode"] == "mock"


def test_triage_endpoint_suggests_queue_and_next_steps() -> None:
    payload = {
        "subject": "Usuarios sem acesso ao ERP",
        "description": "Time financeiro relata erro de autenticacao e nenhum usuario consegue entrar no ERP.",
        "service_name": "erp",
    }

    response = client.post("/api/v1/helpdesk/triage", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_category"] == "acesso"
    assert body["resolved_priority"] == "high"
    assert body["suggested_queue"] == "ServiceDesk-Acessos"
    assert len(body["next_steps"]) >= 1


def test_open_ticket_applies_triage_when_priority_is_omitted() -> None:
    payload = {
        "subject": "Servico fora do ar",
        "description": "Aplicacao principal esta fora do ar e todos os usuarios ficaram indisponiveis.",
        "asset_name": "app-prod-01",
        "service_name": "erp",
        "requester": {
            "external_id": "user-crit-001",
            "display_name": "Operacao Financeira",
            "phone_number": "+5511900001111",
            "role": "user",
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["triage"]["resolved_priority"] == "critical"
    assert body["routed_to"] == "NOC-Critico"
    assert "Resumo de triagem" in " ".join(body["notes"])


def test_open_ticket_generates_unique_mock_ids_even_when_time_source_repeats(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.services.glpi.time", lambda: 1710000000, raising=False)
    MOCK_TICKET_STORE.clear()

    payload = {
        "subject": "Teste de unicidade no modo mock",
        "description": "Abrindo dois chamados seguidos para validar IDs únicos.",
        "category": "infra",
        "asset_name": "app-node-01",
        "service_name": "api",
        "priority": "medium",
        "requester": {
            "external_id": "user-unique",
            "display_name": "Teste Unico",
            "phone_number": "+5511912340000",
            "role": "user",
        },
    }

    try:
        first_response = client.post("/api/v1/helpdesk/tickets/open", json=payload)
        second_response = client.post("/api/v1/helpdesk/tickets/open", json=payload)
    finally:
        MOCK_TICKET_STORE.clear()

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["ticket_id"] != second_response.json()["ticket_id"]


def test_get_unknown_ticket_returns_404() -> None:
    response = client.get("/api/v1/helpdesk/tickets/GLPI-LOCAL-inexistente")

    assert response.status_code == 404
    assert "não encontrado" in response.json()["detail"].lower()


def test_get_registered_identity_returns_directory_entry() -> None:
    response = client.get("/api/v1/helpdesk/identities/+5511912345678")

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "tech-ana-souza"
    assert body["role"] == "technician"
    assert body["team"] == "infraestrutura"
    assert body["glpi_user_id"] == 201


def test_get_registered_admin_identity_returns_directory_entry() -> None:
    response = client.get("/api/v1/helpdesk/identities/+5511900019999")

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "admin-ricardo-ops"
    assert body["role"] == "admin"
    assert body["team"] == "plataforma"


def test_whatsapp_message_uses_identity_directory_role() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Nome ignorado",
        "text": "Quero consultar o incidente do servidor web.",
        "requester_role": "user",
        "service_name": "web",
        "priority": "medium",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "technician"
    assert body["requester_external_id"] == "tech-ana-souza"
    assert body["requester_team"] == "infraestrutura"
    assert body["requester_glpi_user_id"] == 201
    assert body["identity_source"] == "directory"
    assert body["assistant_result"]["flow_name"] == "technician_operations"
    assert "Não abri chamado automaticamente" in body["assistant_result"]["reply_text"]


def test_user_whatsapp_message_opens_ticket_by_default() -> None:
    payload = {
        "sender_phone": "+5521997775269",
        "sender_name": "Maria Santos",
        "text": "Estou sem acesso ao GLPI desde cedo.",
        "requester_role": "user",
        "service_name": "glpi",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "ticket"
    assert body["requester_role"] == "user"
    assert body["ticket"]["requester_glpi_user_id"] == 101


def test_user_ticket_description_uses_identity_resolved_by_phone_for_sender() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Nome incorreto do payload",
                "text": "Estou sem acesso ao GLPI desde cedo.",
                "requester_role": "user",
                "service_name": "glpi",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"

        ticket_id = body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert "Remetente: Maria Santos" in record.description
        assert "Nome incorreto do payload" not in record.description
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_greeting_starts_catalog_intake() -> None:
    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5521997775269",
            "sender_name": None,
            "text": "Oi",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "user"
    assert body["assistant_result"]["flow_name"] == "user_catalog_intake"
    assert body["assistant_result"]["intake_stage"] == "awaiting_catalog"
    assert any(option.startswith("1. ") for option in body["assistant_result"]["available_options"])
    assert "classificar" in body["assistant_result"]["reply_text"].lower()


def test_user_catalog_sequence_collects_context_before_opening_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "Oi",
                "requester_role": "user",
            },
        )
        second_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "1",
                "requester_role": "user",
            },
        )
        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "Nao consigo acessar o ERP desde 08:10.",
                "requester_role": "user",
            },
        )

        assert first_response.status_code == 202
        assert second_response.status_code == 202
        second_body = second_response.json()
        assert second_body["outcome_type"] == "assistant"
        assert second_body["assistant_result"]["intake_stage"] == "awaiting_description"

        assert final_response.status_code == 202
        final_body = final_response.json()
        assert final_body["outcome_type"] == "ticket"

        ticket_id = final_body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert record.subject.startswith("WhatsApp: Acesso / Login / Senha -")
        assert "Remetente: Maria Santos" in record.description
        assert "Tipo de chamado: Acesso / Login / Senha" in record.description
        assert "Historico da coleta:" in record.description
        assert "- Oi" in record.description
        assert "- 1" in record.description
        assert "- Nao consigo acessar o ERP desde 08:10." in record.description
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_intake_context_shift_reclassifies_new_issue() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Oi",
                "requester_role": "user",
            },
        )
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade a VPN nao conecta desde 07:40 e nao consigo acessar a rede do escritorio.",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"
        assert any("mudou o contexto" in note.lower() for note in body["notes"])

        ticket_id = body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert record.subject.startswith("WhatsApp: Rede / VPN / Internet -")
        assert "VPN" in record.description or "vpn" in record.description.lower()
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_lists_owned_options() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "ERP indisponível para o financeiro",
                "description": "Usuária precisa encerrar um chamado antigo depois da validação.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "GLPI sem acesso para aprovação",
                "description": "Usuária quer fechar o ticket mais recente após confirmação.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "closed"
        MOCK_TICKET_STORE[second_ticket].status = "solved"

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert second_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert first_ticket not in "\n".join(body["assistant_result"]["available_options"])
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_lists_open_owned_tickets_even_if_new_or_waiting() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado ainda novo para acompanhamento",
                "description": "Usuária quer saber por que ainda não consegue encerrar pelo WhatsApp.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado aguardando atendimento",
                "description": "Usuária ainda não pode fechar o ticket porque ele não avançou para resolução.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "new"
        MOCK_TICKET_STORE[second_ticket].status = "waiting"

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert first_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert second_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert "status=new" in "\n".join(body["assistant_result"]["available_options"]).lower()
        assert "status=waiting" in "\n".join(body["assistant_result"]["available_options"]).lower()
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_intake_context_shift_to_finalization_switches_flow() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        existing_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado pronto para ser encerrado",
                "description": "Usuária precisa escolher um ticket para encerrar.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[existing_ticket].status = "solved"

        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Oi",
                "requester_role": "user",
            },
        )
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade quero finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert existing_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert any("fluxo de finalização" in note.lower() for note in body["notes"])
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_selection_closes_chosen_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Primeiro chamado para manter aberto",
                "description": "Este chamado deve continuar aberto após a seleção.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Segundo chamado para finalizar",
                "description": "Este chamado deve ser finalizado quando a usuária escolher a primeira opção.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "processing"
        MOCK_TICKET_STORE[second_ticket].status = "solved"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        assert final_response.status_code == 202
        body = final_response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "completed"
        assert second_ticket in body["assistant_result"]["reply_text"]
        assert MOCK_TICKET_STORE[second_ticket].status == "closed"
        assert MOCK_TICKET_STORE[first_ticket].status == "processing"
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_selection_closes_new_ticket_when_user_resolved_it() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado novo resolvido pelo proprio usuario",
                "description": "Usuária resolveu o problema sem precisar aguardar atendimento técnico.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado em andamento para permanecer aberto",
                "description": "Este chamado não deve ser encerrado quando a usuária escolher a primeira opção.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "new"
        MOCK_TICKET_STORE[second_ticket].status = "processing"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "2",
                "requester_role": "user",
            },
        )

        assert final_response.status_code == 202
        body = final_response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "completed"
        assert first_ticket in body["assistant_result"]["reply_text"]
        assert MOCK_TICKET_STORE[first_ticket].status == "closed"
        assert MOCK_TICKET_STORE[second_ticket].status == "processing"
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalization_context_shift_opens_new_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        existing_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado antigo para finalização",
                "description": "Usuária iniciou o fluxo de finalização antes de mudar de assunto.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[existing_ticket].status = "solved"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade estou sem VPN desde 07:40 e nao consigo conectar no escritorio.",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"
        assert any("saiu do contexto de finalização" in note.lower() for note in body["notes"])

        new_ticket_id = body["ticket"]["ticket_id"]
        assert new_ticket_id != existing_ticket
        assert MOCK_TICKET_STORE[existing_ticket].status == "solved"
        assert MOCK_TICKET_STORE[new_ticket_id].subject.startswith("WhatsApp: Rede / VPN / Internet -")
    finally:
        MOCK_TICKET_STORE.clear()


def test_technician_open_command_creates_ticket_explicitly() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Ana Souza",
        "text": "/open ERP indisponível para o financeiro desde 08:00.",
        "requester_role": "user",
        "service_name": "erp",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "open"
    assert body["command_result"]["opened_ticket"]["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["command_result"]["opened_ticket"]["requester_glpi_user_id"] == 201


def test_admin_freeform_message_uses_admin_operational_flow() -> None:
    payload = {
        "sender_phone": "+5511900019999",
        "sender_name": "Ricardo Admin",
        "text": "Preciso revisar o impacto do incidente do ERP.",
        "requester_role": "user",
        "service_name": "erp",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "admin"
    assert body["assistant_result"]["flow_name"] == "admin_operations"


def test_technician_command_returns_operational_result() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Ana Souza",
        "text": "/me",
        "requester_role": "user",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "me"
    assert body["command_result"]["status"] == "completed"
    assert body["requester_glpi_user_id"] == 201


def test_technician_ticket_command_reads_existing_ticket() -> None:
    create_payload = {
        "subject": "Servidor de banco sem resposta",
        "description": "Banco principal não responde às consultas do ERP.",
        "category": "infra",
        "asset_name": "db-prod-01",
        "service_name": "postgresql",
        "priority": "high",
        "requester": {
            "external_id": "user-789",
            "display_name": "Bruno Costa",
            "phone_number": "+5511966665555",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/ticket {ticket_id}",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "ticket"
    assert body["command_result"]["ticket"]["ticket_id"] == ticket_id


def test_technician_comment_command_adds_followup() -> None:
    create_payload = {
        "subject": "ERP indisponível para o financeiro",
        "description": "Usuários do financeiro relatam falha ao autenticar.",
        "category": "acesso",
        "asset_name": "erp-fin-01",
        "service_name": "erp",
        "priority": "high",
        "requester": {
            "external_id": "user-100",
            "display_name": "Luciana Prado",
            "phone_number": "+5511955554444",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/comment {ticket_id} Coletando logs do host afetado.",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "comment"
    assert body["command_result"]["ticket"]["followup_count"] == 1


def test_technician_comment_command_notifies_requester() -> None:
    create_payload = {
        "subject": "ERP indisponível para o financeiro",
        "description": "Usuários do financeiro relatam falha ao autenticar.",
        "category": "acesso",
        "asset_name": "erp-fin-01",
        "service_name": "erp",
        "priority": "high",
        "requester": {
            "external_id": "user-carlos-lima",
            "display_name": "Carlos Lima",
            "phone_number": "+5511977776666",
            "role": "user",
            "glpi_user_id": 102,
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/comment {ticket_id} Reiniciei a validação do acesso e preciso que você teste novamente.",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "comment"
    assert "enviado ao solicitante" in body["command_result"]["reply_text"].lower()
    assert any("+5511977776666" in note for note in body["notes"])


def test_technician_status_command_updates_allowed_status() -> None:
    create_payload = {
        "subject": "VPN intermitente",
        "description": "Conexão cai a cada poucos minutos.",
        "category": "rede",
        "asset_name": "vpn-edge-01",
        "service_name": "vpn",
        "priority": "medium",
        "requester": {
            "external_id": "user-200",
            "display_name": "Renata Melo",
            "phone_number": "+5511944443333",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/status {ticket_id} processing",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "status"
    assert body["command_result"]["ticket"]["status"] == "processing"


def test_technician_status_command_denies_closed_status() -> None:
    create_payload = {
        "subject": "Chamado para validação",
        "description": "Validação de controle de permissão.",
        "category": "acesso",
        "asset_name": "auth-01",
        "service_name": "auth",
        "priority": "medium",
        "requester": {
            "external_id": "user-300",
            "display_name": "Rafael Nunes",
            "phone_number": "+5511933332222",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/status {ticket_id} closed",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["status"] == "forbidden"


def test_supervisor_assign_command_updates_assignee() -> None:
    create_payload = {
        "subject": "Fila de impressão parada",
        "description": "Impressoras da recepção não recebem novos jobs.",
        "category": "infra",
        "asset_name": "print-spool-01",
        "service_name": "print-spooler",
        "priority": "high",
        "requester": {
            "external_id": "user-400",
            "display_name": "Patricia Gomes",
            "phone_number": "+5511922221111",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5521972008679",
            "sender_name": "Paula Almeida",
            "text": f"/assign {ticket_id} tech-ana-souza",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "assign"
    assert body["command_result"]["ticket"]["assigned_glpi_user_id"] == 201


def test_technician_assign_command_is_forbidden() -> None:
    create_payload = {
        "subject": "Supervisor only action",
        "description": "Teste de permissão de atribuição.",
        "category": "infra",
        "asset_name": "router-edge-02",
        "service_name": "routing",
        "priority": "medium",
        "requester": {
            "external_id": "user-500",
            "display_name": "Fabio Teixeira",
            "phone_number": "+5511910101010",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/assign {ticket_id} supervisor-paula-almeida",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["status"] == "forbidden"


def test_meta_webhook_creates_ticket_with_valid_signature() -> None:
    settings = get_settings()
    original_validate_signature = settings.whatsapp_validate_signature
    original_app_secret = settings.whatsapp_app_secret

    settings.whatsapp_validate_signature = True
    settings.whatsapp_app_secret = "super-secret"

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "5511999999999",
                                    "profile": {"name": "João Silva"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": "wamid.HBgLTESTE123",
                                    "timestamp": "1713456789",
                                    "type": "text",
                                    "text": {"body": "Estou sem acesso ao ERP"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        b"super-secret",
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/meta",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
    finally:
        settings.whatsapp_validate_signature = original_validate_signature
        settings.whatsapp_app_secret = original_app_secret

    assert response.status_code == 202
    body = response.json()
    assert body["processed_messages"] == 1
    assert len(body["interactions"]) == 1
    assert body["interactions"][0]["ticket"]["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["interactions"][0]["requester_external_id"] == "5511999999999"


def test_meta_webhook_rejects_invalid_signature() -> None:
    settings = get_settings()
    original_validate_signature = settings.whatsapp_validate_signature
    original_app_secret = settings.whatsapp_app_secret

    settings.whatsapp_validate_signature = True
    settings.whatsapp_app_secret = "super-secret"

    payload = {
        "object": "whatsapp_business_account",
        "entry": [],
    }

    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/meta",
            json=payload,
            headers={"X-Hub-Signature-256": "sha256=assinatura-invalida"},
        )
    finally:
        settings.whatsapp_validate_signature = original_validate_signature
        settings.whatsapp_app_secret = original_app_secret

    assert response.status_code == 403
    assert "assinatura" in response.json()["detail"].lower()
