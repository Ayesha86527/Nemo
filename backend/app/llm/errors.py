"""Error taxonomy for LLM providers.

Every error carries a user-facing `hint` so callers can surface actionable
recovery guidance (e.g. check the base URL, fix the API key, retry later).
"""


class LLMError(Exception):
    """Base class for all provider failures."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class ProviderUnavailableError(LLMError):
    """The provider endpoint cannot be reached (network, process down)."""


class ModelNotLoadedError(LLMError):
    """The requested model is not installed/loaded on the provider."""


class InferenceTimeoutError(LLMError):
    """The provider accepted the request but inference timed out.

    Common on constrained hardware with large models.
    """


class ResourceExhaustedError(LLMError):
    """The provider ran out of memory/compute during inference."""


class AuthenticationError(LLMError):
    """The provider rejected the API key (401/403)."""


class ProviderConfigError(LLMError):
    """The provider is misconfigured (missing key, unknown provider)."""
