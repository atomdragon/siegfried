# SIEGFRIED v1.0 — GATE F5.4

Fecha: 2026-10-09. HEAD: `c5fc7cdd99dc37b461ba21942b32efb38a34819c`.

## 1. Resumen ejecutivo

**PASS_WITH_DEVIATIONS.** Piloto aislado supervisado satisfactorio: snapshot privado, daemon real, IPC, recuperación, notificación/acción nativa inocua y rollback. **La certificación operativa total de Fase 5 permanece condicionada:** no hubo nuevo login, confirmación visual humana ni clic con REPL real. No se instaló Autostart real, corrigieron permisos reales ni activaron adaptadores persistentemente.

Se corrigieron tres defectos demostrados: probe del hook comparaba `ok` con la respuesta contractual `OK`; uninstall dejaba un snapshot propio después de fallar la publicación del .desktop; dry-run no comprobaba los padres .local/share que sí rechazaba la instalación. Se reutilizó el instalador y los componentes aprobados. Se añadió un modo manual acotado al verificador existente, porque su acción automática a los 100 ms no permite verificar un popup mediante observación humana.

Resultado: **10 pruebas F5.4 + 90 F5.3 PASS; regresión 644 PASS; cinco SLO PASS**. Recomendación: **apto para piloto aislado/manual supervisado; despliegue real condicionado a autorización, permisos y validación del runtime existente**.

## 2. Estado Git inicial

Staging vacío y sin modificaciones rastreadas; único archivo previo sin seguimiento: `docs/gates/GATE_F5_3_1_REPORT.md`. Se preservó byte a byte, SHA-256 `5a213dec103dc0be4d40607b6586a981c9b63ac4f9cde8f03f48d845e5bb8414`. `git diff --check` inicial PASS.

Se leyeron PLAN, SPECIFICATION, README, F5.3, F5.3.1, inventario y ambos instaladores. No se encontraron instrucciones AGENTS aplicables. Se revisaron compositor, hook, presentador, launcher, paths, inicialización, daemon y SessionController. No se hicieron git add, commit, push, resets ni movimientos del checkout.

## 3. Arquitectura previa y preservación

Núcleo stdlib; dbus-python/PyGObject opcionales de distribución, imports diferidos; sin PyPI. El LLM interpreta y recomienda; el núcleo valida, calcula y ejecuta. Briefing no inicia inferencia ni requiere Cloud. FocusTracker conserva el intervalo único y SessionController coordina sesión/descanso. Se preservan Event/IPC/Config v1, sin nuevos esquemas, servicios ni fuentes de verdad.

El despliegue recomendado utiliza `tools/install_boot_briefing.py` y el snapshot existente; no se creó otro instalador. `tools/install_user_service.py` y la unidad histórica se inspeccionaron, pero **no se certifica su instalación real**: ese instalador copia wrappers/unidad, no el snapshot completo de fuentes. No forma parte del procedimiento de piloto aprobado aquí. El daemon se ejecutó desde el módulo de la copia privada temporal, sin instalar ni activar una unidad de usuario.

## 4. Auditoría de permisos

UID/GID 1000/1000, usuario okami. Sesión gráfica Wayland activa. lstat/namei/getfacl de sólo lectura; rutas relevantes sin enlaces simbólicos; ACL básicas, sin entradas nominativas/default adicionales en las cuatro rutas solicitadas.

| Ruta real | Modo | Evaluación |
|---|---:|---|
| HOME | 0750 | Compatible como raíz del instalador |
| .config, .config/autostart | 0775 | Rechazo correcto por escritura de grupo |
| .local, .local/share | 0775 | Rechazo correcto por escritura de grupo |
| .local/share/siegfried-boot | Ausente | Sin snapshot real |
| .siegfried | 0775 | Runtime existente no cumple directorio privado 0700 |

**0755 es compatible para las cuatro rutas compartidas por aplicaciones**, demostrado en fixture; no se exige 0700 para todo HOME. Exclusivos del snapshot/runtime: 0700; archivos privados: 0600, entrypoints de código 0700. La pertenencia actual del grupo no elimina el riesgo de escritura compartida.

