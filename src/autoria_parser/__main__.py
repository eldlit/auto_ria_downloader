"""Module entry point (`python -m autoria_parser`)."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

from .app import run
from .cli import parse_args
from .logging import setup_logging


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    setup_logging(args.log_level)
    logging.getLogger(__name__).debug("Starting Autoria parser")
    try:
        asyncio.run(run(args.config, args.input, dry_run=args.dry_run))
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Interrupted by user")


if __name__ == "__main__":  # pragma: no cover
    main()
