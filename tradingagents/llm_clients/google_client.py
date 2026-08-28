from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

from .base_client import BaseLLMClient, normalize_content
from .retry import call_with_rate_limit_retry
from .validators import validate_model


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        parent_invoke = super().invoke
        return normalize_content(
            call_with_rate_limit_retry(
                lambda: parent_invoke(input, config, **kwargs),
                description=self.model,
            )
        )


# Model families that reject thinking_level="minimal" (matched on the model id).
_NO_MINIMAL_THINKING = ("pro", "3.7-flash")


def _supports_minimal_thinking(model: str) -> bool:
    """True when the model accepts thinking_level="minimal"."""
    lowered = model.lower()
    return not any(marker in lowered for marker in _NO_MINIMAL_THINKING)


class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatGoogleGenerativeAI instance."""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in ("timeout", "max_retries", "temperature", "callbacks", "http_client", "http_async_client", "rate_limiter"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Unified api_key maps to provider-specific google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key")
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        # Gemini 3.x takes the string ``thinking_level`` (the integer
        # ``thinking_budget`` was for the now-retired 2.5 line). Not every
        # model accepts every level: Pro and 3.7 Flash reject "minimal" with a
        # 400 INVALID_ARGUMENT ("Thinking level MINIMAL is not supported for
        # this model"), while 3.1 Flash / Flash-Lite accept it. Map an
        # unsupported "minimal" to the nearest level the model does accept.
        thinking_level = self.kwargs.get("thinking_level")
        if thinking_level:
            if thinking_level == "minimal" and not _supports_minimal_thinking(self.model):
                thinking_level = "low"
            llm_kwargs["thinking_level"] = thinking_level

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
