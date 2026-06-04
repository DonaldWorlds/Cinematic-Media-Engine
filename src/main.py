"""Alternate entry point: ``python -m src.main``."""

from __future__ import annotations

from .cli import app


def main() -> None:
    """Run the Typer CLI app."""
    app()


if __name__ == "__main__":
    main()
