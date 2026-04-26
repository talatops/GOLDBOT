from __future__ import annotations

import httpx


class GroqService:
    def __init__(self, api_key: str, openrouter_api_key: str = "", openrouter_model: str = "openai/gpt-4o-mini") -> None:
        self._api_key = api_key
        self._openrouter_api_key = openrouter_api_key
        self._openrouter_model = openrouter_model
        self._url = "https://api.groq.com/openai/v1/chat/completions"
        self._models_url = "https://api.groq.com/openai/v1/models"
        self._openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
        self._openrouter_models_url = "https://openrouter.ai/api/v1/models"
        self._resolved_model: str | None = None
        self._resolved_openrouter_free_model: str | None = None

    async def _resolve_model(self) -> str:
        if self._openrouter_api_key:
            return self._openrouter_model
        if self._resolved_model:
            return self._resolved_model

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(self._models_url, headers=headers)
                response.raise_for_status()
                data = response.json()
            candidates = [
                "llama-3.3-70b-versatile",
                "openai/gpt-oss-20b",
                "llama-3.1-8b-instant",
            ]
            available = {item.get("id") for item in data.get("data", []) if item.get("id")}
            for candidate in candidates:
                if candidate in available:
                    self._resolved_model = candidate
                    return candidate
            if available:
                first = sorted(available)[0]
                self._resolved_model = str(first)
                return self._resolved_model
        except Exception:
            pass

        self._resolved_model = "llama-3.3-70b-versatile"
        return self._resolved_model

    async def answer(self, question: str, market_context: str) -> str:
        if not self._api_key and not self._openrouter_api_key:
            return "No AI key configured. Set GROQ_API_KEY or OPENROUTER_API_KEY in .env."

        system_prompt = (
            "You are a gold market assistant. "
            "Answer the user's question directly based on provided context. "
            "If user asks for a specific location price (e.g., Dubai), prioritize matching extracted custom website content. "
            "If exact value is unavailable, clearly say unavailable and state best known fallback value."
        )
        model = await self._resolve_model()
        payload = {
            "model": model,
            "temperature": 0.25,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Market context:\n{market_context}\n\n"
                        f"Question: {question}\n\n"
                        "Return max 80 words, plain text."
                    ),
                },
            ],
        }
        headers = self._headers()
        try:
            data = await self._send_chat(payload=payload, headers=headers)
            content = data["choices"][0]["message"]["content"]
            return str(content).strip()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402 and self._openrouter_api_key:
                return (
                    "OpenRouter billing/credits are required for the selected model. "
                    "Set OPENROUTER_MODEL to a :free model or add credits."
                )
            return "AI request failed due to provider response error."
        except Exception:
            return (
                "I could not reach the AI provider right now. "
                "Please try again in a moment."
            )

    async def curate_news_update(self, market_context: str) -> str:
        if not self._api_key and not self._openrouter_api_key:
            return "No AI key configured. Set GROQ_API_KEY or OPENROUTER_API_KEY in .env."

        system_prompt = (
            "You are a professional gold market analyst. "
            "Write a concise actionable brief for traders based on provided headlines and live price snapshot. "
            "Output exactly these fields and nothing else: "
            "Signal: BUY/SELL/HOLD, Confidence: High/Medium/Low, Reason: short explanation. "
            "Keep total response under 85 words. "
            "Reason MUST be concrete and include exactly 3 parts in one sentence: "
            "(1) current price action with number, (2) dominant catalyst from the provided headlines, "
            "(3) a clear trigger level/event to change stance. "
            "Never use generic placeholders like 'mixed signals' or 'no clear trigger'."
        )
        model = await self._resolve_model()
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Create a clean Telegram-ready gold market brief in HTML-friendly plain text from this context:\n\n"
                        f"{market_context}\n\n"
                        "Use this strict shape:\n"
                        "Signal: <BUY|SELL|HOLD>\n"
                        "Confidence: <High|Medium|Low>\n"
                        "Reason: <one sentence with 3 concrete parts>."
                    ),
                },
            ],
        }
        headers = self._headers()
        try:
            data = await self._send_chat(payload=payload, headers=headers)
            content = data["choices"][0]["message"]["content"]
            return str(content).strip()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402 and self._openrouter_api_key:
                return (
                    "OpenRouter model requires payment/credits. "
                    "Showing raw website headlines instead."
                )
            return (
                "AI provider rejected the request. "
                "Showing raw website headlines instead."
            )
        except Exception:
            return (
                "I could not curate the market update right now. "
                "Showing raw website headlines instead."
            )

    async def curate_headlines(self, headline_context: str) -> str:
        if not self._api_key and not self._openrouter_api_key:
            return "Top Headlines:\n- AI unavailable (missing API key)."

        system_prompt = (
            "You are a gold macro news editor. "
            "Create exactly 3 dynamic market-pulse bullets from context. "
            "Do not copy source headlines verbatim. "
            "Each bullet must highlight a different angle: (1) price action, (2) macro/rates catalyst, (3) risk trigger to watch. "
            "Each bullet must include: what happened, why it matters for gold, and what to monitor next. "
            "Keep each bullet between 18 and 28 words. "
            "Output plain text only; one line per bullet; each line starts with '- '."
        )
        model = await self._resolve_model()
        payload = {
            "model": model,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": headline_context},
            ],
        }
        headers = self._headers()
        try:
            data = await self._send_chat(payload=payload, headers=headers)
            content = str(data["choices"][0]["message"]["content"]).strip()
            return content
        except Exception:
            return "Top Headlines:\n- Headline curation unavailable right now."

    def _headers(self) -> dict[str, str]:
        if self._openrouter_api_key:
            return {
                "Authorization": f"Bearer {self._openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://telegram.org",
                "X-Title": "Telegram Gold News Bot",
            }
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _target_url(self) -> str:
        if self._openrouter_api_key:
            return self._openrouter_url
        return self._url

    async def _send_chat(self, payload: dict, headers: dict[str, str]) -> dict:
        target_url = self._target_url()
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(target_url, json=payload, headers=headers)
            if response.status_code == 402 and self._openrouter_api_key:
                free_model = await self._resolve_openrouter_free_model(headers=headers)
                if free_model:
                    retry_payload = dict(payload)
                    retry_payload["model"] = free_model
                    retry = await client.post(target_url, json=retry_payload, headers=headers)
                    retry.raise_for_status()
                    return retry.json()
            response.raise_for_status()
            return response.json()

    async def _resolve_openrouter_free_model(self, headers: dict[str, str]) -> str | None:
        if self._resolved_openrouter_free_model:
            return self._resolved_openrouter_free_model
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(self._openrouter_models_url, headers=headers)
                response.raise_for_status()
                data = response.json()
            for item in data.get("data", []):
                model_id = str(item.get("id") or "")
                if model_id.endswith(":free"):
                    self._resolved_openrouter_free_model = model_id
                    return model_id
        except Exception:
            return None
        return None
