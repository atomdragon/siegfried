"""CLI package exposing app entrypoint, REPL and router."""

from siegfried.cli.app import run_cli

__all__ = [
    "CommandRouter",
    "RouteMatch",
    "SiegfriedREPL",
    "run_cli",
]


def __getattr__(name: str):
    if name in ("CommandRouter", "RouteMatch"):
        from siegfried.cli.router import CommandRouter, RouteMatch
        globals()["CommandRouter"] = CommandRouter
        globals()["RouteMatch"] = RouteMatch
        return globals()[name]
    if name == "SiegfriedREPL":
        from siegfried.cli.repl import SiegfriedREPL
        globals()["SiegfriedREPL"] = SiegfriedREPL
        return SiegfriedREPL
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

