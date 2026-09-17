"""Chybové typy sdílené napříč agentem."""


class IgAgentError(Exception):
    """Základ pro všechny chyby agenta."""


class ConfigError(IgAgentError):
    """Chybějící nebo neplatná konfigurace."""


class GraphAPIError(IgAgentError):
    """Instagram Graph API vrátilo chybu."""

    def __init__(self, message, *, status=None, code=None, subcode=None, fbtrace_id=None, payload=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id
        self.payload = payload or {}

    @property
    def is_rate_limit(self):
        # 4 = app-level throttling, 17 = user-level, 32 = page-level, 613 = custom rate limit
        return self.code in (4, 17, 32, 613) or self.status == 429

    @property
    def is_transient(self):
        # 1/2 = neznámá/dočasná chyba na straně Meta, 5xx = server
        return self.is_rate_limit or self.code in (1, 2) or (self.status or 0) >= 500

    def __str__(self):
        base = super().__str__()
        bits = [b for b in (
            f"status={self.status}" if self.status else None,
            f"code={self.code}" if self.code else None,
            f"subcode={self.subcode}" if self.subcode else None,
            f"trace={self.fbtrace_id}" if self.fbtrace_id else None,
        ) if b]
        return f"{base} ({', '.join(bits)})" if bits else base


class MediaError(IgAgentError):
    """Selhalo generování nebo zpracování média."""


class BrainError(IgAgentError):
    """Selhalo volání Claude nebo je odpověď nepoužitelná."""


class PublishBlocked(IgAgentError):
    """Publikace zastavena bezpečnostní pojistkou (limit, schvalování, dry-run)."""
