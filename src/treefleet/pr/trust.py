"""Who may ask for a revision. Both pollers call this and nothing else."""

from __future__ import annotations

from treefleet.pr.config import PrSettings

UNRESOLVED = "authenticated login could not be resolved"


def provider_login(provider: object) -> str | None:
    """The provider's login. None means it has no user lookup. Empty means the lookup failed."""
    user = getattr(provider, "user", None)
    if not callable(user):
        return None
    try:
        text = str(user() or "").strip()
    except Exception:
        return ""
    if not text or any(char.isspace() for char in text) or "/" in text:
        return ""
    return text


def trusted_logins(settings: PrSettings, provider: object, *, fallback: str = "") -> tuple[set[str], str]:
    """Trusted logins, and a reason when the set would otherwise be empty.

    An empty trusted_commenters list means the authenticated login. A failed lookup
    does not fall back to trusting nobody.
    """
    named = {item.strip() for item in settings.trusted_commenters if item and item.strip()}
    if named:
        return named, ""
    login = provider_login(provider)
    if login is None:
        login = fallback.strip()
    if not login:
        return set(), UNRESOLVED
    return {login}, ""
