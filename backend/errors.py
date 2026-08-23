"""Top of the exception hierarchy for WebMedic.

Every WebMedic-specific failure raised from the extraction, healer, and
versioning layers inherits from :class:`WebMedicError`.

:class:`~backend.acquisition.base.ProviderUnavailable` is re-exported from
here for convenience but inherits from :class:`RuntimeError` — its base is
kept unchanged for backward compatibility with existing call sites. Code
that wants to catch every WebMedic failure with a single ``except`` clause
can catch the :data:`WEBMEDIC_ERRORS` tuple::

    from backend.errors import WEBMEDIC_ERRORS
    try:
        ...
    except WEBMEDIC_ERRORS as exc:
        log.warning("webmedic failure: %s", exc)

Public API:
    - WebMedicError        -- base class for WebMedic-specific errors.
    - ExtractionFailed     -- extraction returned no records for a
                              well-formed strategy on a fetched page.
    - NoValidCandidates    -- the healer produced no candidate that
                              survived the deployment gate.
    - ProviderUnavailable  -- re-exported from
                              backend.acquisition.base for convenience.
    - WEBMEDIC_ERRORS      -- tuple of every WebMedic-owned error class,
                              suitable for a single ``except`` clause.
"""

from __future__ import annotations


class WebMedicError(Exception):
    """Base class for every WebMedic-specific error.

    Prefer raising a subclass. Callers may catch this base for
    log-and-continue behaviour when a scraper failure is expected and
    survivable (e.g. a fixture-only fallback path).
    """


class ExtractionFailed(WebMedicError):
    """Raised when extraction returns no records for a fetched page.

    Distinct from :class:`ProviderUnavailable`: the fetch succeeded but the
    strategy could not produce any records. This is the trigger the healer
    listens for.
    """


class NoValidCandidates(WebMedicError):
    """Raised when the healer generated candidates but none passed the gate.

    Emitted by :mod:`backend.healer.repair` after every candidate is scored,
    shadow-run, and rejected. The deploy layer treats this as an audited
    ``reject`` event, not a crash.
    """


# Re-export ProviderUnavailable so `from backend.errors import ...` reaches
# every WebMedic error from one place. Imported at the bottom to avoid a
# circular import at module-load time.
from backend.acquisition.base import ProviderUnavailable  # noqa: E402

WEBMEDIC_ERRORS: tuple[type[Exception], ...] = (
    WebMedicError,
    ProviderUnavailable,
)

__all__ = [
    "WEBMEDIC_ERRORS",
    "ExtractionFailed",
    "NoValidCandidates",
    "ProviderUnavailable",
    "WebMedicError",
]
