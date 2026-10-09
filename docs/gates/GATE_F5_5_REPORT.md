# SIEGFRIED v1.0 — GATE F5.5

Fecha: 2026-10-09. Actualización: ocho directorios corregidos con dos autorizaciones explícitas; ambos instaladores pasan en dry-run. Verificación UTC: 2026-10-09T18:01:48.140017+00:00.

**Veredicto: BLOCKED.** Despliegue privado corregido y probado; no se ha instalado ni activado el piloto en HOME real. Permisos superados. Faltan autorización de inicialización del runtime, instalación, arranque manual, clic físico y login. No se certifica uso diario todavía. La desviación de RSS observada se documenta separadamente y no se disimula cambiando MB por MiB.

## 1. Estado inicial

Repositorio `/media/okami/Mio/Siegfried`; rama `main`; HEAD `8011f64e771813b1ae1b5fbdeafdf6abef9ece0b`. Árbol y staging inicialmente limpios, sin archivos sin seguimiento. No se ejecutaron commits, push, reset ni movimientos del checkout. No se encontraron instrucciones AGENTS aplicables.

Se revisaron PLAN, SPECIFICATION, README y reportes F5.3, F5.3.1 y F5.4. HEAD incorpora los ocho archivos del cierre F5.4; las tres correcciones reportadas están presentes. `git diff c5fc7cdd99dc37b461ba21942b32efb38a34819c HEAD -- schemas src/siegfried/contracts` vacío. El baseline certificado es 644 pruebas; las 644 siguen incluidas en la regresión final.

## 2. Diagnóstico de permisos

UID/GID 1000/1000, `okami:okami`. HOME 0750. `id`, `stat`, `namei`, `getfacl` y consulta del grupo efectuados en lectura. En el inventario original y el precheck anterior al chmod, las cinco rutas no eran symlinks y sus ACL sólo contenían `user::rwx`, `group::rwx`, `other::r-x`, sin entradas nominativas, máscara ni ACL default. El grupo okami no anuncia miembros suplementarios; esto no demuestra que ninguna automatización necesite escritura compartida.

