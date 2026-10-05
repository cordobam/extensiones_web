"""Permite `python -m radar ingest` y `python -m radar match`.

Es una línea que llama a `radar.cli.main`. Está separado para que el comando
`radar` (el entry point de pyproject) y `python -m radar` sean lo mismo.
"""

from radar.cli import main

raise SystemExit(main())
