from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from app.core.config import Settings
from app.services.exceptions import IntegrationError


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    GROQ = "groq"
    GEMINI = "gemini"
    CLAUDE = "claude"


@dataclass(slots=True)
class LLMStatus:
    enabled: bool
    provider: str
    model: str | None
    status: str
    base_url: str | None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMTextResult:
    provider: str
    model: str
    content: str
    status: str
    notes: list[str] = field(default_factory=list)


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider(self.settings.llm_provider)

    def get_status(self) -> LLMStatus:
        provider = self.provider
        model = self._resolved_model()
        base_url = self._resolved_base_url()
        notes: list[str] = []

        if not self.settings.llm_enabled:
            notes.append("Camada de IA desabilitada por configuração.")
            return LLMStatus(
                enabled=False,
                provider=provider.value,
                model=model,
                status="disabled",
                base_url=base_url,
                notes=notes,
            )

        if not model:
            notes.append("Nenhum modelo foi configurado para a camada de IA.")
            return LLMStatus(
                enabled=True,
                provider=provider.value,
                model=None,
                status="incomplete",
                base_url=base_url,
                notes=notes,
            )

        if provider is not LLMProvider.OLLAMA and not self._resolved_api_key():
            notes.append(f"API key ausente para o provider {provider.value}.")
            return LLMStatus(
                enabled=True,
                provider=provider.value,
                model=model,
                status="incomplete",
                base_url=base_url,
                notes=notes,
            )

        notes.append(f"Provider ativo configurado: {provider.value}.")
        if provider is LLMProvider.OLLAMA:
            notes.append("Ollama local não exige API key, apenas base URL e modelo.")
        return LLMStatus(
            enabled=True,
            provider=provider.value,
            model=model,
            status="configured",
            base_url=base_url,
            notes=notes,
        )

    async def generate_text(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 400,
        temperature: float | None = None,
    ) -> LLMTextResult:
        status = self.get_status()
        if status.status != "configured":
            raise IntegrationError(
                f"Camada de IA não está pronta para uso: status={status.status}."
            )

        model = status.model or ""
        selected_temperature = (
            self.settings.llm_temperature if temperature is None else temperature
        )

        if self.provider is LLMProvider.OLLAMA:
            return await self._generate_with_ollama(
                model=model,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=selected_temperature,
            )
        if self.provider in {LLMProvider.OPENAI, LLMProvider.GROQ}:
            return await self._generate_with_openai_compatible(
                model=model,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=selected_temperature,
            )
        if self.provider is LLMProvider.GEMINI:
            return await self._generate_with_gemini(
                model=model,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=selected_temperature,
            )
        return await self._generate_with_claude(
            model=model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=selected_temperature,
        )

    async def _generate_with_ollama(
        self,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
        temperature: float,
    ) -> LLMTextResult:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout) as client:
                response = await client.post(self._ollama_chat_url(), json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao consultar Ollama: {exc}") from exc

        content = ((data.get("message") or {}).get("content") or "").strip()
        if not content:
            raise IntegrationError("Ollama não retornou conteúdo textual.")

        return LLMTextResult(
            provider=self.provider.value,
            model=model,
            content=content,
            status="completed",
            notes=["Resposta gerada com sucesso pelo Ollama local."],
        )

    async def _generate_with_openai_compatible(
        self,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMTextResult:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._resolved_api_key() or ''}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout) as client:
                response = await client.post(
                    self._openai_compatible_chat_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(
                f"Falha ao consultar o provider {self.provider.value}: {exc}"
            ) from exc

        choices = data.get("choices") or []
        if not choices:
            raise IntegrationError(
                f"O provider {self.provider.value} não retornou choices na resposta."
            )

        message = choices[0].get("message") or {}
        content = self._extract_openai_compatible_content(message.get("content"))
        if not content:
            raise IntegrationError(
                f"O provider {self.provider.value} não retornou conteúdo textual."
            )

        return LLMTextResult(
            provider=self.provider.value,
            model=model,
            content=content,
            status="completed",
            notes=[f"Resposta gerada com sucesso pelo provider {self.provider.value}."],
        )

    async def _generate_with_gemini(
        self,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
        temperature: float,
    ) -> LLMTextResult:
        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": {"temperature": temperature},
        }
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

        headers = {
            "x-goog-api-key": self._resolved_api_key() or "",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout) as client:
                response = await client.post(
                    self._gemini_generate_url(model),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao consultar Gemini: {exc}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise IntegrationError("Gemini não retornou candidatos na resposta.")

        parts = (((candidates[0].get("content") or {}).get("parts")) or [])
        content = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not content:
            raise IntegrationError("Gemini não retornou conteúdo textual.")

        return LLMTextResult(
            provider=self.provider.value,
            model=model,
            content=content,
            status="completed",
            notes=["Resposta gerada com sucesso pelo Gemini."],
        )

    async def _generate_with_claude(
        self,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMTextResult:
        payload: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        headers = {
            "x-api-key": self._resolved_api_key() or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout) as client:
                response = await client.post(
                    self._claude_messages_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao consultar Claude: {exc}") from exc

        content_blocks = data.get("content") or []
        text_blocks = [
            block.get("text", "")
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        content = "".join(text_blocks).strip()
        if not content:
            raise IntegrationError("Claude não retornou conteúdo textual.")

        return LLMTextResult(
            provider=self.provider.value,
            model=model,
            content=content,
            status="completed",
            notes=["Resposta gerada com sucesso pelo Claude."],
        )

    def _resolved_model(self) -> str | None:
        return self.settings.llm_model

    def _resolved_base_url(self) -> str:
        if self.settings.llm_base_url:
            return self.settings.llm_base_url.rstrip("/")

        defaults = {
            LLMProvider.OLLAMA: "http://127.0.0.1:11434",
            LLMProvider.OPENAI: "https://api.openai.com/v1",
            LLMProvider.GROQ: "https://api.groq.com/openai/v1",
            LLMProvider.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
            LLMProvider.CLAUDE: "https://api.anthropic.com/v1",
        }
        return defaults[self.provider]

    def _resolved_api_key(self) -> str | None:
        generic_key = self.settings.llm_api_key
        if self.provider is LLMProvider.OPENAI:
            return self.settings.openai_api_key or generic_key
        if self.provider is LLMProvider.GROQ:
            return self.settings.groq_api_key or generic_key
        if self.provider is LLMProvider.GEMINI:
            return self.settings.gemini_api_key or generic_key
        if self.provider is LLMProvider.CLAUDE:
            return self.settings.anthropic_api_key or generic_key
        return None

    def _ollama_chat_url(self) -> str:
        base_url = self._resolved_base_url()
        if base_url.endswith("/api/chat"):
            return base_url
        if base_url.endswith("/api"):
            return f"{base_url}/chat"
        return f"{base_url}/api/chat"

    def _openai_compatible_chat_url(self) -> str:
        base_url = self._resolved_base_url()
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1") or base_url.endswith("/openai/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def _gemini_generate_url(self, model: str) -> str:
        base_url = self._resolved_base_url()
        return f"{base_url}/models/{model}:generateContent"

    def _claude_messages_url(self) -> str:
        base_url = self._resolved_base_url()
        if base_url.endswith("/messages"):
            return base_url
        return f"{base_url}/messages"

    def _extract_openai_compatible_content(self, content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""

        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
                continue
            text_parts.append(str((item.get("text") or {}).get("value") or ""))
        return "".join(text_parts).strip()