| Ruta | Original | Actual verificado tras autorización | Efecto |
|---|---:|---:|---|
| `~/.config` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.config/autostart` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.local` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.local/share` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.siegfried` | 0775 | 0700 | Acceso exclusivo del propietario |
| `~/.config/systemd` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.config/systemd/user` | 0775 | 0755 | Quitar escritura de grupo |
| `~/.siegfried/data` | 0775 | 0700 | Acceso privado y modo requerido por el validador |

Las aplicaciones ejecutadas con el mismo UID conservan acceso. Los cuatro padres 0755 son compatibles con los controles del instalador, probado en fixtures anteriores y preservado en la regresión. No se cambia HOME ni el checkout, cuyo ancestro `/media/okami` está en 0777 y raíz en 0775. Se rechaza ejecución persistente desde esa fuente compartida.

**Tres hallazgos adicionales resueltos con autorización posterior:** `.config/systemd` y `.config/systemd/user`, 0775 → 0755; `.siegfried/data`, 0775 → 0700. Precheck de directorios reales, propiedad del usuario y ausencia de symlinks en rutas/ancestros o ACL adicionales PASS. `stat` y `getfacl` posteriores PASS; propietario, grupo e inodos conservados. No hay otro bloqueo de permisos entre las rutas requeridas actualmente existentes.

## 3. Cambios autorizados

El usuario autorizó exclusivamente los dos comandos de chmod de las cinco rutas iniciales. Se repitió la comprobación de tipo real, propietario, grupo, ausencia de symlinks en ruta/ancestros y ACL básicas antes de ejecutarlos. Aplicados exactamente: cuatro rutas 0775 → 0755 y `.siegfried` 0775 → 0700. Verificación inmediata con `stat` y `getfacl` PASS; propietario, grupo e inodos conservados. Las ACL básicas ahora reflejan los nuevos modos. Sin cambios de propietarios, ACL adicionales, archivos internos ni otros directorios; sin inicialización, instalación o activación real. Las excepciones anteriores al sandbox sólo autorizaron consultas/pruebas aisladas.

En una segunda autorización exclusiva se ejecutaron `chmod 755 "$HOME/.config/systemd" "$HOME/.config/systemd/user"` y `chmod 700 "$HOME/.siegfried/data"`. Los tres originales 0775 y ACL básicas se registraron antes de actuar en el manifiesto; postcheck conservó los inodos y propietarios. No se usó chmod recursivo ni se alteraron archivos internos u otros permisos.

Corrección controlada del defecto ya señalado por F5.4: el instalador antiguo copiaba wrappers sin las fuentes que importaban. Se reutilizan las funciones de snapshot existentes con una selección `daemon=True`, un paquete independiente y un marcador F5.5. El paquete normal F5.3 conserva su selección original. No se modificaron núcleo, inferencia, IPC, timers, agenda ni contratos.

## 4. Estado del runtime real

Inspección de metadatos de nombres oficiales, sin mostrar ni leer historial, credenciales o Vault. Existe `.siegfried` y `data/.history` previo, propiedad del usuario, historial 0600. Faltan `config`, `assets`, `sounds`, `logs`, `secrets.env`, `core_profile.json`, `active_agenda.json`, lock de agenda y Vault. El historial debe conservarse. No se inicializó ni reparó ningún archivo.

`python3 -S bin/siegfried doctor` retornó 1: `Rutas: NOT_INITIALIZED`, falta `config`, `Estado: NOT_READY`. Este resultado termina en las comprobaciones de existencia: sus líneas por defecto `Configuración/Permisos/Vault: OK` **no prueban validez de esas secciones**. La repetición después de corregir los ocho permisos confirma el mismo estado: `doctor` exit 1, `NOT_INITIALIZED`, falta `config`. La inspección de metadatos de los nombres oficiales está completada: `.siegfried` y `data` 0700, historial regular 0600/UID 1000, sin symlink ni hardlink. Sin locks ni temporales huérfanos entre los directorios existentes de configuración/datos. No hay configuración, secretos o Vault que validar todavía; no se inicializó el runtime. Configuración y Vault ausentes no equivalen a corrupción. No hay datos suficientes para declarar READY.

No existe socket, PID de llama ni marcador de briefing en `/run/user/1000` en el inventario. No se retiró lock ni resto alguno. Inicialización oficial propuesta, con autorización independiente y sólo después de resolver permisos y revisar el historial:

```bash
SIEGFRIED_HOME="$HOME/.siegfried" /usr/bin/python3 -B /media/okami/Mio/Siegfried/bin/siegfried init
SIEGFRIED_HOME="$HOME/.siegfried" /usr/bin/python3 -B /media/okami/Mio/Siegfried/bin/siegfried doctor
```

**Propuestos, no ejecutados.** El primer comando administrativo puntual de la CLI oficial crearía los elementos ausentes con modos privados y locks propios. Conserva el historial existente; el segundo realizaría la auditoría completa del runtime. No instalan unidades o snapshots ni arrancan un daemon. La ejecución persistente posterior usará únicamente el código privado aprobado. Los 61 hashes de fuentes y hashes de instaladores/unidad siguen iguales a la evidencia revisada; no se requiere repetir las auditorías de permisos superadas. Antes de `init`, limitar la comprobación a que los archivos de destino siguen ausentes y el historial conserva tipo/propiedad/permisos.

Ensayo adicional permitido: CLI oficial init/doctor sobre runtime temporal sintético incompleto con historial no vacío. Ambos exit 0; bytes, inodo y 0600 del historial conservados. Fixture retirado al terminar. No se leyó ni escribió el historial real.

## 5. Snapshot privado

Dos artefactos independientes:

- `.local/share/siegfried-boot`: selección original, 57 fuentes, marcador F5.3.
- `.local/share/siegfried-daemon`: 61 fuentes, marcador F5.5; añade entrypoint del daemon y tres recursos explícitos de KWin a la misma base Python.

Fuentes Python, wrappers, hook y recursos KWin permitidos explícitamente. Sin `.git`, tests, `__pycache__`, secretos, agenda, Vault, historial, GGUF, logs ni temporales. Directorios 0700, Python/recursos/marcador 0600, entrypoints 0700. No hay copias indiscriminadas del repositorio ni instalación real de KPackage. No se carga ningún script KWin al preparar el paquete.

Los modelos no forman parte de la instalación. `LlamaLifecycleManager` recibe `model_path=None` desde el daemon actual; un motor local sin modelo configurado queda indisponible. No se inventa una ruta a GGUF ni se añade configuración a los contratos. El sonido opcional se resuelve desde `.siegfried/assets/sounds/leaver.ogg`, sin copiar audio personal.

## 6. Integridad

Comparación de cada fuente y snapshot temporal byte a byte y SHA-256 PASS. Los 61 hashes están en [GATE_F5_5_EVIDENCE.json](GATE_F5_5_EVIDENCE.json). El manifiesto [GATE_F5_5_RECOVERY.json](GATE_F5_5_RECOVERY.json) registra HEAD, metadatos, ACL, ausencia de destinos y hashes originales de los entrypoints, hook, instaladores y unidad desde HEAD.

Escaneo limitado de patrones de claves privadas y tokens largos en las fuentes desplegables: cero coincidencias. No es una prueba universal de ausencia de secretos. No se imprimió contenido privado. El snapshot protege modificaciones posteriores; los hashes locales y la comparación con Git no autentican por sí solos una fuente compartida frente a un actor con autoridad para sustituirla antes de la auditoría.

## 7. Instalación del daemon

`tools/install_user_service.py` simula por defecto en CLI; `--apply` escribe explícitamente. Instala snapshot independiente, wrappers 0700 y unidad 0600. Rechaza symlinks, padres compartidos, archivos ajenos/modificados, hardlinks y contenido diferente. Publicación de archivos sin sobrescritura mediante enlace atómico de un temporal propio. Reinstalar el mismo contenido no cambia sus inodos.

Unidad: `ExecStart=/usr/bin/python3 -B "%h/.local/share/siegfried-daemon/bin/siegfried-daemon"`; sin referencia ejecutable al checkout. Se preserva hardening, límites de reinicio y EX_CONFIG. `PYTHONDONTWRITEBYTECODE=1` impide caches de código en el paquete. Los wrappers de `.siegfried/bin` resuelven sólo el paquete privado relativo a la ubicación instalada.

`systemd-analyze --user verify` PASS en HOME temporal. Para esta comprobación se sustituye únicamente `%h` por el HOME del fixture, porque systemd usa el HOME real de la cuenta aun cambiando la variable de entorno. Esto valida sintaxis/rutas ejecutables del fixture, **no demuestra que el sandbox de la unidad pueda arrancar en el sistema real**. La disponibilidad de namespaces y el arranque real siguen pendientes. Fallo de verify se devuelve como fallo, sin anunciar éxito; snapshot parcial recuperable mediante retry o uninstall.

## 8. Instalación del briefing

Instalación, integridad, `desktop-file-validate`, idempotencia, disable/enable y rollback temporales PASS en la regresión. Entrada y snapshots reales siguen ausentes. Después de corregir los ocho permisos, `python3 -B tools/install_user_service.py --dry-run` y `python3 -B tools/install_boot_briefing.py --dry-run` retornan **0**, sin escrituras. Los rechazos anteriores de permisos están resueltos. El dry-run del daemon comprueba preflight/fuentes, pero no sustituye la verificación real de publicación o de la unidad, ya probadas en fixtures. Desinstalar briefing conserva el daemon; desinstalar daemon conserva briefing y datos. `install_boot_briefing.py --apply` publica Autostart habilitado: requiere autorización de la etapa 6, no debe emplearse como mera preparación de código.

Compatibilidad: ejecución comprobada con Python 3.14.4. AST de todas las fuentes desplegables aceptado con gramática Python 3.10; no equivale a ejecutar toda la suite en 3.10. Wrappers, daemon, CLI y hook ejecutados desde la instalación temporal, `cwd=/`, sin PYTHONPATH del checkout; hook/daemon existentes se prueban también con `-S`.

## 9. Pruebas REPL

Sólo runtime y código privados temporales. Doctor READY; PING/STATUS `OK`; ayuda, `status`, `ping`, entrada desconocida, `salir` y EOF terminan de forma controlada. CLI desconocida devuelve 2. El daemon permanece operativo después de cerrar REPL y después de una consulta sin Cloud/modelo local disponible; 25 PING adicionales PASS. Sin claves, Internet, modelos, GUI ni tareas reales. SIGTERM sólo al hijo creado, salida 0 y socket retirado.

La ejecución en Konsole con runtime real y observación humana no se ha realizado. La automatización por stdin no sustituye esa prueba.

## 10. Prueba visual KDE

Wayland/KDE; targets `graphical-session` y `plasma-workspace` activos; Konsole disponible. Consultas reales del bus: Notifications anuncia 13 capacidades, incluyendo `actions`; propietario único `:1.31`, UID 1000/PID 2816; `Inhibited=false`. No se cambió DND. KWin `isScriptLoaded("siegfried_focus_watcher")=false`. Esta consulta no enumera todos los alias posibles.

No se envió una nueva notificación en F5.5 ni se invocó ActionInvoked/InvokeAction. Pendientes: popup visible, texto, botón «Abrir sesión», clic físico y apertura real del REPL. Procedimiento inocuo posterior, sesión desbloqueada y observador presente:

```bash
PYTHONPATH=src timeout -s INT -k 5s 30s \
  python3 tools/verify_boot_briefing_f53.py --manual
