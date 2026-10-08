"""CLI REPL loop and user interaction."""

import sys
from siegfried.contracts.ipc import IPCCommand, IPCStatus
from siegfried.ipc.client import IPCClient
from siegfried.cli.router import CommandRouter
from siegfried.core.errors import IPCCommunicationError


class SiegfriedREPL:
    """Interactive command shell for Siegfried."""

    def __init__(self, client: IPCClient) -> None:
        self.client = client
        self.router = CommandRouter()

    def run(self) -> None:
        """Main REPL loop."""
        print("Siegfried v1.0 — A su servicio, Señor.")
        print("Escriba 'salir', 'exit' o Ctrl+D para terminar.\n")

        while True:
            try:
                line = input("[Siegfried] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nHasta luego, Señor.")
                break

            if not line:
                continue

            if line.lower() in ("salir", "exit", "quit"):
                print("Hasta luego, Señor.")
                break

            # Route input through Fast-Path router
            match = self.router.route(line)
            if match.is_fast_path and match.command:
                try:
                    resp = self.client.call(match.command, match.args)
                    if resp.status == IPCStatus.OK.value:
                        msg = resp.payload.get("message") or resp.payload
                        print(f"Siegfried: {msg}")
                    elif resp.status == IPCStatus.REJECTED.value:
                        print(f"Siegfried [Aviso]: Solicitud rechazada — {resp.error_msg}")
                    else:
                        print(f"Siegfried [Error]: {resp.error_msg}")
                except IPCCommunicationError as e:
                    print(f"Siegfried: No se pudo conectar con el daemon en segundo plano ({e}).")
            elif match.is_fast_path and match.direct_response is not None:
                if match.direct_response:
                    print(f"Siegfried: {match.direct_response}")
            else:
                # Placeholder for Deep-Path (LLM)
                print("Siegfried: Vía cognitiva requerida (Modo Inferencia aún no activo en Fase 0/1).")
