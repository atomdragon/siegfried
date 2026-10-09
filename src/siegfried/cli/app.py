"""CLI application entrypoint logic and direct command execution."""

import argparse
import sys
from typing import Any


def _format_runtime_path(base_dir: Any) -> str:
    from pathlib import Path
    try:
        home = Path.home().resolve()
        resolved = Path(base_dir).resolve()
        if resolved == home / ".siegfried":
            return "~/.siegfried/"
        if resolved.is_relative_to(home):
            rel = resolved.relative_to(home)
            return f"~/{rel}/"
    except (ValueError, RuntimeError):
        pass
    path_str = str(base_dir)
    return path_str if path_str.endswith("/") else f"{path_str}/"


def _format_relative_path(path: Any, base_dir: Any) -> str:
    from pathlib import Path
    try:
        p = Path(path).resolve()
        b = Path(base_dir).resolve()
        if p == b:
            return "."
        return str(p.relative_to(b))
    except (ValueError, RuntimeError):
        return str(path)


class _FastArgparseTheme:
    def __getattr__(self, name: str) -> str:
        return ""


class FastHelpFormatter(argparse.HelpFormatter):
    """Zero-overhead HelpFormatter avoiding heavy dynamic imports of _colorize, shutil, and compression."""

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 24,
        width: int | None = None,
        color: bool = False,
    ) -> None:
        if width is None:
            try:
                import os
                width = os.get_terminal_size().columns - 2
            except OSError:
                width = 78
        super().__init__(prog, indent_increment, max_help_position, width, color=False)

    def _set_color(self, color: bool) -> None:
        self._theme = _FastArgparseTheme()
        self._decolor = getattr(argparse, "_identity", lambda s: s)


