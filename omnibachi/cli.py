"""
cli.py — Entrypoint for the omnibachi CLI.

Delegates entirely to cli_adapter.main().
"""

from omnibachi.implementation.ingress.cli.cli_adapter import main

if __name__ == "__main__":
    main()
