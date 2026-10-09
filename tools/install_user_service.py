#!/usr/bin/env python3
"""Siegfried User Service & Binary Installer (Sudo-free).

Reproducibly installs:
1. Executable wrapper entrypoints into ~/.siegfried/bin/
2. User systemd service unit into ~/.config/systemd/user/siegfried.service
3. Validates unit syntax with systemd-analyze verify (non-root)

Zero root/sudo privileges required.
Supports isolated temporary home directory installation via --home.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def install_user_service(
    target_home: Path | None = None,
    dry_run: bool = False,
    verify: bool = True,
) -> int:
    home_dir = Path(target_home or Path.home()).resolve()
    siegfried_base = home_dir / ".siegfried"
    bin_dir = siegfried_base / "bin"
    systemd_user_dir = home_dir / ".config" / "systemd" / "user"
    service_src = REPO_ROOT / "systemd" / "siegfried.service"
    service_dst = systemd_user_dir / "siegfried.service"

    print(f"[INSTALL] Target HOME: {home_dir}")
    print(f"[INSTALL] Siegfried base: {siegfried_base}")
    print(f"[INSTALL] Executables dir: {bin_dir}")
    print(f"[INSTALL] Systemd user dir: {systemd_user_dir}")

    if dry_run:
        print("[INSTALL] Modo dry-run activado: no se escribirán archivos en disco.")
        return 0

    # 1. Create target directories with 0700 permissions
    old_umask = os.umask(0o077)
    try:
        bin_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        systemd_user_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    finally:
        os.umask(old_umask)

    # 2. Install executable binaries (siegfried and siegfried-daemon)
    for bin_name in ["siegfried", "siegfried-daemon"]:
        src_bin = REPO_ROOT / "bin" / bin_name
        dst_bin = bin_dir / bin_name
        if src_bin.exists():
            # Copy content
            content = src_bin.read_text(encoding="utf-8")
            dst_bin.write_text(content, encoding="utf-8")
            os.chmod(dst_bin, 0o755)
            print(f"[INSTALL] Instalado ejecutable: {dst_bin}")
        else:
            print(f"[WARN] No se encontró ejecutable origen: {src_bin}", file=sys.stderr)

    # 3. Install systemd user service unit
    if service_src.exists():
        content = service_src.read_text(encoding="utf-8")
        service_dst.write_text(content, encoding="utf-8")
        os.chmod(service_dst, 0o644)
        print(f"[INSTALL] Instalada unidad systemd: {service_dst}")
    else:
        print(f"[ERROR] No se encontró archivo de unidad: {service_src}", file=sys.stderr)
        return 1

    # 4. Verification with systemd-analyze if requested and available
    if verify:
        analyze_path = shutil.which("systemd-analyze")
        if analyze_path:
            env = os.environ.copy()
            env["HOME"] = str(home_dir)
            try:
                proc = subprocess.run(
                    [analyze_path, "verify", str(service_dst)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                if proc.returncode == 0:
                    print("[VERIFY] systemd-analyze verify: UNIDAD VÁLIDA (PASS)")
                else:
                    print(f"[VERIFY] Advertencias/Errores de verificación:\n{proc.stderr.strip()}", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] No se pudo ejecutar systemd-analyze verify: {e}", file=sys.stderr)
        else:
            print("[INFO] systemd-analyze no disponible en PATH.")

    print("\n[SUCCESS] Instalación completada con éxito.")
    print("Para activar el servicio en su entorno real (sin sudo):")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now siegfried.service")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalador reproducible del servicio de usuario Siegfried")
    parser.add_argument("--home", type=Path, default=None, help="Directorio HOME destino (default: actual del usuario)")
    parser.add_argument("--dry-run", action="store_true", help="Simular sin escribir en disco")
    parser.add_argument("--no-verify", action="store_true", help="Omitir systemd-analyze verify")
    args = parser.parse_args()

    return install_user_service(
        target_home=args.home,
        dry_run=args.dry_run,
        verify=not args.no_verify,
    )


if __name__ == "__main__":
    sys.exit(main())
