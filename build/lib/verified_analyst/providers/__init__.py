from .base import Provider, ToolCall, Turn, Usage


def make_provider(spec: str, model: str | None = None):
    """spec: 'anthropic' | 'openai' (any OpenAI-compatible endpoint)."""
    if spec == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model)
    if spec in ("openai", "openrouter"):
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(model)
    raise ValueError(f"unknown provider {spec!r}")


__all__ = ["Provider", "ToolCall", "Turn", "Usage", "make_provider"]
