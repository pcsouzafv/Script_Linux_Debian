from dataclasses import dataclass, field
import hashlib
import hmac
import re
from typing import Any

import httpx

from app.core.config import Settings
from app.schemas.helpdesk import NormalizedWhatsAppMessage, UserRole
from app.services.exceptions import IntegrationError


@dataclass(slots=True)
class WhatsAppDeliveryResult:
    status: str
    mode: str
    provider_message_id: str | None = None
    notes: list[str] = field(default_factory=list)


class WhatsAppClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def meta_configured(self) -> bool:
        return bool(
            self.settings.whatsapp_access_token and self.settings.whatsapp_phone_number_id
        )

    @property
    def evolution_configured(self) -> bool:
        return bool(
            self.settings.evolution_base_url
            and self.settings.evolution_api_key
            and self.settings.evolution_instance_name
        )

    @property
    def configured(self) -> bool:
        return self.meta_configured or self.evolution_configured

    async def send_text_message(self, to_number: str, body: str) -> WhatsAppDeliveryResult:
        provider = self._resolve_delivery_provider()

        if provider == "meta":
            return await self._send_text_message_via_meta(to_number, body)

        if provider == "evolution":
            return await self._send_text_message_via_evolution(to_number, body)

        notes = [f"Confirmação simulada para {to_number}.", *self._mock_delivery_notes()]
        return WhatsAppDeliveryResult(
            status="queued-local",
            mode="mock",
            notes=notes,
        )

    async def _send_text_message_via_meta(
        self,
        to_number: str,
        body: str,
    ) -> WhatsAppDeliveryResult:
        headers = {
            "Authorization": f"Bearer {self.settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": body},
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self._messages_url(), headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao enviar mensagem pelo WhatsApp Meta: {exc}") from exc

        messages = data.get("messages") or []
        provider_message_id = None
        if messages:
            provider_message_id = messages[0].get("id")

        if not provider_message_id:
            raise IntegrationError("WhatsApp Meta não retornou identificador de mensagem.")

        return WhatsAppDeliveryResult(
            status="sent",
            mode="meta",
            provider_message_id=provider_message_id,
            notes=["Confirmação enviada para o usuário via WhatsApp Meta."],
        )

    async def _send_text_message_via_evolution(
        self,
        to_number: str,
        body: str,
    ) -> WhatsAppDeliveryResult:
        headers = {
            "apikey": self.settings.evolution_api_key or "",
            "Content-Type": "application/json",
        }
        payload = {
            "number": self._normalize_evolution_number(to_number),
            "text": body,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self._evolution_send_text_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao enviar mensagem pela Evolution API: {exc}") from exc

        provider_message_id = self._extract_evolution_message_id(data)
        if not provider_message_id:
            raise IntegrationError("Evolution API não retornou identificador de mensagem.")

        return WhatsAppDeliveryResult(
            status="sent",
            mode="evolution",
            provider_message_id=provider_message_id,
            notes=["Confirmação enviada para o usuário via Evolution API."],
        )

    def _messages_url(self) -> str:
        phone_number_id = self.settings.whatsapp_phone_number_id or ""
        return f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"

    def _evolution_send_text_url(self) -> str:
        base_url = (self.settings.evolution_base_url or "").rstrip("/")
        instance_name = self.settings.evolution_instance_name or ""
        return f"{base_url}/message/sendText/{instance_name}"

    def validate_webhook_signature(
        self,
        raw_body: bytes,
        signature_header: str | None,
    ) -> bool:
        if not self.settings.whatsapp_validate_signature:
            return True

        if not self.settings.whatsapp_app_secret:
            raise IntegrationError(
                "A validação de assinatura do WhatsApp está habilitada, mas HELPDESK_WHATSAPP_APP_SECRET não foi configurado."
            )

        if not signature_header or not signature_header.lower().startswith("sha256="):
            return False

        received_signature = signature_header.split("=", maxsplit=1)[1].strip().lower()
        expected_signature = hmac.new(
            self.settings.whatsapp_app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, received_signature)

    def validate_evolution_webhook_secret(self, secret_header: str | None) -> bool:
        expected_secret = self.settings.evolution_webhook_secret
        if not expected_secret:
            return True

        if not secret_header:
            return False

        return hmac.compare_digest(secret_header.strip(), expected_secret)

    def normalize_webhook_payload(
        self,
        payload: dict,
    ) -> tuple[list[NormalizedWhatsAppMessage], list[str]]:
        normalized_messages: list[NormalizedWhatsAppMessage] = []
        ignored_events: list[str] = []

        if payload.get("object") not in {None, "whatsapp_business_account"}:
            return [], ["Payload ignorado: objeto do webhook não pertence ao WhatsApp Business."]

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value") or {}
                contacts = self._map_contacts(value.get("contacts") or [])

                statuses = value.get("statuses") or []
                if statuses:
                    ignored_events.append(
                        f"{len(statuses)} atualização(ões) de status recebida(s) e ignorada(s)."
                    )

                for message in value.get("messages") or []:
                    sender_phone = str(message.get("from") or "").strip()
                    if not sender_phone:
                        ignored_events.append("Mensagem ignorada: remetente ausente no payload da Meta.")
                        continue

                    text = self._extract_message_text(message)
                    if not text:
                        message_id = message.get("id") or "sem-id"
                        message_type = message.get("type") or "desconhecido"
                        ignored_events.append(
                            f"Mensagem {message_id} ignorada: tipo {message_type} não suportado."
                        )
                        continue

                    normalized_messages.append(
                        NormalizedWhatsAppMessage(
                            sender_phone=sender_phone,
                            sender_name=contacts.get(sender_phone),
                            text=text,
                            external_message_id=message.get("id"),
                            requester_role=UserRole.USER,
                        )
                    )

        if not normalized_messages and not ignored_events:
            ignored_events.append("Payload sem mensagens processáveis.")

        return normalized_messages, ignored_events

    def normalize_evolution_webhook_payload(
        self,
        payload: dict,
    ) -> tuple[list[NormalizedWhatsAppMessage], list[str]]:
        normalized_messages: list[NormalizedWhatsAppMessage] = []
        ignored_events: list[str] = []

        event_name = self._normalize_evolution_event_name(payload.get("event") or payload.get("type"))
        if event_name and ("message" not in event_name or "upsert" not in event_name):
            return [], [f"Evento Evolution ignorado: {event_name}."]

        for message in self._extract_evolution_message_entries(payload):
            if self._is_evolution_outbound_message(message):
                ignored_events.append("Mensagem ignorada: originada pela própria instância Evolution.")
                continue

            remote_jid = self._extract_evolution_remote_jid(message)
            if not remote_jid:
                ignored_events.append("Mensagem ignorada: remetente ausente no payload da Evolution.")
                continue

            if remote_jid.endswith("@g.us"):
                ignored_events.append(f"Mensagem ignorada: grupo {remote_jid} não é suportado no MVP.")
                continue

            sender_phone = self._resolve_evolution_sender_phone(remote_jid)
            if not sender_phone:
                ignored_events.append(
                    f"Mensagem ignorada: JID {remote_jid} não pôde ser convertido em número."
                )
                continue

            text = self._extract_evolution_message_text(message)
            if not text:
                message_id = self._extract_evolution_message_id(message) or "sem-id"
                message_type = str(message.get("messageType") or "desconhecido")
                ignored_events.append(
                    f"Mensagem Evolution {message_id} ignorada: tipo {message_type} não suportado."
                )
                continue

            normalized_messages.append(
                NormalizedWhatsAppMessage(
                    sender_phone=sender_phone,
                    sender_name=self._extract_evolution_sender_name(message),
                    text=text,
                    external_message_id=self._extract_evolution_message_id(message),
                    requester_role=UserRole.USER,
                )
            )

        if not normalized_messages and not ignored_events:
            ignored_events.append("Payload Evolution sem mensagens processáveis.")

        return normalized_messages, ignored_events

    def _resolve_delivery_provider(self) -> str:
        provider = self.settings.whatsapp_delivery_provider

        if provider == "meta":
            return "meta" if self.meta_configured else "mock"
        if provider == "evolution":
            return "evolution" if self.evolution_configured else "mock"
        if provider == "mock":
            return "mock"

        if self.evolution_configured:
            return "evolution"
        if self.meta_configured:
            return "meta"
        return "mock"

    def _mock_delivery_notes(self) -> list[str]:
        provider = self.settings.whatsapp_delivery_provider
        if provider == "meta" and not self.meta_configured:
            return [
                "Envio configurado para Meta, mas as credenciais não estão completas; mantido modo mock.",
            ]
        if provider == "evolution" and not self.evolution_configured:
            return [
                "Envio configurado para Evolution, mas base URL, API key ou instância não foram configuradas; mantido modo mock.",
            ]
        if provider == "auto" and not self.configured:
            return [
                "Nenhum provedor de entrega configurado; mantido modo mock para confirmações.",
            ]
        return []

    def _map_contacts(self, contacts: list[dict]) -> dict[str, str]:
        mapped_contacts: dict[str, str] = {}
        for contact in contacts:
            wa_id = contact.get("wa_id")
            profile = contact.get("profile") or {}
            name = profile.get("name") or profile.get("display_name")
            if wa_id and name:
                mapped_contacts[str(wa_id)] = str(name)
        return mapped_contacts

    def _extract_message_text(self, message: dict) -> str | None:
        message_type = message.get("type")

        if message_type == "text":
            return (message.get("text") or {}).get("body")

        if message_type == "button":
            return (message.get("button") or {}).get("text")

        if message_type == "interactive":
            interactive = message.get("interactive") or {}
            interactive_type = interactive.get("type")
            if interactive_type == "button_reply":
                button_reply = interactive.get("button_reply") or {}
                return button_reply.get("title") or button_reply.get("id")
            if interactive_type == "list_reply":
                list_reply = interactive.get("list_reply") or {}
                return list_reply.get("title") or list_reply.get("id")

        media_types = {"audio", "document", "image", "sticker", "video"}
        if message_type in media_types:
            return f"[{message_type}] mídia recebida via WhatsApp."

        if message_type == "location":
            location = message.get("location") or {}
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            return f"[location] Localização recebida via WhatsApp: lat={latitude}, lon={longitude}."

        if message_type == "contacts":
            return "[contacts] Contato compartilhado via WhatsApp."

        return None

    def _normalize_evolution_event_name(self, event_name: object) -> str:
        normalized = str(event_name or "").strip().lower()
        return normalized.replace("_", ".")

    def _extract_evolution_message_entries(self, payload: dict) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        def merge_context(base: dict[str, Any], candidate: Any) -> dict[str, Any] | None:
            if not isinstance(candidate, dict):
                return None
            merged = {key: value for key, value in base.items() if key != "data"}
            merged.update(candidate)
            return merged

        if payload.get("key") or payload.get("message"):
            top_level = merge_context({}, payload)
            if top_level:
                entries.append(top_level)

        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                merged = merge_context(payload, item)
                if merged:
                    entries.append(merged)
            return entries

        if isinstance(data, dict):
            data_context = merge_context(payload, data) or {}

            nested_messages = data.get("messages")
            if isinstance(nested_messages, list):
                for item in nested_messages:
                    merged = merge_context(data_context, item)
                    if merged:
                        entries.append(merged)
                return entries

            if data.get("key") or data.get("message"):
                entries.append(data_context)

        return entries

    def _is_evolution_outbound_message(self, payload: dict[str, Any]) -> bool:
        key = payload.get("key") or {}
        return bool(payload.get("fromMe") or key.get("fromMe"))

    def _extract_evolution_remote_jid(self, payload: dict[str, Any]) -> str | None:
        key = payload.get("key") or {}
        candidates = [
            key.get("remoteJid"),
            payload.get("remoteJid"),
            payload.get("jid"),
            payload.get("chatId"),
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _extract_evolution_sender_name(self, payload: dict[str, Any]) -> str | None:
        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
        candidates = [
            payload.get("pushName"),
            payload.get("senderName"),
            sender.get("pushName"),
            payload.get("profileName"),
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _extract_evolution_message_text(self, payload: dict[str, Any]) -> str | None:
        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        return self._extract_text_from_evolution_message(message)

    def _extract_text_from_evolution_message(self, message: dict[str, Any]) -> str | None:
        for wrapper_name in (
            "ephemeralMessage",
            "viewOnceMessage",
            "viewOnceMessageV2",
            "documentWithCaptionMessage",
        ):
            wrapper = message.get(wrapper_name)
            if isinstance(wrapper, dict):
                nested_message = wrapper.get("message")
                if isinstance(nested_message, dict):
                    extracted = self._extract_text_from_evolution_message(nested_message)
                    if extracted:
                        return extracted

        conversation_text = message.get("conversation")
        if conversation_text:
            return str(conversation_text)

        extended_text = (message.get("extendedTextMessage") or {}).get("text")
        if extended_text:
            return str(extended_text)

        image_caption = (message.get("imageMessage") or {}).get("caption")
        if image_caption:
            return str(image_caption)

        video_caption = (message.get("videoMessage") or {}).get("caption")
        if video_caption:
            return str(video_caption)

        document_caption = (message.get("documentMessage") or {}).get("caption")
        if document_caption:
            return str(document_caption)

        button_response = message.get("buttonsResponseMessage") or {}
        if button_response.get("selectedDisplayText") or button_response.get("selectedButtonId"):
            return str(
                button_response.get("selectedDisplayText") or button_response.get("selectedButtonId")
            )

        list_response = message.get("listResponseMessage") or {}
        if list_response.get("title"):
            return str(list_response.get("title"))
        single_select = list_response.get("singleSelectReply") or {}
        if single_select.get("selectedRowId"):
            return str(single_select.get("selectedRowId"))

        template_reply = message.get("templateButtonReplyMessage") or {}
        if template_reply.get("displayText") or template_reply.get("selectedId"):
            return str(template_reply.get("displayText") or template_reply.get("selectedId"))

        if message.get("audioMessage"):
            return "[audio] áudio recebido via Evolution API."
        if message.get("stickerMessage"):
            return "[sticker] figurinha recebida via Evolution API."
        if message.get("documentMessage"):
            return "[document] documento recebido via Evolution API."
        if message.get("imageMessage"):
            return "[image] imagem recebida via Evolution API."
        if message.get("videoMessage"):
            return "[video] vídeo recebido via Evolution API."
        if message.get("contactsArrayMessage") or message.get("contactMessage"):
            return "[contacts] contato compartilhado via Evolution API."

        location_message = message.get("locationMessage") or {}
        if location_message:
            latitude = location_message.get("degreesLatitude") or location_message.get("latitude")
            longitude = location_message.get("degreesLongitude") or location_message.get("longitude")
            return (
                f"[location] Localização recebida via Evolution API: lat={latitude}, lon={longitude}."
            )

        return None

    def _extract_evolution_message_id(self, payload: Any) -> str | None:
        candidates = [
            self._nested_get(payload, "key", "id"),
            self._nested_get(payload, "data", "key", "id"),
            self._nested_get(payload, "response", "key", "id"),
            self._nested_get(payload, "message", "key", "id"),
            self._nested_get(payload, "response", "message", "key", "id"),
            payload.get("id") if isinstance(payload, dict) else None,
            payload.get("messageId") if isinstance(payload, dict) else None,
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _normalize_evolution_number(self, value: str) -> str:
        if "@" in value:
            return value.split("@", maxsplit=1)[0].split(":", maxsplit=1)[0]
        digits = re.sub(r"\D+", "", value)
        return digits or value

    def _resolve_evolution_sender_phone(self, jid: str) -> str | None:
        if self._is_evolution_lid_jid(jid):
            return self._lookup_evolution_lid_phone(jid)
        return self._extract_phone_from_jid(jid)

    def _lookup_evolution_lid_phone(self, jid: str) -> str | None:
        lid_key = self._normalize_evolution_lid_key(jid)
        if not lid_key:
            return None
        phone_number = self.settings.evolution_lid_phone_map.get(lid_key)
        if not phone_number:
            return None
        return phone_number

    def _is_evolution_lid_jid(self, jid: str) -> bool:
        return jid.lower().split(":", maxsplit=1)[0].endswith("@lid")

    def _normalize_evolution_lid_key(self, jid: str) -> str | None:
        candidate = jid.split("@", maxsplit=1)[0].split(":", maxsplit=1)[0]
        digits = re.sub(r"\D+", "", candidate)
        return digits or None

    def _extract_phone_from_jid(self, jid: str) -> str | None:
        candidate = jid.split("@", maxsplit=1)[0].split(":", maxsplit=1)[0]
        digits = re.sub(r"\D+", "", candidate)
        return digits or None

    def _nested_get(self, payload: Any, *path: str) -> Any:
        current = payload
        for segment in path:
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current