def run_cli(args: list[str] | None = None, paths: Any = None) -> int:
    """CLI main execution dispatcher."""
    parser = argparse.ArgumentParser(
        prog="siegfried",
        description="Siegfried — Asistente Personal y Mayordomo Estratégico",
        formatter_class=FastHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    def _add_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        kwargs.setdefault("formatter_class", FastHelpFormatter)
        return subparsers.add_parser(name, **kwargs)

    # init
    _add_parser("init", help="Inicializar runtime seguro del usuario")

    # doctor
    _add_parser("doctor", help="Diagnóstico no destructivo del runtime y configuración")

    # ping
    _add_parser("ping", help="Verificar conexión con el daemon")

    # status
    _add_parser("status", help="Consultar estado actual de enfoque y postura")

    # focus
    focus_p = _add_parser("focus", help="Iniciar bloque de enfoque")
    focus_p.add_argument("duration", type=int, nargs="?", default=25, help="Duración en minutos (default 25)")
    focus_p.add_argument("-t", "--task", type=str, default="Bloque General", help="Nombre de la tarea")

    # cancel
    _add_parser("cancel", help="Cancelar bloque activo")

    # ack
    _add_parser("ack", help="Silenciar alarma y confirmar descanso")

    # ask
    ask_p = _add_parser("ask", help="Consultar a Siegfried por la vía cognitiva")
    ask_p.add_argument("prompt", type=str, nargs="+", help="Texto de la consulta")
    ask_p.add_argument(
        "--policy",
        type=str,
        choices=["CLOUD_PREFERRED", "LOCAL_PREFERRED", "LOCAL_ONLY", "CLOUD_ONLY"],
        default=None,
        help="Política de inferencia",
    )

    parsed = parser.parse_args(args)


    if parsed.command == "init":
        from siegfried.storage.paths import default_paths
        from siegfried.storage.initialization import ensure_user_runtime
        from siegfried.core.errors import StorageError

        active_paths = paths or default_paths
        try:
            result = ensure_user_runtime(active_paths)
            if result.created:
                display_path = _format_runtime_path(result.base_dir)
                print(f"[Siegfried] Runtime inicializado correctamente.\nRuta: {display_path}\nEstado: {result.status}")
            else:
                print(f"[Siegfried] Runtime existente verificado.\nEstado: {result.status}")
            return 0
        except StorageError as e:
            print(f"[ERROR] Error al inicializar runtime: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[ERROR] Error inesperado al inicializar runtime: {e}", file=sys.stderr)
            return 1

    if parsed.command == "doctor":
        from siegfried.storage.paths import default_paths
        from siegfried.storage.validation import validate_user_runtime

        active_paths = paths or default_paths
        result = validate_user_runtime(active_paths, deep_vault_audit=True)

        config_status = result.sections.get("Configuración", "OK")
        perm_status = result.sections.get("Permisos", "OK")
        vault_status = result.sections.get("Vault", "OK")
        rutas_status = result.sections.get("Rutas", "OK")

        print("[Siegfried] Diagnóstico del runtime\n")
        print(f"Configuración: {config_status}")
        if result.problematic_path and result.problematic_path != active_paths.base_dir and config_status != "OK":
            rel_path = _format_relative_path(result.problematic_path, active_paths.base_dir)
            print(f"Archivo: {rel_path}")
        print(f"Permisos: {perm_status}")
        if result.problematic_path and result.problematic_path != active_paths.base_dir and perm_status != "OK" and config_status == "OK":
            rel_path = _format_relative_path(result.problematic_path, active_paths.base_dir)
            print(f"Archivo: {rel_path}")
        print(f"Vault: {vault_status}")
        if result.problematic_path and result.problematic_path != active_paths.base_dir and vault_status != "OK" and config_status == "OK" and perm_status == "OK":
            rel_path = _format_relative_path(result.problematic_path, active_paths.base_dir)
            print(f"Archivo: {rel_path}")
        print(f"Rutas: {rutas_status}")
        if result.problematic_path and result.problematic_path != active_paths.base_dir and rutas_status != "OK" and config_status == "OK" and perm_status == "OK" and vault_status == "OK":
            rel_path = _format_relative_path(result.problematic_path, active_paths.base_dir)
            print(f"Archivo: {rel_path}")
        print()

        if result.is_ready:
            print("Estado: READY")
            return 0
        else:
            print("Estado: NOT_READY")
            return 1

    from siegfried.contracts.ipc import IPCCommand, IPCStatus
    from siegfried.storage.paths import default_paths
    from siegfried.ipc.client import IPCClient
    from siegfried.core.errors import IPCCommunicationError

    paths = paths or default_paths
    client = IPCClient(paths.socket_file)

    if parsed.command is None:
        # Modo interactivo REPL
        from siegfried.cli.repl import SiegfriedREPL
        repl = SiegfriedREPL(client, paths=paths)
        repl.run()
        return 0

    try:
        if parsed.command == "ping":
            res = client.call(IPCCommand.PING)
        elif parsed.command == "status":
            res = client.call(IPCCommand.STATUS)
        elif parsed.command == "focus":
            res = client.call(IPCCommand.START_FOCUS, {"duration_min": parsed.duration, "task": parsed.task})
        elif parsed.command == "cancel":
            res = client.call(IPCCommand.CANCEL_FOCUS)
        elif parsed.command == "ack":
            res = client.call(IPCCommand.ACK_BREAK)
        elif parsed.command == "ask":
            full_prompt = " ".join(parsed.prompt)
            args_payload = {"prompt": full_prompt}
            if parsed.policy:
                args_payload["policy"] = parsed.policy
            from siegfried.cli.router import CommandRouter
            r_match = CommandRouter().route(full_prompt)
            if r_match.args.get("is_historical"):
                args_payload["is_historical"] = True
                args_payload["time_window"] = r_match.args.get("time_window", "today")
            res = client.call(IPCCommand.QUERY, args_payload, timeout_seconds=15.0)
            if res.status == IPCStatus.OK.value:
                content = res.payload.get("response", "")
                route = res.payload.get("route_used", "")
                fallback = res.payload.get("fallback_used", False)
                tag = f" [{route}]" if route else ""
                if fallback:
                    tag += " (fallback)"
                print(f"Siegfried{tag}: {content}")
                return 0
            elif res.status == IPCStatus.REJECTED.value:
                print(f"[REJECTED] {res.error_msg}", file=sys.stderr)
                return 1
            else:
                print(f"[ERROR] {res.error_msg}", file=sys.stderr)
                return 1
        else:
            parser.print_help()
            return 1


        if res.status == IPCStatus.OK.value:
            print(f"[OK] {res.payload}")
            return 0
        elif res.status == IPCStatus.REJECTED.value:
            print(f"[REJECTED] {res.error_msg}")
            return 1
        else:
            print(f"[ERROR] {res.error_msg}")
            return 1

    except IPCCommunicationError as e:
        print(f"Error de comunicación con el daemon: {e}", file=sys.stderr)
        return 2