```

Ese modo no invoca programáticamente la acción. La apertura del REPL real requiere autorización separada; no se certificará clic humano mediante D-Bus.

## 11. Activación Autostart

**No ejecutada.** Servicio real: `LoadState=not-found`, `ActiveState=inactive`, `MainPID=0`, sin FragmentPath ni UnitFileState. No hay entry de Autostart ni snapshots reales. Búsqueda limitada en autostart, unidades propias y `.local/bin`: sin candidatos adicionales con nombre Siegfried. Revisión limitada de argv del UID, sin imprimirlos: cero daemon/hook identificados. Se preserva cualquier instalación con otro nombre no identificada.

Comandos exactos propuestos por etapas, con autorizaciones distintas. Los ocho permisos están resueltos; avanzar requiere runtime READY, snapshot íntegro y ausencia de instancia/servicio previo. Las comprobaciones de permisos ya superadas se conservan como evidencia.

Preparación privada sin Autostart; no inicia servicio:

```bash
cd /media/okami/Mio/Siegfried
python3 tools/install_user_service.py --dry-run
python3 tools/install_user_service.py --apply
python3 -B - <<'PY'
from pathlib import Path
from tools.install_boot_briefing import _validate_home, prepare_package, package_sources
home = _validate_home(Path.home())
package = prepare_package(home, Path.cwd(), False)
assert all((package / name).read_bytes() == data for name, data in package_sources(Path.cwd()).items())
print('Snapshot del briefing verificado; Autostart sin publicar.')
PY
```

Después de `init`/doctor autorizado, inicio manual del daemon con autorización específica; no habilita persistencia:

```bash
SIEGFRIED_ENABLE_KWIN=0 SIEGFRIED_ENABLE_SESSION=0 \
SIEGFRIED_HOME="$HOME/.siegfried" \
  /usr/bin/python3 -B "$HOME/.local/share/siegfried-daemon/bin/siegfried-daemon"