Se inspeccionó únicamente metadato de `.siegfried`; no se leyó su agenda, Vault, credenciales ni archivos internos. **No basta cambiar las cuatro rutas para certificar el daemon sobre el runtime real**: requiere una revisión autorizada de sus propios permisos/integridad. No se hizo chmod, chown, modificación de ACL ni cambio recursivo.

## 5. Riesgos de rutas compartidas

`/media` root 0755; `/media/okami` UID 1000, **0777**; `/media/okami/Mio` 0755; checkout **0775**. Se mantiene ubicación/permisos originales. Launcher directo del checkout sigue rechazado, no se debilita trusted_executable.

La copia privada impide modificaciones posteriores de terceros con distinto UID; **no autentica una fuente ya alterada**. package_sources valida archivos/propietario/symlinks/tamaño y excluye archivos world-writable, pero permite fuentes group-writable y no convierte el ancestro 0777 en confiable. Antes del piloto real se debe revisar la procedencia y comparar los bytes de origen y snapshot. Hashes del Gate son evidencia local, no una firma independiente ni protección contra un proceso malicioso del mismo UID.

## 6. Instalador y defectos corregidos

Reproducción previa a editar: inyectar fallo OSError en os.replace al publicar .desktop dejaba snapshot existente; uninstall retornaba removed=false y lo conservaba. Crear .local 0775: dry-run retornaba true; apply rechazaba la ruta después. Primera prueba real daemon/hook: PING retornaba `OK`, el probe no emitía READY por comparar `ok`.

Correcciones mínimas:

- `_validate_home` incluye .local y .local/share existentes, tanto en dry-run como apply.
- Uninstall sin entrada reutiliza remove_package y sus comprobaciones de marcador, propietario, contenido y symlinks; no crea configuraciones nuevas. Un snapshot ajeno o con contenido extraño se conserva y se rechaza la operación.
- Probe usa `IPCStatus.OK.value`, manteniendo el protocolo congelado.

Instalador conserva dry-run por defecto, private snapshot, dirfds/O_NOFOLLOW, escritura atómica, validación de .desktop, idempotencia, enable/disable/uninstall y conservación de entradas ajenas. La publicación fallida es recuperable por retry o uninstall; no se promete atomicidad conjunta de todos los archivos ante corte eléctrico. Dry-run real rechazó los permisos sin escrituras.

## 7. Integridad del snapshot

Fuentes desplegables: **57 archivos, 391 506 bytes en inspección inicial**: boot_hook.py, bin/siegfried y fuentes Python de src/siegfried. Los bytes del hook cambiaron posteriormente por la corrección indicada. Sin tests, documentación, JSONL, secretos, modelos, assets personales ni artefactos temporales. Escaneo acotado de patrones de claves privadas/tokens largos en esas fuentes: cero coincidencias; no es garantía universal de ausencia de secretos.

Snapshots preparados sólo en HOME temporal. Comparación byte a byte con package_sources, validación de ejecución `python3 -S`, private launcher y .desktop PASS. Se verificó rechazo de código alterado en snapshot existente sin sobrescribirlo. Fuentes/archivos 0600, wrappers 0700 y directorios privados; marcador de pertenencia F5.3.

Hashes de herramientas revisadas para autorización posterior:

```text
88264f4534f9a09e2bc68098286ef6aea09cf3ea9103257a3bacef7e28449d1d  tools/install_boot_briefing.py
3fbf5616f83192de38eb7229d40c8848ec11b28144c4c7daabfb3d18d29ec736  scripts/boot_hook.py
c081b0d158e65e51cdb5e400c973c8fbb3cc8cb4a76e1044be07b26409f06499  tools/verify_boot_briefing_f53.py
```

## 8. Estado de Autostart y systemd

Entrada real `org.siegfried.BootBriefing.desktop` ausente, snapshot real ausente; `siegfried.service`: LoadState=not-found, ActiveState=inactive, MainPID=0. Targets graphical-session y plasma-workspace activos. No se hizo daemon-reload, enable, start/stop de servicios reales ni sesión nueva.

