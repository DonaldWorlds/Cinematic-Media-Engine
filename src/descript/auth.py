"""DESCRIPT_API_KEY loading for the Descript handoff (Phase 9)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

__all__ = ["load_descript_token"]

_TOKEN_ENV = "DESCRIPT_API_KEY"


def load_descript_token() -> str:
    """Return the Descript bearer token from the environment.

    Loads ``.env`` without overriding values already in the process environment, then
    reads ``DESCRIPT_API_KEY``. Raises with setup instructions if absent/blank.
    """
    load_dotenv(override=False)
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"{_TOKEN_ENV} not set. Add it to .env (or your environment). "
            "Create a token in Descript: Settings > API tokens (shown once at creation)."
        )
    return token