```

Desde otra consola, sólo comandos inocuos; revisar STATUS sin volcar datos personales a informes:

```bash
/usr/bin/python3 -B "$HOME/.local/share/siegfried-daemon/bin/siegfried" ping
/usr/bin/python3 -B "$HOME/.local/share/siegfried-daemon/bin/siegfried" status
konsole --separate -e /usr/bin/python3 -B "$HOME/.local/share/siegfried-daemon/bin/siegfried"
```

Cerrar REPL con `salir`/Ctrl+D y comprobar PING. Parar únicamente el daemon foreground con Ctrl+C antes de pasar a systemd. Activación persistente, con autorización adicional, sólo después de las pruebas manuales y visuales:

```bash
systemctl --user daemon-reload
systemctl --user enable --now siegfried.service
python3 tools/install_boot_briefing.py --dry-run
python3 tools/install_boot_briefing.py --apply
desktop-file-validate "$HOME/.config/autostart/org.siegfried.BootBriefing.desktop"
systemctl --user show siegfried.service -p LoadState -p ActiveState -p MainPID -p FragmentPath -p UnitFileState
/usr/bin/python3 -B "$HOME/.local/share/siegfried-daemon/bin/siegfried" ping
```

No se habilitan adaptadores KWin/sesión implícitamente. Su piloto posterior requiere acordar uso de telemetría y asegurar ambos adaptadores antes de admitir foco. Scripts del snapshot disponibles, sin cargar ni activar automáticamente.

## 12. Prueba de login

**Pendiente.** El usuario deberá cerrar e iniciar sesión manualmente después de autorizar la activación. No se cerró sesión ni se reinició el equipo. Al regreso: contar una instancia/briefing, comprobar IPC, popup, clic físico, REPL, errores, CPU/RSS y escrituras. Si hay doble instancia, detener sólo la activación propia identificada.

No se midió login→visible. Registrar referencia gráfica de logind/targets, entrada al hook, solicitud Notify, aceptación, observación humana y apertura. Hook/imports y retorno D-Bus son hitos parciales. Los logs existentes no proporcionan necesariamente todos estos hitos; si faltan, registrarlos como no disponibles. No añadir una falsa precisión subsegundo a observaciones humanas ni certificar <2 s con cold-start.

## 13. Consumo de recursos

Dos ventanas consecutivas en el mismo daemon temporal, sin cargar ningún modelo:

| Fase | Ventana | Muestras RSS | CPU de un núcleo | RSS |
|---|---:|---:|---:|---:|
| Reposo tras PING/STATUS, antes de consulta cognitiva | 30,002 s | 7 | 0,19999 % | 29 495 296 bytes (29,50 MB) |
| Reposo tras REPL/entrada desconocida, motor indisponible | 45,003 s | 10 | 0,15554 % | 30 806 016 bytes (30,81 MB) |

RSS constante en cada ventana. El reposo inicial está dentro de 30 000 000 bytes; después de cargar la ruta cognitiva para gestionar una entrada desconocida supera el objetivo en 806 016 bytes. No hay proceso de modelo ni llamada Cloud, pero sí se intentó la ruta cognitiva sin proveedor disponible. La primera sonda independiente de 45 s observó 0,17776 % y 30 769 152 bytes, consistente con la desviación. La CPU inicial está en el límite de resolución, sin margen demostrable para certificar <0,2 % sostenido. No se cambia la unidad del objetivo a 30 MiB para obtener PASS.

El proceso observado es real en el host, pero runtime temporal, adaptadores apagados y sin servicio systemd. No certifica consumo sostenido del runtime del usuario ni de KWin/sesión. CPU calculada con delta de utime+stime / tiempo monotónico, porcentaje de un núcleo, sin dividir por todos los cores. Resolución de CPU 10 ms; muestras de RSS cada 5 s. Sin inferencia pesada, audio ni cambio de carga del host.

## 14. Recuperación y estabilidad

Suite completa conserva pruebas de timers, alertas, KWin, sesión, degradación y daemon restart/IPC. Nuevas pruebas comprueban recuperación de snapshot parcial por fallo de verificación y conservación de instalaciones/datos ajenos. No se sustituyen por nuevos ensayos físicos: KWin real, notificaciones/alertas reales, desbloqueo, suspensión, reinicio systemd y red real siguen pendientes. No se provocó suspensión, bloqueo ni alteración de tareas reales.

## 15. Rollback

[Manifiesto de recuperación](GATE_F5_5_RECOVERY.json): rutas, propietarios, permisos, ACL, hashes de origen y destinos ausentes. No existe backup de contenido privado ni se precisa restaurarlo porque no se modificó. No es una copia de seguridad del usuario.

Rollback futuro propuesto, **no ejecutado contra HOME real**. Comprobar antes que unidad, entry y paquetes coinciden con los propios registrados y que fueron creados por este piloto; no deshabilitar instalaciones previas/ajenas. Desactivar primero los componentes realmente instalados y no ejecutar operaciones sobre componentes ausentes:

```bash
python3 tools/install_boot_briefing.py --operation disable --apply
systemctl --user disable --now siegfried.service
systemctl --user show siegfried.service -p ActiveState -p MainPID
python3 tools/install_user_service.py --operation uninstall --apply
python3 tools/install_boot_briefing.py --operation uninstall --apply
systemctl --user daemon-reload
```

Uninstall se limita a snapshot marcado, dos wrappers propios y unidad propia; todos los archivos ajenos/modificados causan rechazo. Conserva directorios vacíos compartidos y todo el runtime, secretos, Vault, historial y repositorio. Paquete de briefing independiente. No usa sudo, pkill, killall ni borrados generales. No hay scripts KWin cargados por F5.5 que descargar; si un ensayo futuro carga uno, registrar ID y descargar sólo ese script propio. Tras rollback comprobar unidad inactiva/MainPID=0, entry/snapshots propios ausentes y ningún proceso/socket propio restante; no borrar un socket residual sin identificar a su dueño y proceso.

Restauración POSIX preparada para las cinco rutas iniciales, sólo si previamente cambiadas por el piloto, por instrucción del usuario y después de retirar los componentes privados; supone volver al estado compartido inseguro. No aplicar automáticamente:

```bash
chmod 775 "$HOME/.config" "$HOME/.config/autostart" "$HOME/.local" "$HOME/.local/share"
chmod 775 "$HOME/.siegfried"
```

Las ACL actuales son básicas y se conservan en el manifiesto. Restaurar un modo no restaura una ACL general. Si cambia la ACL durante el piloto, comparar la copia y restaurar sólo entradas específicas con nueva autorización; no usar setfacl indiscriminado. Los tres directorios adicionales requieren un plan propio si se autoriza cambiarlos; su original documentado es 0775.

## 16. Regresión completa

**655 pruebas PASS, 36,157 s, cero fallos/errores/omitidas**: 644 baseline preservadas + 11 pruebas de despliegue privado. Se ejecutó después de la última modificación de código y tests. Primera ejecución dentro del sandbox no es resultado certificable: remapeo de propietarios y denegación de sockets, 588 casos descubiertos, 162 errores y 2 fallos. Se repitió fuera con autorización del entorno; los errores de fixture de permisos de dos tests nuevos se corrigieron en sus propias jerarquías 0700, sin debilitar controles ni tocar pruebas anteriores. La regresión final PASS es la evidencia válida.

```bash
PYTHONPATH=src timeout -s INT -k 5s 180s \
python3 -X faulthandler -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src python3 tools/benchmark.py
PYTHONPATH=src python3 tools/benchmark_boot_briefing.py
git diff --check
git status --short --untracked-files=all
```

Logs de fixtures adversariales y ResourceWarning heredados no equivalen a errores del runtime real. No se certifica una suite libre de warnings. Los logs completos temporales están en `/tmp/siegfried-f55-regression.log`; no contienen datos del runtime real y no se incorporaron al despliegue.

## 17. SLOs

Harness ejecutados secuencialmente después de la regresión y después de terminar las mediciones de recursos; ambos exit 0. Cinco SLO PASS:

| Harness | Muestras | P95 ms | Objetivo ms | Resultado |
|---|---:|---:|---:|---|
| Fast-Path | 2500 | 0,0016 | <10 | PASS |
| Vault append/fsync, disco persistente | 200 | 2,2926 | <10 | PASS |
| CLI cold-start | 50 | 31,09 | <50 | PASS |
| Routing | 1000 | 0,0055 | <1 | PASS |
| Histórico heurístico, 20k | 100 | 1,9575 | <10 | PASS |
| Histórico exhaustivo, separado | 25 | 8,2975 | Observacional | Sin SLO propio |

Briefing: composición, 1000 muestras, P95 0,000348 ms; proceso del hook --dry-run, 25 muestras, P95 115,52772 ms. `notification_visible_ms=null`. El histórico usa eventos sintéticos ordenados/caché caliente; no se declara exactitud o latencia universal.

Los microbenchmarks no demuestran CPU/RSS sostenido, rendering KDE, login, VRAM ni estabilidad de todos los entornos. Medición de VRAM no aplicable: no se inició proceso de inferencia.

## 18. Riesgos residuales

- Ocho permisos corregidos y ambos dry-run PASS; el runtime real sigue incompleto y su inicialización requiere autorización.
- Historial previo debe preservarse; init todavía no está autorizado.
- RSS después de la ruta cognitiva supera 30 MB decimales en la observación, sin convertirlo en PASS por usar MiB.
- Arranque real de unidad/hardening, adaptadores optativos, popup/clic y login no demostrados.
- Mismo UID y fuente compartida forman parte del límite de confianza local; hashes no son firma independiente.
- No hay estimación de descanso entre reinicios sin evidencia contractual suficiente; se conserva F5.2 y se omite esa duración.
- No se demuestra exactly-once global ante pérdida del marcador o crash entre Notify y marcado.
- Python 3.10 sólo comprobado sintácticamente, ejecución real en 3.14.4.

## 19. Estado final Git

HEAD y rama conservados. Cuatro archivos rastreados modificados y cuatro nuevos sin seguimiento:

```text
 M README.md
 M systemd/siegfried.service
 M tools/install_boot_briefing.py
 M tools/install_user_service.py
