# SIEGFRIED — GATE F5.3.1

Fecha: 2026-10-09. Plataforma observada: KDE Plasma 6.6.6 / Wayland.

**Veredicto: PASS_WITH_DEVIATIONS.** Siegfried está preparado para un **piloto manual supervisado**, sin autoinicio persistente y sin exposición de tareas privadas. **No está aprobado todavía el despliegue automático en el HOME actual.** Faltan confirmación visual humana, medición desde un nuevo login y clic físico con apertura del REPL real; además, los permisos actuales impiden instalar con seguridad. No se modificó la arquitectura ni el código funcional.

## 1. Estado inicial e inventario

HEAD inicial/final: `c5fc7cd` (`feat: Add briefing marker file and implement tests for boot briefing`). Árbol y staging inicialmente limpios; `git diff --check` y `git diff --stat` sin incidencias. Los commits previos son `c9a20e4`, `cba9059`, `b4ac8ed`, `6d03d70`. No se ejecutaron add, commit ni push.

Se revisaron SPECIFICATION, PLAN, README, reportes F5.3/F5.2 e inventario F5.3, compositor, coordinador, presentador KDE, launcher, instalador y pruebas. **Los 15 archivos con tamaño/SHA-256 del manifest final coinciden byte a byte; el archivo 16 es el inventario autorreferente, rastreado y sin cambios.** El manifest anterior de 108 archivos representa el baseline histórico de F5.3, no sus hashes finales: sus diferencias esperadas no constituyen corrupción. Los contratos coinciden también con el baseline `c9a20e4`: `git diff c9a20e4 HEAD -- schemas src/siegfried/contracts` vacío.

Único archivo añadido por F5.3.1: este reporte. No se sobrescribió trabajo ajeno. Las sondas se ejecutaron en memoria y sus fixtures/snapshots en directorios temporales; no se incorporaron modelos, credenciales ni datos del usuario. La propuesta de recuperación de F5.3 ya corresponde a código comprometido por una acción anterior a este Gate; para revisión humana posterior se propone únicamente `docs: record Gate F5.3.1 operational validation`, sin ejecutarlo.

## 2. Diagnóstico de KDE

| Comprobación real, de lectura | Resultado |
|---|---|
| Propietario de `org.freedesktop.Notifications` | `plasmashell`, PID 2816, UID 1000, nombre único `:1.31`, unidad `plasma-plasmashell.service` |
| Servidor / protocolo anunciado | Plasma, KDE, 6.6.6, protocolo 1.2 |
| Sesión logind | sesión 3, Wayland, `Active=yes`, `State=active` |
| Targets de usuario | `plasma-workspace.target` y `graphical-session.target` activos |
| Entorno | `XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=KDE`, bus Unix `/run/user/1000/bus` |
| Capacidades | 13; incluyen `actions`, `body`, `body-markup`, `persistence`, `inhibitions` |
| Interfaces | Notifications, Properties; extensión KDE `InvokeAction`; propiedad `Inhibited` con señal de cambios |
| Utilidades | Konsole, notify-send, desktop-file-validate y getfacl disponibles |

**`Inhibited=false` durante todas las lecturas de este Gate.** Una suscripción acotada a PropertiesChanged del propietario fijado no observó cambios durante la sonda de aproximadamente 20 s; lectura final false, igual que en las pruebas de acción y hook. El reporte F5.3 había observado true. La evolución comprobable es «true en la ejecución histórica; false ahora», sin evidencia de cuándo ni por qué cambió. El agente no desactivó inhibiciones.

