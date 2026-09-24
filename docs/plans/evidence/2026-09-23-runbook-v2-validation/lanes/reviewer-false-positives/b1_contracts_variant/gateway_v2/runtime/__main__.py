"""CLI: python -m gateway_v2.runtime [--json|--serve]."""

from gateway_v2.runtime.lifecycle import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