?? docs/gates/GATE_F5_5_EVIDENCE.json
?? docs/gates/GATE_F5_5_RECOVERY.json
?? docs/gates/GATE_F5_5_REPORT.md
?? tests/integration/test_private_deployment_f55.py
```

`git diff --check` PASS; nuevos Python comprobados con compilación/AST y nuevos JSON parseados; reporte revisado sin placeholders ni datos privados.

Contratos congelados sin diferencias, staging sin cambios respecto al inicio. Sin commits ni push. Archivos nuevos revisados adicionalmente porque `git diff --check` no comprueba contenido sin seguimiento.

## 20. Veredicto y siguiente autorización

**BLOCKED por autorización de inicialización y validación operativa pendiente.** Las ocho correcciones de permisos están terminadas, ambos instaladores pasan en dry-run y las fuentes revisadas se conservan. No se emite PASS o PASS_WITH_DEVIATIONS de operación real sin instalación, observación y login.

Los originales 0775 y ACL básicas permanecen en el inventario de recuperación y en `permission_actions`, con precheck/postcheck de cada autorización. No se restauró ningún modo. Restitución preparada para los últimos tres directorios, sólo por instrucción del usuario y cuando sea seguro:

```bash
chmod 775 "$HOME/.config/systemd" "$HOME/.config/systemd/user" "$HOME/.siegfried/data"
```

La restauración POSIX no restituiría ACL adicionales que pudieran aparecer posteriormente. No se aplicó restauración ni cambios de propietarios.

Verificaciones no destructivas restantes completadas después de ambos dry-run:

- Runtime inspeccionado por metadatos y doctor; faltan configuración/secretos/Vault. Historial 0600 preservado y contenido no leído. Cero locks/temporales huérfanos en configuración/datos existentes.
- Servicio propio ausente/inactivo, MainPID=0; snapshots, wrappers y Autostart propios ausentes. Sin sockets/PID/marcador propios ni daemon/hook identificado por el escaneo acotado de argv, que no se imprimió.
- Sesión Wayland/KDE activa; targets gráficos activos, Konsole y validadores disponibles, bindings dbus/GLib disponibles. Notifications anuncia actions e Inhibited=false; script KWin canónico no cargado. Son comprobaciones de lectura, sin envío de notificaciones ni carga de scripts.
- Integridad de las 61 fuentes y herramientas/unidad revisadas PASS, sin cambios de implementación. Se conserva la evidencia de 655 tests/cinco SLOs; no se repitió la regresión por cambios exclusivamente de permisos y documentación.
- Inicialización/doctor y conservación de un historial sintético no vacío comprobadas en fixture temporal, sin tocar el runtime real.

**Siguiente paso concreto: autorizar exclusivamente `siegfried init` para el runtime real y su diagnóstico posterior**, con los dos comandos de la sección 4. Crearía config, assets/sounds, logs, defaults de perfil/agenda, plantilla de secretos sin credenciales reales, Vault vacío y locks privados. Mantendría `data/.history`; no habilita inferencia, servicios ni Autostart. Toda instalación/activación posterior requiere autorización independiente.

No quedan bloqueos de permisos detectados. La desviación previa de RSS (30,81 MB después de una consulta sin motor) y la CPU sin margen de resolución siguen registradas; no impiden preparar el runtime ni se convierten en SLOs operativos aprobados. Arranque real de unidad/hardening, KDE visual/clic físico/login y estabilidad real quedan pendientes de sus etapas autorizadas. No se provocó bloqueo, suspensión, cierre de sesión o reinicio. Sin commits ni push.