La lectura limitada de `plasmanotifyrc` no encontró valores explícitos en `[DoNotDisturb]` para Until, WhenScreensMirrored, WhenScreenSharing, WhenFullscreen o NotificationSoundsMuted. Esto **no demuestra** que todas las preferencias sean falsas: KDE tiene defaults y estado dinámico. El [esquema oficial de Plasma 6.6](https://raw.githubusercontent.com/KDE/plasma-workspace/Plasma/6.6/libnotificationmanager/kcfg/donotdisturbsettings.kcfg) contempla activación automática por pantallas duplicadas, compartición y pantalla completa. El [código oficial del servidor](https://raw.githubusercontent.com/KDE/plasma-workspace/Plasma/6.6/libnotificationmanager/server.cpp) distingue inhibición general y por aplicaciones; esas causas detalladas no se exponen en la introspección D-Bus disponible. No se inspeccionaron ventanas, motivos privados de aplicaciones ni capturas.

Clasificación: **no se reproduce una inhibición actual ni se ha detectado un fallo de integración de Siegfried**. La causa histórica entre configuración del usuario e inhibición temporal de KDE queda indeterminada. La falta de evidencia visual es una limitación de observación del entorno del agente. No corresponde atribuir actualmente `BLOCKED_BY_ENVIRONMENT` a Inhibited: está false. Si vuelve a true durante la verificación humana, ese subcaso se registrará como `BLOCKED_BY_ENVIRONMENT`, sin cambiar ajustes automáticamente.

## 3. Presentación visual, acción y limpieza

Se usó **KDEBriefingPresenter existente**, con conexión privada y propietario validado, para enviar un saludo genérico de prueba sin tarea, descanso, clima ni contenido privado. Acción «Abrir sesión» ligada sólo a un callback inocuo que contaba clics, sin abrir procesos. Caducidad solicitada: 20 000 ms. ID aceptado: 152.

| Nivel de evidencia | Resultado |
|---|---|
| Solicitud Notify | Comprobada; timestamp monotónico capturado inmediatamente antes de call_blocking |
| Aceptación D-Bus | Comprobada; ID positivo y retorno del propietario KDE |
| Aparición efectiva del popup | **Pendiente**: se solicitó observación humana, sin confirmación recibida al cerrar el reporte |
| Acción anunciada/enviada | Comprobada; servidor anuncia actions y se envió el par open / Abrir sesión |
| Botón físicamente visible | **Pendiente**, distinto de capacidad anunciada |
| Clic humano en la sonda | No observado; contador 0 |
| Caducidad | NotificationClosed autenticada, reason=1, a 19 884,709 ms desde inicio de sonda |
| Limpieza | Matches y fuentes liberados, bus cerrado; get_is_connected false |

La caducidad es evidencia del ciclo del servidor, no prueba de renderizado. El [protocolo oficial](https://specifications.freedesktop.org/notification/latest/protocol.html) define reason=1 como expiración y retorna un ID desde Notify; no proporciona un acuse independiente de aparición visual.

Se repitió `tools/verify_boot_briefing_f53.py`: **native_notify PASS, native_action PASS, konsole_test_executable PASS**, un lanzamiento, cero apertura automática, sin REPL real ni Autostart. InvokeAction fue invocado por D-Bus para esa notificación concreta; no se presenta como clic físico. La herramienta cerró su conexión y eliminó sus matches. El proceso de Konsole con ejecutable inocuo terminó con código 0; no se dejó un worker propio residente.

## 4. Mediciones y límites

Sonda del presentador, monotónico absoluto de inicio `23061.629625368 s`:

| Hito | Desde inicio de sonda |
|---|---:|
| Inicio de composición | 31,271 ms |
| Fin de composición | 31,289 ms |
| Solicitud Notify | 31,572 ms |
| Aceptación KDE | 47,168 ms |
| Aparición observable | No disponible |

Se instrumentó adicionalmente **run_briefing real**, sin editarlo: wrapper de composición y subclase observadora del presentador; runtime temporal privado, socket inexistente, sin clima ni datos personales. Texto sustituido por un mensaje público de prueba. Inicio exterior de invocación `23180.934034560 s`; éste no es el inicio del login ni del proceso Python. Ventana de acción acotada a 0,2 s.

| Hito del coordinador | Desde referencia exterior |
|---|---:|
| Inicio de composición | 83,040 ms |
| Solicitud Notify | 84,115 ms |
| Aceptación KDE | 101,877 ms |

Resultado interno del hook: composición **0,063457 ms**, envío aceptado **101,600426 ms**, total **200,209710 ms**. La pequeña diferencia con la referencia exterior corresponde al instante inicial interno y la instrumentación. Segundo hook en el mismo runtime: `duplicate=true`, **0,703967 ms**, sin segundo envío. Recursos liberados. La prueba nativa de Konsole registró envío aceptado en **43,683 ms**. Son muestras operativas individuales, no percentiles ni garantía bajo cualquier carga.

Referencias reales disponibles: logind TimestampMonotonic **18 426 712 µs**, inicio de plasmashell **20 564 990 µs**, unidad activa **20 808 706 µs**. Corresponden a la sesión gráfica existente, no a un nuevo login con Autostart de Siegfried. No se restaron para inventar una latencia de arranque. **Login → aparición visible <2 s sigue pendiente**. Tampoco se midió tiempo de renderizado efectivo ni se usó cold-start como sustituto.

Regresión finalizada antes de repetir el harness de benchmarks; no se ejecutaron suites y benchmarks simultáneamente. Cinco SLO PASS en esta ejecución:

| Medición | Muestras | P95 |
|---|---:|---:|
| Fast-Path, objetivo <10 ms | 2500 | 0,0016 ms |
| Vault append, SLO vigente del harness, ext4 persistente | 200 | 2,4222 ms |
| CLI cold-start, objetivo <50 ms | 50 | 31,08 ms |
| Routing, objetivo <1 ms | 1000 | 0,0071 ms |
| Histórico heurístico, 20k eventos, objetivo <10 ms | 100 | 1,9992 ms |
| Histórico exhaustivo exacto, medición separada | 25 | 8,2527 ms |

El exhaustivo procesó 20k eventos sintéticos separados 120 s, caché caliente; no demuestra exactitud o latencia universal ni certifica el SLO visual del briefing.

## 5. Seguridad del despliegue XDG

Metadatos lstat y ACL leídos sin modificación; ningún directorio enumerado es symlink. Propietario de los directorios de usuario: UID/GID 1000/1000.

| Ruta | Modo | Consecuencia |
|---|---:|---|
| `/home/okami` | 0750 | Sin escritura de grupo/otros |
| `.config`, `.config/autostart` | 0775 | Escritura de grupo; instalador rechaza |
| `.local`, `.local/share` | 0775 | Escritura de grupo; preparación de snapshot real rechazaría |
| `.local/share/siegfried-boot` | Ausente | Ningún snapshot real instalado |
| `/media` | 0755, UID 0 | Ancestro sin escritura de grupo/otros |
| `/media/okami` | 0777 | Escritura universal; ejecución directa del checkout rechazada |
| `/media/okami/Mio` | 0755 | Sin escritura de grupo/otros |
| Checkout | 0775 | Escritura de grupo; launcher directo no confiable |

getfacl muestra únicamente entradas POSIX básicas, sin ACL nominativas/default adicionales en las rutas auditadas. La pertenencia actual del grupo no elimina el riesgo ni justifica saltar la política. `tools/install_boot_briefing.py --dry-run` contra HOME real retorna 1 con diagnóstico sanitizado y sin escrituras. La entrada real `org.siegfried.BootBriefing.desktop` no existe.

Se repitió el ciclo completo en **HOME temporal 0700**: dry-run no crea archivos; instalación, comparación de **57 archivos fuente** byte a byte, desktop-file-validate exit 0, reinstalación unchanged, disable/enable y desinstalación PASS. Snapshot privado: archivos 0600, dos entrypoints 0700, directorios privados y marcador propio. Otra entrada fixture permanece con el mismo hash tras desinstalar. No se instalaron datos de runtime ni una nueva fuente de verdad.

El instalador conserva O_NOFOLLOW/dirfds, rechaza symlinks y entradas ajenas, limita archivos, verifica propietario y contenido del snapshot existente, escribe entry 0600 mediante reemplazo atómico y argumentos `.desktop` escapados. Exec fijo a Python y boot_hook del snapshot, OnlyShowIn=KDE, Terminal=false; red/tareas son opt-in. No requiere sudo ni reemplaza entradas ajenas.

**Límites de seguridad:** copiar desde el checkout no autentica su origen. package_sources permite archivos fuente escribibles por grupo; el ancestro 0777 deja una superficie de sustitución previa a lectura. El snapshot restringe modificaciones posteriores, no sanea código ya alterado. Se exige revisión humana de hashes/código y una fuente confiable antes del despliegue; no se propone relajar las comprobaciones del launcher. Como otros controles locales, no protege contra un proceso malicioso del mismo UID. La desinstalación estándar exige conservar la entrada y el marcador propios; si un tercero borra sólo la entrada, la limpieza del snapshot huérfano necesita revisión específica, no borrado global.

## 6. Pruebas y preservación de contratos

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout -s INT -k 5s 60s \
  python3 -X faulthandler -m unittest tests.integration.test_boot_briefing_f53 -v
90 tests, 0.379 s, OK

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout -s INT -k 5s 180s \
  python3 -X faulthandler -m unittest discover -s tests -p 'test_*.py'
634 tests, 35.234 s, OK

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout -s INT -k 5s 120s \
  python3 tools/benchmark.py
Cinco SLO PASS
```

No se editaron tests para obtener PASS. Logs de errores en la regresión corresponden a fixtures adversariales esperadas. La suite específica conserva cobertura de duplicados, argv fijo, emisor incorrecto, falta de bindings, pantalla bloqueada, symlinks, privacidad, concurrencia y cleanup. Dos comprobaciones adicionales en memoria: Konsole no encontrado → False y cero Popen; fallo FileNotFoundError al lanzar → False sin excepción escapada. No se quitó Konsole del host para simularlo.

`git diff --check` PASS. Contratos Event/IPC/Config v1 y sistema de inferencia sin cambios. Sin paquetes nuevos, llamadas Cloud, suspensión/reinicio/apagado, chmod/chown reales, activación de Autostart ni inicio automático del REPL. Los únicos lanzamientos de escritorio fueron los ensayos inocuos autorizados; no se cambiaron ajustes del escritorio ni archivos reales mediante el instalador.

## 7. Desviaciones resueltas o acotadas

| Desviación F5.3 | Cierre F5.3.1 |
|---|---|
| Aparición visual no comprobada | Sigue pendiente de observación humana; envío, aceptación, expiración y cleanup reales confirmados |
| Tiempo login → visible no medido | Sigue pendiente; referencias monotónicas de sesión identificadas, sin sustitución por cold-start |
| Plasma reportaba inhibición | Ya no se reproduce: false en lecturas y durante sonda; causa histórica indeterminada |
| Autostart real no activado | Se conserva por restricción explícita; ciclo temporal completo PASS |
| Directorios 0775 / ancestro 0777 | Riesgos y rechazo confirmados; procedimiento mínimo preparado, no aplicado |
| REPL real sin clic humano | Ruta nativa con ejecutable inocuo PASS; clic físico y REPL real pendientes |

No se necesita corrección funcional de F5.3 para los hallazgos comprobados. Las desviaciones restantes requieren decisiones del usuario y una ejecución observable, no cambios arquitectónicos.

## 8. Riesgos residuales

No hay evidencia obligatoria suficiente para PASS total. Una notificación puede ser aceptada y expirar sin que el usuario vea su popup. La futura política de KDE puede inhibir mensajes; Siegfried no debe anularla. El ensayo InvokeAction no prueba interacción física ni activación Wayland del REPL real. No existe medición de login nuevo. El HOME actual no admite instalación automática segura y la fuente compartida requiere revisión. La estimación F5.2 mantiene sus límites de persistencia: no se inventa descanso entre reinicios. No se revela tarea privada en el piloto recomendado.

## 9. Acciones manuales necesarias

1. Con la sesión desbloqueada y observador presente, enviar el saludo público con el presentador existente y confirmar visualmente título, texto y botón; registrar hora y resultado. No usar la prueba de InvokeAction a los 100 ms como sustituto de observar el popup. Si Inhibited=true, revisar voluntariamente el indicador de No molestar y las condiciones de duplicación/compartición/pantalla completa en KDE; documentar la causa antes de cambiar ajustes. El agente no los cambia.
2. Para la apertura del REPL, autorizar previamente el ensayo real desde una fuente/snapshot confiable, pulsar físicamente «Abrir sesión» y comprobar una única Konsole con Siegfried; cerrar la sesión de prueba. No se autoriza aquí por inferencia ni se inicia automáticamente.
3. Antes de instalar, revisar propietario, ACL, necesidad de compartir y hashes de la fuente. **Corrección POSIX mínima propuesta, sólo para una autorización posterior:** quitar exclusivamente escritura de grupo en `/home/okami/.config`, `.config/autostart`, `.local` y `.local/share` (0775 → 0755, operación `chmod g-w` sobre esas cuatro rutas exactas). Mantener HOME 0750, evitar chmod recursivo, no cambiar propietarios correctos ni tocar otros archivos. Si compartir escritura es un requisito del usuario, no aplicar esa propuesta: mantener el rechazo. Para el checkout, usar una copia revisada en ubicación privada; no cambiar permisos de `/media/okami` globalmente.
4. Tras autorización de permisos y fuente, repetir dry-run y revisar Exec, snapshot y hashes. Autorizar por separado instalación/activación real. Las operaciones existentes `--operation disable` y `--operation uninstall` permiten revertir únicamente la entrada/paquete propios; probar primero en HOME temporal. Ningún `--apply` real se ejecutó en este Gate.
5. Para medir <2 s, autorizar un futuro login de prueba con instrumentación: referencia gráfica declarada (TimestampMonotonic logind y disponibilidad de sesión Plasma diferenciados), entrada del hook, composición, solicitud, aceptación y evidencia observable de aparición usando el mismo dominio temporal o sincronización validada. Registrar exactamente qué referencia significa «login». Si no hay acuse visual instrumentable sin invasión, registrar observación humana con su incertidumbre; no certificar un límite estricto de 2 s usando sólo el retorno de Notify.

## 10. Dictamen y estado final

**PASS_WITH_DEVIATIONS. Sí, Siegfried está preparado para un piloto manual supervisado, público y sin Autostart persistente. No está listo para instalación automática en el HOME actual ni para certificación visual de login <2 s.** Los impedimentos restantes están identificados y no justifican reconstruir F5.3 ni avanzar a nuevas funcionalidades.

Estado Git esperado y comprobado al cierre: HEAD `c5fc7cd`, staging vacío, un único archivo sin seguimiento `docs/gates/GATE_F5_3_1_REPORT.md`; todos los 16 archivos de F5.3 preservados. `git diff --check` PASS. El nuevo archivo es documentación revisada, sin secretos, modelos o datos privados; ninguna instalación/activación persistente, commit o push.
