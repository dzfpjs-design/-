"""Server-owned allowlist for outbound cloud model calls."""
from .config import settings


def provider_enabled(provider):
    raw = settings().cloud_allowed_providers
    if not isinstance(raw, str):
        return False
    choices = [item.strip() for item in raw.split(',')]
    if not choices or any(item not in {'qwen', 'deepseek'} for item in choices):
        return False
    return provider in choices