En temporal: desktop-file-validate PASS, 0755 en padres aceptado, entry 0600, snapshot 0700, retry tras fallo y rollback conservando otra entrada fixture. OnlyShowIn=KDE, TryExec a Python, Terminal=false, Exec a snapshot con quoting y argumentos fijos. [XDG Autostart](https://specifications.freedesktop.org/autostart/latest/) determina Hidden/OnlyShowIn/TryExec; el [contrato Exec](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html) exige quoting y escape de campos. La herramienta install con --apply **publica la entrada habilitada**; no se presenta como preparación sin activación futura. Para preparar sólo código se reutiliza prepare_package antes de publicar la entrada, como muestra la sección 17.

## 9. Validación KDE

Plasma **6.6.6**, protocolo Notifications 1.2; propietario `:1.31`, plasmashell PID 2816, UID 1000, unidad plasma-plasmashell.service. Bus Unix `/run/user/1000/bus`. XDG_SESSION_TYPE=wayland, XDG_CURRENT_DESKTOP=KDE, XDG_SESSION_ID=3. Sesión logind Active=yes/State=active; TimestampMonotonic=18 426 712 µs. Konsole disponible. 13 capacidades, incluida actions. **Inhibited=false** en inspección y sondas finales; ningún ajuste DND cambiado.

Consulta de sólo lectura isScriptLoaded con ID canónico `siegfried_focus_watcher`: false. No se cargó, descargó ni duplicó ningún script KWin. La API consultada no enumera universalmente scripts con alias distintos; no se certifica ausencia de todos los alias posibles. Escaneo limitado de argv de procesos del UID, sin imprimir contenidos privados: ningún daemon/hook/verificador residual al cerrar las sondas.

## 10. Verificación visual

Modo manual nuevo: ventana de 20 s, espera máxima 21 s, **sin InvokeAction automático**. Notify aceptado, ID **161**, actions=true, Inhibited=false, envío **46,564 ms**. Se solicitó observación del usuario; no se recibió confirmación al cerrar el Gate. Action count=0, estado PENDING_HUMAN_ACTION. **PENDING_VISUAL** para popup/botón; sin capturas, inspección de ventanas privadas ni sustitución por aceptación D-Bus.

Modo automatizado conservado: Notify ID **162**, envío **44,354 ms**, native_notify/native_action/konsole_test_executable PASS, acción una vez, sin lanzamiento automático antes de la acción. Activación por API KDE; no se confunde con clic físico. Ambos ensayos fueron públicos, sin tareas, descanso o credenciales. Se eliminaron matches y conexiones privadas; get_is_connected false. No se midió renderizado visible ni se registró un acuse visual inexistente.

## 11. Launcher

REPLLauncher usa Konsole --separate -e, Python y bin/siegfried del snapshot/fixture, argv fijo y sin shell=True. Meteorología/tareas no llegan a argv. El ejecutable inocuo temporal escribió su marker y terminó 0; como máximo una acción por notificación, sin apertura previa. La ruta insegura original no se habilitó.

Los 90 tests conservados cubren pantalla bloqueada/desconocida, propietario incorrecto, duplicados, ausencia de bindings, errores, recursos y concurrencia. El nuevo test de --manual prueba que sin clic no se llama ni InvokeAction ni subprocess.run. **No se inició el REPL real desde ninguna notificación.** La apertura real debe ensayarse desde snapshot validado con runtime previamente autorizado y seguro.

## 12. Integración operativa real y simulada

Daemon real ejecutado con Python -S desde snapshot privado y **runtime temporal inicializado**, sin credenciales, modelos, bus ni adaptadores opt-in heredados. Dos ciclos secuenciales: socket **0600**, PING OK/pong=true, probe READY, SIGTERM sólo al hijo propio, exit 0, socket retirado, restart PASS. Vault temporal 0600, líneas compatibles v1. No se accedió a datos del HOME real.

Ensayo adicional daemon privado + presentador sobre KDE real: hook confirma daemon y entrega briefing público; segunda ejecución retorna duplicate=true. Adaptadores de foco/sesión siguen apagados en ese daemon. No se activaron persistentemente todos los componentes ni se provocó foco privado, suspensión o bloqueo. Integración FocusTracker/SessionController, Pomodoro y alertas conserva la evidencia automatizada F5.1/F5.2 y regresión, sin afirmar una nueva validación física de esas transiciones.

Sin fuentes gráficas/bindings: pruebas existentes de degradación PASS; hook desde copia privada headless/offline termina correctamente, sin crear base de datos inexistente ni inventar descanso. SessionController conserva INSUFFICIENT_DATA cuando falta par histórico. La ruta del briefing no llama inferencia. El clima del ensayo temporal fue **fixture de 80 ms, sin red**, no una nueva certificación del proveedor. Defaults mantienen clima/tarea desactivados.

RSS puntual del daemon temporal **28 752 KiB**, sólo una muestra. No demuestra estabilidad prolongada ni certifica CPU <0,2 %; no se provocó saturación del host. Procesos, sockets y recursos propios liberados; no se usaron comandos globales contra procesos.

## 13. Benchmark de arranque y puntos de referencia

Sonda desde código privado, referencia de entrada al coordinador `25024.127133913 s` monotónico, tras imports del proceso Python:

| Hito | Desde entrada exterior al hook |
|---|---:|
| Composición | 109,304 ms |
| Primer Notify, solicitud | 110,158 ms |
| Respuesta KDE | 127,750 ms |
| Comienzo de clima simulado | 129,094 ms |

Métricas internas: composición **0,077391 ms**, envío **127,473727 ms**, clima fixture **80,898968 ms**, total **300,765234 ms**, ventana de acción 0,3 s. Dos send por actualización del clima sobre el ID del mismo briefing; segunda ejecución del hook deduplicada **0,413240 ms**. Primer Notify precede clima. PING presupuestado es contexto esencial; no hay espera ilimitada del daemon, inferencia ni lectura de tarea privada.

Benchmark separado existente: compositor 1000 muestras P95 **0,000342 ms**; cold-start **dry-run** 25 muestras P95 **115,787949 ms**. Incluye creación/espera del proceso; no mide entrega KDE. Login, activación de Autostart e inicio del proceso no equivalen a entrada al coordinador. Aparición efectiva no disponible. **SLO visible <2 s desde login PENDIENTE**; no se usó TimestampMonotonic de la sesión ya abierta para inventar el resultado.

## 14. Suite de regresión

Baseline 634 preservado, 10 tests añadidos en `tests/integration/test_operational_pilot_f54.py`. Cobertura incremental: fallo de publicación/rollback, retry, conservación de entrada ajena, snapshot huérfano ajeno o symlink, dry-run con .local compartida, aceptación 0755, integridad/tampering/exclusión de datos, hook privado headless, modo manual sin invocación y daemon real/IPC/probe/restart.

Primera ejecución de nueve tests encontró la comparación IPC defectuosa; se corrigió el código, sin relajar la expectativa READY. Resultado final combinado específico: **100 tests en 1,307 s, OK**. Regresión: **644 tests en 33,269 s, OK**. No se modificaron las 634 pruebas existentes.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout -s INT -k 5s 60s python3 -X faulthandler -m unittest tests.integration.test_operational_pilot_f54 tests.integration.test_boot_briefing_f53 -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout -s INT -k 5s 180s python3 -X faulthandler -m unittest discover -s tests -p 'test_*.py'
```

Pruebas usan mocks, snapshots y runtimes temporales. Errores de fixtures adversariales en logs son esperados. Suites y benchmarks no se ejecutaron simultáneamente.

## 15. SLOs

Ambos harness existentes ejecutados después de regresión, con timeout 120 s/60 s respectivamente, sin alterar su metodología.

| Harness de arquitectura | Muestras | P95 | Resultado |
|---|---:|---:|---|
| Fast-Path <10 ms | 2500 | 0,0016 ms | PASS |
| Vault append, SLO vigente, ext4 persistente | 200 | 2,3165 ms | PASS |
| CLI cold-start <50 ms | 50 | 35,55 ms | PASS |
| Routing <1 ms | 1000 | 0,0069 ms | PASS |
| Histórico heurístico, 20k, <10 ms | 100 | 2,0239 ms | PASS |
| Histórico exhaustivo exacto, separado | 25 | 8,1974 ms | Observacional |

Exhaustivo: 20k eventos sintéticos separados 120 s, caché caliente; sin garantía universal. Cinco SLO del harness no incluyen prueba de login/renderizado, estabilidad de RSS a largo plazo ni consumo VRAM; no se arrancó inferencia.

## 16. Riesgos residuales

Pendientes: popup/botón observados, clic real/REPL, login medido, autorización de permisos/Autostart, validación del runtime real 0775 y origen del código compartido. DND puede cambiar por decisiones del usuario/políticas de KDE. La prueba API no certifica activación física Wayland. El runtime real inseguro no se repara silenciosamente: daemon fail-closed, no garantía de funcionamiento sobre esos datos actuales.

No se certifica despliegue persistente del daemon por el instalador genérico, ni E2E nuevo de suspensión/reanudación/foco físico. Se mantiene la limitación histórica de descanso F5.2. Mismo UID puede modificar su código/bus; bindings/broker forman parte de la confianza. Ninguno de estos límites se oculta con PASS total.

## 17. Procedimiento de piloto real — PROPUESTO, NO EJECUTADO

Requiere autorización humana explícita por etapa. Fuente revisada, sesión desbloqueada, observador presente, ningún servicio Siegfried concurrente y runtime real previamente validado. Si .siegfried mantiene 0775 o hay archivos privados inseguros, **detenerse antes del daemon/REPL** y solicitar una auditoría/corrección específica autorizada; no añadir chmod recursivo ni leer secretos para improvisarla.

**Paso 1: cuatro cambios mínimos propuestos.** Compatibilidad 0755 comprobada; conservan lectura/travesía de grupo. No aplicarlos si compartir escritura es necesario:

```bash
chmod g-w "$HOME/.config" "$HOME/.config/autostart" "$HOME/.local" "$HOME/.local/share"
```

**Paso 2: revisión y preparación del código privado, sin publicar Autostart aún:**

```bash
cd /media/okami/Mio/Siegfried
sha256sum tools/install_boot_briefing.py scripts/boot_hook.py tools/verify_boot_briefing_f53.py
python3 tools/install_boot_briefing.py --dry-run
python3 - <<'PY'
from pathlib import Path
from tools.install_boot_briefing import _validate_home, prepare_package, package_sources
home = _validate_home(Path.home())
root = Path.cwd()
target = prepare_package(home, root, False)
assert all((target / name).read_bytes() == data for name, data in package_sources(root).items())
print('Snapshot privado verificado; Autostart todavía no publicado.')
PY
```

La preparación reutiliza funciones existentes del instalador; no es otro instalador. Ejecutar sólo después de confiar en la fuente y autorizar escrituras reales. No sobrescribe snapshots distintos ni copia datos.

**Paso 3: observación inocua antes de activar:**

```bash
PYTHONPATH=src timeout -s INT -k 5s 30s python3 tools/verify_boot_briefing_f53.py --manual
```

Confirmar popup y botón, pulsar voluntariamente y comprobar una Konsole inocua. Si no aparecen, registrar PENDING_VISUAL/inhibición y detener activación. No alterar DND automáticamente.

**Paso 4: sólo con runtime autorizado y seguro, ensayo foreground del daemon privado.** No inicia modelos por mostrar el briefing. Mantener adaptadores apagados para el primer ciclo; no duplicar un daemon ya activo:

```bash
SIEGFRIED_ENABLE_KWIN=0 SIEGFRIED_ENABLE_SESSION=0 SIEGFRIED_HOME="$HOME/.siegfried" PYTHONPATH="$HOME/.local/share/siegfried-boot/src" python3 -m siegfried.daemon
```

Comprobar PING/STATUS desde otra consola y detener ese foreground con Ctrl+C. No ejecutar init contra datos reales sin una autorización separada; no utilizar el instalador genérico de servicio como sustituto de esta prueba.

**Paso 5: autorización distinta para publicar/activar Autostart:**

```bash
python3 tools/install_boot_briefing.py --apply
desktop-file-validate "$HOME/.config/autostart/org.siegfried.BootBriefing.desktop"
```

--apply instala/reutiliza snapshot y crea entry Hidden=false, habilitada para el próximo login. Clima/tareas quedan desactivados. Ninguno de esos comandos se ejecutó en HOME real durante este Gate.

**Paso 6:** el usuario autoriza y efectúa cierre/inicio de sesión por KDE, sin comando automático de logout/reboot. Registrar referencia gráfica, arranque del hook, composición, Notify/aceptación y aparición visible con reloj alineado/incertidumbre declarada. El verificador actual no mide login; falta instrumentación observacional aprobada antes de certificar <2 s. Si no hay evidencia temporal suficiente, conservar pendiente.

**Paso 7:** autorizar clic «Abrir sesión» con REPL real, comprobar una única Konsole y salida limpia. Abrir no autoriza prompts de inferencia ni exposición de tareas. Revisar metadatos de recursos y logs sanitizados, sin volcar contenidos privados. Si falla cualquier precondición, aplicar rollback aprobado y conservar datos.

## 18. Rollback — PROPUESTO, NO EJECUTADO EN HOME REAL

Desactivación reversible de la entrada propia:

```bash
cd /media/okami/Mio/Siegfried
python3 tools/install_boot_briefing.py --apply --operation disable
```

Reactivación sólo con nueva autorización:

```bash
python3 tools/install_boot_briefing.py --apply --operation enable
```

Retirada del .desktop y snapshot propios, conservando otros Autostart y todo runtime/Vault:

```bash
python3 tools/install_boot_briefing.py --apply --operation uninstall
```

También retira snapshot marcado cuando falta entry, corrección F5.4 comprobada. Si encuentra symlinks, archivos ajenos, marcador/permisos incorrectos o código adicional, rechaza y preserva; revisar específicamente, nunca rm global. No revertir a 0775 automáticamente: sólo el usuario decide recuperar escritura compartida tras reevaluar riesgo. Cerrar el foreground propio mediante Ctrl+C; no pkill/killall ni detener servicios ajenos.

## 19. Archivos y estado Git final

Modificados por F5.4: `scripts/boot_hook.py`, `tools/install_boot_briefing.py`, `tools/verify_boot_briefing_f53.py`, `README.md`, `PLAN.md`. Nuevos: `tests/integration/test_operational_pilot_f54.py` y este reporte. Informe F5.3.1 sin seguimiento ya presente, preservado.

El manifest F5.3 permanece histórico: sus cinco diferencias esperadas son exactamente los cinco archivos rastreados modificados arriba; los demás hashes finales coinciden. No se reescribió el inventario para ocultar cambios. Schemas y src/siegfried/contracts sin diferencias contra HEAD ni baseline previo. Sin staging, HEAD intacto, `git diff --check` PASS. Archivos nuevos revisados: tests/documentación, sin modelos, secretos o datos personales. Propuesta humana posterior: `fix: validate supervised deployment and briefing daemon probe`; no se ejecutó.

## 20. Dictamen

**PASS_WITH_DEVIATIONS.** Implementación y piloto permitido completos, defectos demostrados corregidos y 644 pruebas/cinco SLO satisfactorios. **Sí al piloto aislado/manual supervisado; no a activar ahora Autostart o usar el runtime real sin sus autorizaciones y correcciones.** Fase 5 tiene cierre técnico condicionado, no certificación operativa total. PENDING_VISUAL, clic real y login <2 s se mantienen hasta evidencia humana suficiente. No se avanza a nuevas funcionalidades.
