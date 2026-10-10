# SIEGFRIED v1.0 — GATE F5.5

Fecha: 2026-10-09. Estado actual: servicio active/enabled, Autostart Hidden=false,
runtime READY; última regresión aislada registrada 689 PASS. Ver sección 42 para
la preparación y diagnóstico de lectura del login real.

**Veredicto actual: BLOCKED.** Preparación técnica previa al logout PASS; faltan
login KDE real, observación humana y comprobaciones posteriores. Integración
KDE → REPL técnica y humana PASS. Se conserva la desviación RSS anterior y el
incidente histórico del historial UNRESOLVED. La conservación actual no certifica
recuperación del historial original. Ningún logout/reinicio ejecutado.

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

El usuario autorizó exclusivamente los dos comandos de chmod de las cinco rutas iniciales. Se repitió la comprobación de tipo real, propietario, grupo, ausencia de symlinks en ruta/ancestros y ACL básicas antes de ejecutarlos. Aplicados exactamente: cuatro rutas 0775 → 0755 y `.siegfried` 0775 → 0700. Verificación inmediata con `stat` y `getfacl` PASS; propietario, grupo e inodos conservados. Las ACL básicas ahora reflejan los nuevos modos. En esa etapa de permisos no se modificaron propietarios, ACL adicionales ni archivos internos y no se inicializó, instaló o activó el runtime. Las excepciones anteriores al sandbox sólo autorizaron consultas/pruebas aisladas.

En una segunda autorización exclusiva se ejecutaron `chmod 755 "$HOME/.config/systemd" "$HOME/.config/systemd/user"` y `chmod 700 "$HOME/.siegfried/data"`. Los tres originales 0775 y ACL básicas se registraron antes de actuar en el manifiesto; postcheck conservó los inodos y propietarios. No se usó chmod recursivo ni se alteraron archivos internos u otros permisos.

En una tercera autorización exclusiva se ejecutaron la inicialización oficial y doctor, en ese orden, con `SIEGFRIED_HOME="$HOME/.siegfried"`. Precheck de ruta exacta, ausencia de symlinks y destinos/archivos existentes PASS. La operación creó únicamente cuatro directorios y ocho archivos oficiales ausentes; ningún archivo previo se sobrescribió. En esa etapa de init no se instalaron componentes ni se iniciaron daemon, servicios o Autostart.

En una cuarta autorización exclusiva se ejecutó exactamente el bloque documentado de `prepare_package` para ambos snapshots. Destinos ausentes y fuentes coincidentes con los hashes auditados antes de actuar. No se crearon unidades, archivos XDG Autostart ni wrappers dentro del runtime; no se iniciaron daemon o REPL. No se sobrescribieron instalaciones previas.

En una quinta autorización exclusiva se ejecutaron, en orden, el dry-run oficial, --apply, daemon-reload y show. Se creó únicamente `.siegfried/bin` y sus dos wrappers, más la unidad de usuario. Se reutilizaron ambos snapshots sin cambios y no se sobrescribieron destinos. Servicio loaded/inactive/MainPID=0/disabled. Una comprobación adicional demasiado estricta rechazó el aviso externo de spice-vdagent y retiró los tres archivos nuevos y el directorio bin vacío; la repetición autorizada terminó correctamente. No se iniciaron/habilitaron servicios, no se abrió REPL, no se publicó Autostart y no se cambiaron permisos existentes. Detalles y ambas operaciones conservados en `inactive_service_installation_actions`.

Corrección controlada del defecto ya señalado por F5.4: el instalador antiguo copiaba wrappers sin las fuentes que importaban. Se reutilizan las funciones de snapshot existentes con una selección `daemon=True`, un paquete independiente y un marcador F5.5. El paquete normal F5.3 conserva su selección original. No se modificaron núcleo, inferencia, IPC, timers, agenda ni contratos.

El usuario autorizó posteriormente un único piloto manual: 63,328 s, PING/STATUS OK, socket 0600 y 13 muestras durante 60 s. SIGTERM mediante pidfd al PID 214562, salida 0, sin SIGKILL ni residuos. El postcheck esperaba cuatro líneas INFO y falló con log vacío; se completaron diagnósticos de lectura sin repetir el daemon.

La autorización actual sólo cubre corregir observabilidad en el repositorio y pruebas temporales. No se actualizó código instalado, reinició daemon real ni modificó el log existente. El cambio no autorizado del historial durante la primera regresión se documenta en sección 21.

## 4. Estado del runtime real

**READY, doctor exit 0**, después de la inicialización autorizada. Precheck: `SIEGFRIED_HOME` coincide exactamente con `/home/okami/.siegfried` y el HOME de la cuenta UID 1000, sin symlinks en base, ancestros o entradas existentes. Se comprobaron tipo, propietario, grupo, ACL básicas y modos; todos los destinos que iban a crearse estaban ausentes. Fuentes coincidentes con los 61 hashes revisados. Los originales de permisos y el inventario previo permanecen en el manifiesto.

Comandos exactos ejecutados, en este orden:

```bash
SIEGFRIED_HOME="$HOME/.siegfried" /usr/bin/python3 -B /media/okami/Mio/Siegfried/bin/siegfried init
SIEGFRIED_HOME="$HOME/.siegfried" /usr/bin/python3 -B /media/okami/Mio/Siegfried/bin/siegfried doctor
```

Ambos retornaron 0 y `Estado: READY`. Antes de ejecutar doctor se comprobó que init no cambió ningún archivo previo. `doctor` realiza validación de Config v1, UTF-8 de secretos y auditoría completa del Vault sin imprimir sus contenidos. No hubo conflictos ni corrupción; ante cualquier resultado inesperado se habría detenido la secuencia. La inicialización administrativa puntual no configura ejecución persistente desde el checkout.

| Elementos creados | Rutas oficiales | Modo |
|---|---|---:|
| Cuatro directorios | `config`, `assets`, `assets/sounds`, `logs` | 0700 |
| Cuatro archivos de datos/configuración | `config/secrets.env`, `config/core_profile.json`, `config/active_agenda.json`, `data/siegfried_vault.jsonl` | 0600 |
| Cuatro locks | `config/secrets.lock`, `config/core_profile.lock`, `config/active_agenda.lock`, `data/vault.lock` | 0600 |

`data/history.lock` no se creó: la inicialización del historial tomó la ruta de verificación del archivo previo. No se añadieron archivos ajenos ni quedaron temporales. Root y `data` previos permanecen en 0700; todos los seis directorios privados y los nueve archivos/locks del runtime son del usuario, con 0700/0600 verificados mediante stat y ACL básicas mediante getfacl.

**Historial preservado íntegramente:** comparación de contenido antes/después y de UID, GID, modo, inodo, dispositivo, tamaño y timestamps atime/mtime/ctime PASS. Lectura privada con O_NOFOLLOW y O_NOATIME; fingerprints sólo en memoria, sin persistir hashes ni contenidos privados en artefactos o terminal. No se imprimieron historial, secretos, configuración o eventos. Configuraciones y Vault no existían antes; se crearon defaults oficiales y un Vault vacío de 0 bytes, sin modificar ni eliminar eventos previos. No se agregaron credenciales reales.

Los resultados antes/después y lista exacta de creaciones están en `runtime_initialization_actions` de los dos manifiestos. Verificación UTC: 2026-10-09T18:09:38.827020+00:00. Ensayo temporal anterior de conservación de historial no vacío se conserva como evidencia adicional, sin sustituir este resultado real.

Verificaciones de lectura inmediatamente posteriores a init: ambos instaladores dry-run exit 0; servicio propio entonces not-found/inactive/MainPID=0; snapshots, wrappers y Autostart propios entonces ausentes; sin socket, PID de llama, marcador de briefing ni daemon/hook identificado. En las autorizaciones posteriores se prepararon los snapshots y la unidad inactiva descritos en las secciones 5 y 7.

Después de instalar los wrappers, doctor desde el wrapper privado retorna 0/READY con HOME sólo lectura y el checkout oculto. Los nueve archivos preexistentes conservan contenido, UID/GID, modo, inodo, dispositivo, tamaño y todos sus timestamps respecto del precheck del intento satisfactorio. Fingerprints privados sólo en memoria, nunca en los artefactos. Los subdirectorios anteriores conservan sus metadatos; la raíz `.siegfried` conserva propietario, grupo, modo e inodo, con cambio esperado de tamaño/mtime/ctime por la creación del directorio autorizado `bin`. No se añadió ni eliminó ningún evento, credencial o configuración.

**Estado posterior a esta corrección:** log real 0 bytes/0600 intacto. Configuración, Vault, secretos, wrappers y snapshots no cambiaron. El historial sí cambió durante la primera regresión: contenido/inodo/atime/mtime/ctime y mtime/ctime del directorio data; propietario y permisos conservados. No se intentó reparar. La comparación final demuestra ausencia de cambios adicionales al estado ya afectado, sin demostrar recuperación del original.

## 5. Snapshot privado

**Preparación real autorizada y completada.** Se usó el bloque exacto de la propuesta anterior, reutilizando `prepare_package` del instalador oficial. SHA-256 del comando, precheck, manifiestos completos, propietarios, modos e inodos registrados en `snapshot_deployment_actions` de ambos artefactos. Verificación UTC: 2026-10-09T18:22:04.140048+00:00.

| Destino real | Fuentes | Archivos con marcador | Directorios | Marcador |
|---|---:|---:|---:|---|
| `~/.local/share/siegfried-daemon` | 61 | 62 | 17 | F5.5 |
| `~/.local/share/siegfried-boot` | 57 | 58 | 14 | F5.3 |

Ambos destinos estaban ausentes; no se sobrescribió ni adoptó ninguna instalación previa. Las fuentes y herramientas/unidad coincidían con los hashes auditados antes de escribir. Copias limitadas a Python, wrappers, hook y los tres recursos KWin explícitos del paquete daemon. No se copió .git, __pycache__, GGUF, secrets.env, Vault, historial, agenda, logs, temporales ni datos personales. No se instaló KPackage ni se cargó un script KWin.

Todos los directorios creados son 0700; archivos 0600; `bin/siegfried`, `bin/siegfried-daemon` cuando corresponde y `scripts/boot_hook.py` 0700. UID/GID 1000/1000, ACL exclusivamente básicas, sin symlinks, hardlinks ni entradas ajenas. Verificados todos los elementos, no sólo el directorio raíz. Ambas copias coinciden exactamente con la selección autorizada más su marcador.

El runtime READY conserva todos sus archivos, contenido, UID/GID, modos, inodos y timestamps de archivo. Fingerprints de datos privados sólo en memoria y lectura O_NOATIME; no se persistieron ni imprimieron. No se crearon wrappers en `.siegfried/bin`, unidad systemd ni archivo XDG Autostart. No hubo fallo parcial ni se ejecutó rollback. Ante fallo, la operación estaba limitada a retirar sólo estos paquetes nuevos con marcador, propietario y allowlist comprobados; cualquier contenido desconocido se preservaría.

Modelos fuera de la instalación. Sonido opcional resuelto desde el runtime, sin copiar audio personal. Los recursos KWin están disponibles en el paquete privado, sin activación automática.

## 6. Integridad

Comparación de cada fuente y snapshot temporal, y posteriormente de las dos copias reales, byte a byte/SHA-256 PASS. Los manifiestos reales incluyen también el hash del marcador y los inodos/modos de todos los archivos. Los 61 hashes están en [GATE_F5_5_EVIDENCE.json](GATE_F5_5_EVIDENCE.json). El manifiesto [GATE_F5_5_RECOVERY.json](GATE_F5_5_RECOVERY.json) registra HEAD, metadatos, ACL, ausencia de destinos y hashes originales de los entrypoints, hook, instaladores y unidad desde HEAD.

Escaneo limitado de patrones de claves privadas y tokens largos en las fuentes desplegables: cero coincidencias. No es una prueba universal de ausencia de secretos. No se imprimió contenido privado. El snapshot protege modificaciones posteriores; los hashes locales y la comparación con Git no autentican por sí solos una fuente compartida frente a un actor con autoridad para sustituirla antes de la auditoría.

La corrección actual cambia sólo `src/siegfried/observability/logging.py` y `src/siegfried/daemon/app.py` respecto de las fuentes desplegadas. Se conservan los hashes originales del código instalado; `observability_correction.current_source_hashes` identifica el candidato probado. Ambas copias reales siguen con la versión anterior.

## 7. Instalación del daemon

**Unidad y wrappers reales instalados; ningún arranque mediante systemd.** Sólo hubo el piloto foreground autorizado posterior de sección 13. Precheck: hashes de los dos snapshots, fuentes y herramientas PASS; los tres archivos de destino y `.siegfried/bin` ausentes; sin instancia, socket o enlace de enable. Ningún destino ajeno se adoptó o sobrescribió. El instalador fue leído antes de --apply y su hash coincide con el código probado; no contiene start, enable o daemon-reload implícitos.

Comandos autorizados ejecutados en este orden, y repetidos una vez tras el rollback explicado abajo:

```bash
cd /media/okami/Mio/Siegfried
/usr/bin/python3 -B tools/install_user_service.py --dry-run
/usr/bin/python3 -B tools/install_user_service.py --apply
systemctl --user daemon-reload
systemctl --user show siegfried.service -p LoadState -p ActiveState -p MainPID -p FragmentPath -p UnitFileState
```

Todos retornaron 0. Resultado final:

```text
LoadState=loaded
ActiveState=inactive
MainPID=0
FragmentPath=/home/okami/.config/systemd/user/siegfried.service
UnitFileState=disabled
```

| Creación exclusiva | Modo | Propietario |
|---|---:|---|
| `~/.siegfried/bin` | 0700 | okami:okami |
| `~/.siegfried/bin/siegfried` | 0700 | okami:okami |
| `~/.siegfried/bin/siegfried-daemon` | 0700 | okami:okami |
| `~/.config/systemd/user/siegfried.service` | 0600 | okami:okami |

SHA-256 de los tres archivos coincide con las salidas oficiales auditadas; inodos, hashes y metadatos registrados en ambos manifiestos. Ancestros seguros, ACL básicas, sin symlinks/hardlinks, sin temporales restantes ni enlaces de habilitación. Todos los archivos systemd ajenos y las entradas XDG Autostart conservan sus metadatos. Ambos snapshots mantienen contenido, modos, UID/GID e inodos; no se recompilaron caches.

`systemd-analyze --user verify "$HOME/.config/systemd/user/siegfried.service"`: exit 0, sin errores de la unidad propia. Emite un aviso externo de la distribución: `/usr/lib/systemd/user/spice-vdagent.service:23: Unknown key 'StandardError' in section [Install], ignoring.` No se modificó ese archivo. La primera comprobación exigía stderr vacío y tomó el aviso como fallo; ejecutó rollback sólo de los tres archivos nuevos y bin vacío, seguido de daemon-reload. Se conservó el registro de ese intento. El verificador temporal se corrigió para admitir únicamente ese aviso conocido, sin debilitar el instalador ni los controles de permisos/integridad; el segundo intento pasó. El rollback no utilizó el desinstalador completo ni retiró snapshots preexistentes.

Independencia de los wrappers comprobada con bubblewrap: `/media` oculto, cwd `/`, HOME sólo lectura, `/run` y `/tmp` aislados, sin red ni PYTHONPATH del checkout. Doctor READY y ayuda exit 0. Probe interceptó execv de ambos wrappers y cargó sus entrypoints sin __main__; sus destinos y los 41 módulos importados pertenecen al snapshot privado. No se construyó SiegfriedDaemon ni se ejecutó REPL. El ExecStart efectivo consultado a systemd apunta exclusivamente al ejecutable privado. Sin procesos propios, sockets UNIX de Siegfried, PID de llama, marcador de briefing o entrada Autostart después de la operación.

`tools/install_user_service.py` simula por defecto en CLI; `--apply` escribe explícitamente. Instala snapshot independiente, wrappers 0700 y unidad 0600. Rechaza symlinks, padres compartidos, archivos ajenos/modificados, hardlinks y contenido diferente. Publicación de archivos sin sobrescritura mediante enlace atómico de un temporal propio. Reinstalar el mismo contenido no cambia sus inodos.

Unidad: `ExecStart=/usr/bin/python3 -B "%h/.local/share/siegfried-daemon/bin/siegfried-daemon"`; sin referencia ejecutable al checkout. Se preserva hardening, límites de reinicio y EX_CONFIG. `PYTHONDONTWRITEBYTECODE=1` impide caches de código en el paquete. Los wrappers de `.siegfried/bin` resuelven sólo el paquete privado relativo a la ubicación instalada.

`systemd-analyze --user verify` PASS en HOME temporal. Para esta comprobación se sustituye únicamente `%h` por el HOME del fixture, porque systemd usa el HOME real de la cuenta aun cambiando la variable de entorno. Se repitió la verificación en un archivo temporal de unidad que resuelve `%h` hacia HOME real, ahora con el ejecutable privado existente: PASS, sin publicar unidad. Esto valida sintaxis/rutas, **no demuestra que el sandbox de la unidad pueda arrancar en el sistema real**. La disponibilidad de namespaces y el arranque real siguen pendientes. Fallo de verify se devuelve como fallo, sin anunciar éxito; snapshot parcial recuperable mediante retry o uninstall.

## 8. Instalación del briefing

Instalación, integridad, `desktop-file-validate`, idempotencia, disable/enable y rollback temporales PASS en la regresión. Entrada real ausente; ambos snapshots reales ahora preparados. Después de corregir los ocho permisos, `python3 -B tools/install_user_service.py --dry-run` y `python3 -B tools/install_boot_briefing.py --dry-run` retornan **0**, sin escrituras. Los rechazos anteriores de permisos están resueltos. El dry-run del daemon comprueba preflight/fuentes, pero no sustituye la verificación real de publicación o de la unidad, ya probadas en fixtures. Desinstalar briefing conserva el daemon; desinstalar daemon conserva briefing y datos. `install_boot_briefing.py --apply` publica Autostart habilitado: requiere autorización de la etapa 6, no debe emplearse como mera preparación de código.

**Independencia real del checkout comprobada:** seis diagnósticos (doctor, ayuda CLI y hook --dry-run en cada copia) y dos probes de imports ejecutados con bubblewrap. `/media` oculto, HOME sólo lectura, `/run` y `/tmp` aislados, red/pid namespace separados, cwd `/`, Python -I -B -S y sin PYTHONPATH. Los ocho procesos terminaron 0; doctor READY desde ambas copias. Se verificó que `/media/okami/Mio/Siegfried` no existe dentro del probe y que todos los módulos Siegfried cargados provienen de src dentro de su propio snapshot. Wrapper daemon importado con run_name diferente de __main__, sin construir ni iniciar SiegfriedDaemon. Sin cachés, notificaciones, modelos ni REPL.

Plantilla .desktop apuntando al hook privado: desktop-file-validate PASS en archivo temporal, no publicado. Compatibilidad: ejecución comprobada con Python 3.14.4. AST de todas las fuentes desplegables aceptado con gramática Python 3.10; no equivale a ejecutar toda la suite en 3.10. Wrappers, daemon, CLI y hook ejecutados desde la instalación temporal, `cwd=/`, sin PYTHONPATH del checkout; hook/daemon existentes se prueban también con `-S`.

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

**Autostart y activación no ejecutados.** Servicio real instalado: `LoadState=loaded`, `ActiveState=inactive`, `MainPID=0`, `UnitFileState=disabled`; FragmentPath privado indicado en sección 7. No hay entry XDG Autostart ni enlace de enable; ambos snapshots preparados. Revisión limitada de argv del UID, sin imprimirlos: cero daemon/hook identificados. Se preserva cualquier instalación con otro nombre no identificada.

Comandos exactos propuestos por etapas, con autorizaciones distintas. Los ocho permisos están resueltos; avanzar requiere runtime READY, snapshot íntegro y ausencia de instancia/servicio previo. Las comprobaciones de permisos ya superadas se conservan como evidencia.

Preparación privada e instalación de unidad ya completadas por autorizaciones separadas; no repetirlas. El siguiente ensayo acotado y su parada están propuestos en la sección 20.

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

**Primer piloto manual con runtime real, ya realizado; no repetido para esta corrección.** PID 214562, dos threads y ningún hijo en las muestras. Foreground desde snapshot privado, KWin/sesión=0, sin QUERY/LLM/Cloud, tareas, timers, audio o notificaciones. PING/STATUS iniciales y PING posterior OK. Reposo 60,00048 s después de 3 s de calentamiento; 13 muestras separadas por aproximadamente 5 s.

| Métrica real | Promedio | Máximo | Condiciones |
|---|---:|---:|---|
| CPU de un núcleo | 0,1833319 % | 0,400018 % por intervalo de 5 s | Delta utime+stime, 100 ticks/s, supervisor excluido |
| RSS | 29 564 928 bytes (29,56 MB) | 29 564 928 bytes | Constante en 13 lecturas |

Duración total 63,32806 s, debajo de 90 s. SIGTERM sólo al PID comprobado mediante pidfd: cierre en 0,16424 s; salida 0, sin SIGKILL ni timeout. Sin PID, hijos o socket propios; unidad loaded/inactive/MainPID=0/disabled y doctor READY. Escrituras: creación de log privado 0600 de 0 bytes y creación/retirada del socket 0600. El supervisor comparó contenido/UID/GID/modos/inodos/size/mtime/ctime de archivos previos antes de fallar el postcheck INFO; atime de lectura no certificado. Cliente oficial del supervisor procedía del checkout auditado, idéntico al instalado; daemon ejecutado exclusivamente desde snapshot privado.

CPU promedio y RSS máximo cumplen en esta ventana, pero algunos intervalos CPU superan 0,2 %. Resolución de 10 ms, host no controlado y muestra de un minuto: no demuestra consumo sostenido ni consumo bajo systemd/adaptadores/inferencia. La desviación anterior tras ruta cognitiva se conserva por separado.

## 14. Recuperación y estabilidad

Suite completa conserva pruebas de timers, alertas, KWin, sesión, degradación y daemon restart/IPC. Nuevas pruebas comprueban recuperación de snapshot parcial por fallo de verificación y conservación de instalaciones/datos ajenos. No se sustituyen por nuevos ensayos físicos: KWin real, notificaciones/alertas reales, desbloqueo, suspensión, reinicio systemd y red real siguen pendientes. No se provocó suspensión, bloqueo ni alteración de tareas reales.

## 15. Rollback

[Manifiesto de recuperación](GATE_F5_5_RECOVERY.json): rutas, propietarios, permisos, ACL, hashes de origen y destinos ausentes. No existe backup de contenido privado: el historial previo se preservó y sólo se crearon archivos oficiales ausentes. No eliminar los nuevos datos/configuración del runtime durante un rollback de componentes. No es una copia de seguridad del usuario.

**Rollback de la unidad inactiva y wrappers actuales:** el primer intento ejercitó su retirada limitada con éxito y recargó systemd; no se retiraron snapshots ni datos. Tras la instalación satisfactoria no se ejecutó otro rollback. Si el usuario solicita retirar sólo esta operación, usar el siguiente bloque después de comprobar que el servicio continúa inactivo/deshabilitado; se rechaza cualquier cambio de archivos o contenido desconocido. Los inodos y hashes corresponden al último intento satisfactorio del manifiesto. No se usa el uninstall completo del instalador, que retiraría también el snapshot daemon preexistente.

Comandos preparados para recuperación, **no ejecutar ahora**:

```bash
/usr/bin/python3 -B - <<'PY'
import hashlib, json, os, stat, subprocess
from pathlib import Path
home = Path.home()
manifest = Path('/media/okami/Mio/Siegfried/docs/gates/GATE_F5_5_RECOVERY.json')
action = json.loads(manifest.read_text())['inactive_service_installation_actions'][-1]
assert action['status'] == 'INSTALLED_INACTIVE_VERIFIED'
result = subprocess.run(['systemctl', '--user', 'show', 'siegfried.service',
                         '-p', 'ActiveState', '-p', 'MainPID', '-p', 'UnitFileState'],
                        capture_output=True, text=True, check=True)
state = dict(line.split('=', 1) for line in result.stdout.splitlines())
assert state == {'ActiveState': 'inactive', 'MainPID': '0', 'UnitFileState': 'disabled'}
expected = {home / '.siegfried/bin/siegfried', home / '.siegfried/bin/siegfried-daemon',
            home / '.config/systemd/user/siegfried.service'}
assert {Path(p) for p in action['created_files']} == expected
bin_dir = home / '.siegfried/bin'
assert set(bin_dir.iterdir()) == {p for p in expected if p.parent == bin_dir}
directory = action['created_directories'][str(bin_dir)]
assert bin_dir.lstat().st_ino == directory['inode'] and not bin_dir.is_symlink()
for path in expected:
    record = action['created_files'][str(path)]
    for parent in path.parents:
        info = parent.lstat()
        assert stat.S_ISDIR(info.st_mode) and not parent.is_symlink()
        assert info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_nlink == 1
    assert (info.st_uid, info.st_gid, info.st_ino) == (record['uid'], record['gid'], record['inode'])
    assert f'{stat.S_IMODE(info.st_mode):04o}' == record['mode']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
for path in expected:
    path.unlink()
bin_dir.rmdir()
PY
systemctl --user daemon-reload
systemctl --user show siegfried.service -p LoadState -p ActiveState -p MainPID
```

Después de esa retirada, resultado esperado not-found/inactive/MainPID=0; verificar que ambos snapshots y los datos del runtime se conservan y no aparecen procesos/socket/Autostart. Las modificaciones de mtime/ctime de los dos directorios padres al crear/retirar sus entradas son esperadas. Este bloque no restaura permisos de HOME ni modifica datos privados.

Rollback completo futuro propuesto, **no ejecutado contra HOME real y requiere instrucción expresa de retirada de todos los componentes**. Comprobar antes que unidad, entry y paquetes coinciden con los propios registrados y que fueron creados por este piloto; no deshabilitar instalaciones previas/ajenas. Desactivar primero los componentes realmente instalados y no ejecutar operaciones sobre componentes ausentes:

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

**676 pruebas PASS, 38,102 s, cero fallos/errores/omitidas**, después de la última modificación de producción: 655 baseline conservadas + 21 nuevas (16 unitarias y 5 integraciones). Específicas: 21 PASS en 0,017 s; repetidas en harness final, exit 0. Las dos fixtures heredadas sólo cambian a paths=self.paths; ninguna prueba/assertion eliminada.

```bash
PYTHONPATH=src python3 -B -m unittest tests.unit.test_logging_f55 tests.integration.test_daemon_logging_f55
PYTHONPATH=src timeout -s INT -k 5s 180s python3 -X faulthandler -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src python3 tools/benchmark.py
PYTHONPATH=src python3 tools/benchmark_boot_briefing.py
git diff --check
```

Harness final con SIEGFRIED_HOME/XDG_RUNTIME_DIR temporales, adaptadores=0 y PYTHONDONTWRITEBYTECODE=1. Sólo systemd-analyze verify recibe XDG_RUNTIME_DIR real mediante un wrapper temporal que hace exec del binario oficial; sin start/enable/unidades reales. Con XDG temporal hubo 8 errores de verify de entorno; no se omitió el chequeo. Otro intento con bubblewrap remapeó root a 65534 y dio 27 errores de fixtures/instalación; no se debilitó ningún control de propietarios para hacerlo pasar. La ejecución final preserva propietarios y pasó.

Primera regresión: un fallo de fixture nueva porque mkdir(0755) quedó 0700 por umask heredado; se fijó explícitamente chmod(0755) sólo en el directorio temporal. También se detectó cambio real de history por dos fixtures antiguas, detallado en sección 21. El PASS final no elimina ese incidente.

Logs temporales finales `/tmp/siegfried-f55-logging-{specific,regression,benchmark,boot_benchmark}.log`. Warnings de fixtures no equivalen a errores del runtime; no se certifica suite sin warnings. Después del último cambio de producto sólo se preparó/probó el helper administrativo en /tmp y se actualizaron documentos.

## 17. SLOs

Benchmarks secuenciales después de la regresión final, ambos exit 0. Cinco SLO PASS:

| Harness | Muestras | P95 ms | Objetivo ms | Resultado |
|---|---:|---:|---:|---|
| Fast-Path | 2500 | 0,0016 | <10 | PASS |
| Vault append/fsync, ext4 del checkout | 200 | 2,3089 | <10 | PASS |
| CLI cold-start | 50 | 29,65 | <50 | PASS |
| Routing | 1000 | 0,0049 | <1 | PASS |
| Histórico heurístico, 20k | 100 | 2,2912 | <10 | PASS |
| Histórico exhaustivo | 25 | 8,2242 | Observacional | Sin SLO propio |

Briefing: composición 1000 muestras, P95 0,000339 ms; hook cold-start dry-run 25 muestras, P95 115,283389 ms. notification_visible_ms=null. Datos sintéticos/caché caliente; no demuestra login→visible, VRAM, precisión universal o consumo sostenido. CPU/RSS del primer piloto permanece separado en sección 13.

## 18. Riesgos residuales

- Logging corregido desplegado al snapshot daemon (sección 25) y verificado operativo mediante systemd. Snapshot Briefing conservado con fuentes autorizadas.
- Incidente de history: UNRESOLVED tras auditoría de sección 22; contenido actual de 6056 bytes estrictamente preservado sin modificaciones.
- Verificación de servicio: `siegfried.service` habilitado e iniciado; `ActiveState=active`, `UnitFileState=enabled`, `MainPID=253749`.
- Autostart XDG: `org.siegfried.BootBriefing.desktop` habilitado (`Hidden=false`), validado con `desktop-file-validate`.
- Falta probar un inicio de sesión real (logout/login) con observación humana para declarar el PASS definitivo de Gate F5.5.
- Mismo UID y fuente compartida forman parte del límite de confianza local; hashes no son firma independiente.
- No hay estimación de descanso entre reinicios sin evidencia contractual suficiente; se conserva F5.2 y se omite esa duración.
- No se demuestra exactly-once global ante pérdida del marcador o crash entre Notify y marcado.
- Python 3.10 sólo comprobado sintácticamente, ejecución real en 3.14.4.

## 19. Estado final Git

HEAD observado: 8cba97bdfaf759eb1d6bee156b6cbf3af4622e00, rama main. El agente no ejecutó commits ni push. En este hito sólo están modificados los tres artefactos docs/gates/GATE_F5_5_{REPORT.md,EVIDENCE.json,RECOVERY.json}; staging vacío, sin modificación de producto/tests/instaladores. Los 61 hashes auditados coinciden con el código instalado. git diff --check y JSON de ambos manifiestos PASS.

Daemon activo (`MainPID=253749`), servicio `enabled` en `default.target.wants`; Autostart `enabled` (`Hidden=false`) en `~/.config/autostart/`. Runtime READY y datos privados preservados.

## 20. Veredicto y siguiente autorización

**ACTIVATION_PASS_PENDING_LOGIN_TRIAL.** (No se declara PASS final del Gate F5.5 hasta probar un inicio de sesión real). Confirmación humana de PASS integral del REPL y visual KDE recibida y registrada (sección 39). Activación persistente controlada de `systemd --user` y `XDG Autostart` completada con preflight obligatorio, procedimiento de recuperación verificado y postchecks satisfactorios (sección 40). Daemon activo (`MainPID=253749`), servicio `enabled`, Autostart `enabled`. Sin procesos duplicados ni fugas privadas. Historial real intacto. Pendiente exclusivamente la prueba manual de logout/login por el usuario bajo autorización independiente (sección 41).

**Propuesta histórica, posteriormente aplicada y verificada en sección 25:** actualizar sólo src/siegfried/observability/logging.py y src/siegfried/daemon/app.py del snapshot daemon. Briefing, marcadores, wrappers, unidad y runtime fuera de la actualización. El instalador general rechaza correctamente contenido diferente: no usar --apply/uninstall para reemplazar el snapshot ni borrarlo.

El helper `/tmp/siegfried-f55-update-daemon-observability.py` comprueba todo el paquete anterior, hashes candidatos, propietario/modos, ausencia de instancia/socket y servicio inactive/disabled. Guarda únicamente esas dos fuentes antiguas en backup privado 0700/0600 en /tmp. Publica con reemplazo atómico y checks de hash/inodo; ante fallo parcial restaura sólo la fuente propia si conserva su identidad/hash nuevo. No toca archivos ajenos, datos, servicios o logging real. Su hash se registra en evidencia. Dry-run real PASS sin escrituras. Fixture HOME temporal PASS: sólo dos fuentes actualizadas, permisos 0600, rechazo de preimagen cambiada y rollback tras fallo inyectado en primer archivo.

Comandos concretos para futura autorización de código:

```bash
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py --apply
```

Después: SHA-256 de 61 fuentes/marcador, imports sin checkout, permisos/propietarios y doctor READY. Registrar nuevos inodos y backup en recuperación, manteniendo por separado inventario original.

**Nueva autorización operativa separada:** ensayo de logging de aproximadamente 20 s, con límite de 90 s siguiente. No repetir la medición CPU/RSS. Cliente oficial desde snapshot corregido en proceso separado, PING/STATUS mientras esté activo, SIGTERM sólo al PID identificado al acabar; esperar 5 s y SIGKILL únicamente de respaldo. Adaptadores=0; sin REPL, modelos, Cloud, tareas/timers/alertas.

```bash
(
  umask 077
  cd /
  SIEGFRIED_ENABLE_KWIN=0 SIEGFRIED_ENABLE_SESSION=0 \
  SIEGFRIED_HOME="$HOME/.siegfried" XDG_RUNTIME_DIR="/run/user/$(id -u)" \
    timeout -s TERM -k 5s 90s /usr/bin/python3 -B "$HOME/.siegfried/bin/siegfried-daemon"
)
```

Autorizar únicamente append operativo al log existente; conservar prefijo/inodo/modo, sin truncarlo. Verificar inicio, disponibilidad IPC, parada y cierre INFO en el segmento añadido, con mensajes permitidos y scan de secretos sin volcar contenido privado. Comparar runtime antes/después, socket 0600, ausencia de residuos y unidad todavía inactive/disabled. No ejecutar systemctl start en esta autorización; queda para después.

El incidente del historial necesita decisión separada. Aprobar código/ensayo no autoriza restaurar/truncar/eliminar/reinicializar history; los manifiestos no contienen una copia original de su contenido.

## 21. Corrección puntual de observabilidad e incidente de validación

**Causa del producto:** setup_logger(level=INFO) no hacía logger.setLevel(level); logger NOTSET heredaba WARNING de root. Handlers NOTSET no recuperan registros descartados antes de emitirse. Se creaba el archivo, pero los cuatro INFO nunca llegaban. Probe sobre copia anterior: solicitado 20/INFO, efectivo 30/WARNING, INFO deshabilitado. No era buffering ni fallo de SIGTERM.

**Corrección mínima:** nivel aplicado a logger y handlers, manteniendo arquitectura/append/formatter. Apertura O_APPEND|O_CREAT|O_NOFOLLOW|O_NONBLOCK, modo 0600 y checks de archivo regular, propietario, modo y nlink=1. Directorio 0700 sin chmod de destinos anteriores ni mkdir de padres indiscriminado. Destinos inseguros conservados con aviso genérico en consola. Reconfigurar cierra handlers anteriores y conserva prefijo/inodo sin duplicados; guard POSIX existente reutilizado, sin dependencias nuevas.

Activar INFO hacía visibles nombres privados de tareas en dos llamadas: se eliminaron del log, conservando los datos de dominio/Vault. Excepciones crudas de agenda/runtime/transiciones/telemetría/inferencia/motores se sustituyeron por mensajes constantes; no depender de reconocer un prompt arbitrario dentro del error. Formatter añade redacción de sk-*, Bearer, credenciales etiquetadas/variables de entorno, PEM completo/incompleto y campos estructurados sensibles con secuencias anidadas. Escapa CR/LF/NUL y no serializa traceback privado. Evidencia sobre llamadas operativas y categorías sintéticas probadas; regex no es detector universal de cualquier texto privado. Sin cambios IPC/Event/Config ni del contenido de tareas/eventos.

**Causa del verificador:** línea 176 de /tmp/siegfried-f55-manual-pilot.py exigía len(lines)==len(messages), cuatro frente a cero. Expectativa INFO correcta para logging operativo; AssertionError no demuestra caída del daemon. Script mezclaba esa validación con veredicto global y dejó STOPPED_UNEXPECTED después de medir/parar correctamente. Se distinguen defecto de emisión del producto y clasificación incompleta del supervisor. Se completó con lectura como PASS_WITH_DEVIATIONS, sin repetir piloto. Próximo verificador debe registrar por separado IPC/recursos/parada/preservación/observabilidad y analizar sólo el segmento añadido.

**Cobertura nueva:** 16 unitarias de niveles, umask, append/reconfiguración, rechazo/preservación de modo inseguro, symlink de archivo/directorio, hardlink, FIFO, directorio abierto, redacción texto/JSON/anidados/PEM, traceback, inyección de líneas y traversal. Cinco integraciones temporales: arranque/parada/reinicio y datos conservados, tarea privada en Vault excluida de log, fallo startup, warning agenda y excepción QUERY simulada sin motores/red. Notifier/audio stubs. Total 21 nuevas, 676 PASS.

**Incidente real:** primera regresión detectó con huellas protegidas en RAM y metadatos el reemplazo/cambio de contenido de .siegfried/data/.history; UID/GID/modo conservados. Dos fixtures F2.4/F2.4.1 omitían paths en SiegfriedREPL y guardaban readline en runtime por defecto. Corregidas con paths temporales explícitos sin reducir cobertura. Primera ejecución violó la restricción de no modificar runtime real; PASS final no certifica preservación del original. Usuario informado. No se imprimió contenido ni se persistieron hashes privados; no se restauró/truncó/eliminó el historial y no hay backup de contenido original en esta operación. Recuperación requiere copia previa confirmada y autorización específica; no reconstruir entradas ni inventar información perdida.

Último harness con runtime por defecto temporal y fixtures REPL inyectadas: comparación antes/después de todo runtime ya afectado y ambos snapshots PASS, incluyendo contenidos/inodos/modos/propietarios/size/mtime/ctime y atime de archivos. Log real 0 bytes/0600 intacto en todos los intentos. Configuración/Vault/secretos no cambiaron. Ningún daemon real reiniciado, código instalado actualizado ni servicio/Autostart/KWin activado durante esta corrección.

## 22. Auditoría no destructiva del incidente del historial

**Resultado: UNRESOLVED.** Ruta exacta: `/home/okami/.siegfried/data/.history`.
La evidencia anterior y posterior demuestra reemplazo del archivo y cambio de bytes;
no permite determinar qué entradas se añadieron, eliminaron o modificaron.
No se declara el historial intacto ni se justifica una restauración sin copia verificable.

| Propiedad | Antes: manifiesto de inicialización | Actual: auditoría |
|---|---|---|
| Tamaño | 6056 bytes | 6056 bytes |
| Inodo / dispositivo | 1553351 / 48 | 1575285 / 48 |
| UID:GID | 1000:1000 | 1000:1000 |
| Modo / enlaces | 0600 / 1 | 0600 / 1 |
| atime_ns | 1791567786809446667 | 1791578291320899670 |
| mtime_ns | 1791567786809446667 | 1791578291320899670 |
| ctime_ns | 1791567786810600146 | 1791578291321719340 |
| SHA-256 | No conservado; comparación anterior en RAM detectó cambio | Registrado en ambos manifiestos bajo history_incident_audit_and_isolation |

Fuente anterior: `runtime_initialization_actions[0].existing_metadata['data/.history']`
y su post_metadata idéntico. El verificador de la regresión inicial informó diferencias
de contenido/inodo/timestamps; no guardó los bytes ni el digest anterior. No puede
hacerse ahora una comparación criptográfica del estado original ni reconstruirlo
partiendo del tamaño/hash actual. Igual longitud no demuestra append exclusivo,
ausencia de truncamiento, ni conservación de las mismas entradas.

La implementación REPL usa readline.read_history_file, límite de 1000 y
write_history_file al salir. Ambas fixtures defectuosas compartían readline y omitían
paths. El archivo actual es UTF-8 válido y contiene 1000 entradas; los conteos y
huellas se calcularon localmente sin imprimir entradas. Alcanzar ese límite no
prueba por sí solo cuántas entradas anteriores se perdieron. No hay evidencia
suficiente para clasificar el cambio semántico como sólo adición o truncamiento.

Lectura obligatoria O_NOATIME|O_NOFOLLOW y lock compartido no bloqueante;
si no se pueden cumplir las condiciones se detiene, sin fallback. Búsqueda limitada
con O_NOATIME en directorios, sin seguir enlaces: ~/.siegfried/data,
~/.siegfried/{backup,backups}, ~/{Backups,backups}, ~/.local/share/Trash/files,
/home/.snapshots, /.snapshots y /timeshift/snapshots. Cero candidatos; sólo data
y Trash/files existían en esas ubicaciones. No hubo corte por límites de búsqueda.
Esto no excluye respaldos externos, otras rutas o snapshots no accesibles por esos
nombres. Se solicitó al usuario una posible ubicación adicional; no se restaura ni
se crea una supuesta versión anterior.

Metadatos completos del historial antes/después de esta auditoría idénticos,
incluido atime. Tampoco cambió durante la regresión aislada posterior. Ninguna
escritura, restauración o modificación de permisos del historial. Configuración,
Vault, secretos, log existente y snapshots sin cambios adicionales según comparación
protegida de contenido/metadatos; esa preservación posterior no repara el incidente.

Recuperación posible únicamente si aparece una copia anterior confiable y se
verifica su procedencia/fecha/integridad. Sin ella no pueden recuperarse con certeza
las entradas originales. Se conserva el archivo actual; **UNRESOLVED** expresa la
incertidumbre del alcance, sin convertirla en PRESERVED_WITH_DEVIATIONS.

## 23. Aislamiento de regresión y verificación posterior

Las dos fixtures corregidas usan SiegfriedREPL(self.client, paths=self.paths), líneas
135 de test_functional_integration_f24.py y 631 de test_ipc_backpressure_f241.py.
Se revisaron todos los constructores REPL: inyección explícita de rutas temporales.
Las referencias Path.home en tests de init/M0.1 consultan existencia o comparan
rutas; no se encontró otro escritor demostrado hacia el runtime real. La revisión
estática por sí sola no garantiza ausencia futura de rutas por defecto.

Se añade tools/run_isolated_tests.py y tools/test_isolation/sitecustomize.py.
El lanzador crea HOME, SIEGFRIED_HOME, TMPDIR y las cinco variables XDG en fixtures
antes de importar el producto. Usa entorno mínimo, sin credenciales ni variables
D-Bus/Wayland/display de la sesión. Instala Landlock heredable por subprocesos,
ABI mínimo 3 (este kernel: 8), con lectura/escritura/truncamiento/creación/remoción
de archivos del HOME real y /run/user/1000 denegados. Conserva UID/GID reales y
propietario root de ejecutables; no debilita los validadores de instalación. Falla
cerrado si el kernel o arquitectura no soportan la protección requerida.

Hook de auditoría Python complementario: rechaza listados privados y mutaciones
chmod/chown/utime/unlink/rename/link/mkdir/rmdir/truncate/xattr, incluso con dir_fd.
**Límite:** Landlock no media stat ni todas las operaciones de metadatos; stat sigue
posible y las llamadas nativas de metadatos no quedan universalmente bloqueadas.
Es protección para regresiones confiables, no sandbox de código hostil. No se
afirma ocultamiento universal de nombres/metadatos o aislamiento completo de red.
No hay descriptores privados preabiertos transmitidos al proceso de pruebas.

Dos nuevas pruebas verifican: ruta capturada en un módulo antes de instalar la
política, lectura/append/truncamiento/listado/chmod/utime/unlink rechazados;
subproceso python -S sin hook tampoco puede leer el archivo protegido; escrituras
en fixtures permitidas; defaults importados resuelven sólo HOME/XDG temporales.
El launcher inicial tuvo 8 errores de verify por bloquear el /tmp que usa systemd
y un fallo de ubicación TMPDIR dentro de HOME. Se corrigió sólo el harness:
fixture protegida separada en /var/tmp y TMPDIR fuera del HOME temporal.
Runtime y snapshots se conservaron también en ese intento fallido.

systemd-analyze --user verify ejecuta de verdad, con HOME/XDG temporales y
SYSTEMD_UNIT_PATH=/usr/lib/systemd/user:/lib/systemd/user; exit 0. No hay wrapper
que reintroduzca el runtime real. Persiste únicamente el aviso externo ya conocido
de spice-vdagent. No hubo start/enable/daemon-reload ni cambio de unidad real.

Comandos de regresión futuros (README actualizado):

```bash
timeout -s INT -k 5s 180s /usr/bin/python3 -B tools/run_isolated_tests.py
/usr/bin/python3 -B tools/run_isolated_tests.py tools/benchmark.py
/usr/bin/python3 -B tools/run_isolated_tests.py tools/benchmark_boot_briefing.py
```

Resultado: específicas 23 PASS; suite completa **678 PASS en 37,392 s**, sin skips
ni reducción del baseline 676. Cinco SLOs PASS, P95 ms: router 0,0016; Vault 2,4125;
CLI 31,20; routing 0,0047; histórico heurístico 1,9760. Exhaustivo 8,1379 ms,
observacional. Briefing composición P95 0,000337 ms; cold-start dry-run P95
115,143013 ms; tiempo visible null. No sustituyen medición operativa CPU/RSS/login.

Comparación SHA en RAM con O_NOATIME y metadatos de archivos antes/después:
runtime actual y ambos snapshots sin diferencias adicionales. Ningún daemon real
iniciado ni datos reales usados como fixture. Evidencias/hashes de logs sintéticos
y herramientas conservados en ambos manifiestos. Logs detallados en /tmp;
resultados esenciales persistidos en estos artefactos. git diff --check PASS.

## 24. Propuesta de actualización limitada (histórica; aplicada en sección 25)

Dry-run repetido: exactamente dos fuentes distintas de la copia instalada:

- src/siegfried/observability/logging.py
- src/siegfried/daemon/app.py

Las otras 59 fuentes del paquete daemon coinciden con el inventario auditado.
No se despliegan fixtures, herramientas de pruebas, README ni reportes.
No se actualizan snapshot Boot Briefing, wrappers, unidad, marcadores ni runtime.
El helper de sección 20 conserva el hash registrado; preflight real PASS sin
escrituras. Self-test repetido en HOME temporal PASS: backup privado 0700/0600,
SHA antiguos/nuevos, sólo dos reemplazos, rechazo de preimagen alterada y rollback
tras fallo inyectado en el primer reemplazo. Rollback se limita a identidad/hash
propios; no emplea desinstalación general ni restaura datos del usuario.

Comandos exactos preparados para una autorización posterior, **no ejecutados con
--apply**:

```bash
cd /media/okami/Mio/Siegfried
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py --apply
```

Registrar/verificar hash del helper de nuevo antes de usarlo: /tmp no es un almacén
permanente. Si falta o difiere, detener y preparar una nueva propuesta revisable.
La propuesta no incluye arranque ni ensayo operativo. En el momento de esta propuesta el Gate permanecía BLOCKED
por el incidente UNRESOLVED y las autorizaciones/validaciones operativas pendientes;
ninguna aprobación de código autoriza reparar el historial.

## 25. Actualización limitada autorizada del snapshot daemon

**PASS — desplegado y verificado sin arranque.** El usuario decidió conservar el
historial actual sin modificaciones y mantener el incidente UNRESOLVED. Esa
instrucción no convierte la integridad anterior en PASS ni autoriza reconstrucción.
No se realizó ninguna restauración ni nueva inspección de entradas del historial.

Comprobación inicial del actualizador: SHA-256 registrado
`2b4dce5227b531bbd0862eecccf3c1bf9b7bc97af0315b40eb6a1e0af67a2ae8`,
coincidente antes de ejecutarlo. Se revisó su alcance: dos fuentes permitidas,
backup de código público, reemplazos atómicos y rollback de identidad/hash propio.
Fuentes del repositorio: 61 hashes coincidentes con el inventario candidato;
snapshot previo: archivos identificados por hash/inodo/propietario/modo/tamaño del
manifiesto de despliegue, sin archivos desconocidos. Dry-run PASS sin escrituras.

Antes de apply: unidad loaded/inactive/MainPID=0/disabled; ningún daemon/socket;
doctor READY mediante wrapper privado en namespace readonly con /media oculto,
/run y /tmp temporales, red/PID aislados y Python -I -B -S. Comparación de runtime
tras doctor sin cambios de contenido/metadatos, incluido atime de archivos.

Se ejecutaron exactamente, desde /media/okami/Mio/Siegfried:

```bash
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py
/usr/bin/python3 -B /tmp/siegfried-f55-update-daemon-observability.py --apply
```

Únicas fuentes reemplazadas:

- src/siegfried/observability/logging.py
- src/siegfried/daemon/app.py

SHA-256 instalado idéntico a fuente auditada para ambas. UID:GID 1000:1000,
modo 0600, nlink=1, sin enlaces ni ACL adicionales. Las otras 59 fuentes y el
marcador conservan contenido/inodo/propietarios/modos/tamaño/mtime/ctime; no se
agregaron/eliminaron archivos. El reemplazo atómico modifica naturalmente mtime/
ctime de sus dos directorios padres. Lecturas de código público del helper pueden
actualizar atime; no hay cambios de atime en archivos privados del runtime.

Backup real privado: `/tmp/siegfried-f55-daemon-code-backup-7f30rzuk`, directorio
0700 y dos fuentes anteriores más manifest.json 0600, UID:GID 1000:1000, ACL básicas.
Hashes antiguos/nuevos e inodos antes/después registrados en
limited_observability_deployment_actions de ambos manifiestos. Inventario original
preservado; current_daemon_snapshot contiene la copia actual. No se sobrescriben
las evidencias históricas. Backup en /tmp: conservarlo antes de una limpieza o
reinicio si se necesita rollback posterior; no se supone almacenamiento permanente.

Verificación con checkout oculto: 41 módulos importados desde snapshot privado;
no se construyó daemon, llamó setup_logger ni abrió REPL. Doctor posterior READY.
Runtime completo, incluido historial actual/Vault/configuración/secretos/log vacío,
conservó SHA en RAM y todos los metadatos de archivos. Snapshot Boot Briefing,
wrappers, unidad y XDG Autostart preservados. Estado final loaded/inactive/
MainPID=0/disabled; sin procesos/socket. No hubo start/enable/reload, adaptadores,
Cloud/inferencia, modificación de código del repositorio ni commits/push.

No hubo error ni rollback. Si fuese necesario, restaurar únicamente las dos
fuentes propias desde este backup, comprobando hash/inodo nuevos antes de cada
reemplazo. No usar uninstall general. El actualizador validado es de una sola
preimagen: no repetir --apply ni su dry-run de actualización sobre la nueva copia.

La regresión 678 PASS/cinco SLOs y git diff --check de sección 23 siguen aplicables:
se desplegaron exactamente esas fuentes auditadas, sin nueva modificación del
producto. La emisión INFO real aún requiere ensayo autorizado mediante systemd.
El Gate permanece BLOCKED por validaciones operativas pendientes; el incidente
histórico permanece UNRESOLVED por decisión explícita de conservación.

## 26. Propuesta histórica de ensayo systemd (ejecutado en sección 27)

**Preparación, sin ejecución.** Autorizar por separado start/stop de la unidad
existente, durante como máximo 90 s, manteniendo UnitFileState=disabled. No se
crean unidades/drop-ins, ni se modifica el entorno del manager o Autostart.
Antes del ensayo comprobar nuevamente hashes instalados/unidad, doctor READY,
ausencia de instancia/socket y flags KWin/sesión no habilitados. Consulta actual:
manager sin flags =1; unidad sólo PYTHONUNBUFFERED/PYTHONDONTWRITEBYTECODE,
sin drop-ins. Si el entorno cambia y habilita adaptadores, detener el ensayo.

Operaciones concretas a autorizar:

```bash
systemctl --user start siegfried.service
systemctl --user show siegfried.service \
  -p LoadState -p ActiveState -p SubState -p MainPID -p UnitFileState \
  -p ControlGroup -p NRestarts -p Result
# Mientras está activo: cliente oficial privado PING/STATUS y muestras CPU/RSS.
systemctl --user stop siegfried.service
systemctl --user show siegfried.service \
  -p LoadState -p ActiveState -p SubState -p MainPID -p UnitFileState \
  -p NRestarts -p Result -p ExecMainCode -p ExecMainStatus
```

Organización del supervisor: registrar prefijo/inodo/mode 0600 del log existente
con O_NOATIME, identificar MainPID/starttime/cgroup; esperar IPC como máximo 10 s;
consultar sólo PING/STATUS desde imports privados en un proceso sin REPL, mostrando
sólo OK/FAIL y ningún payload personal. Medir reposo con muestras cada 5 s durante
60 s, CPU por deltas de ticks/tiempo sobre un núcleo y RSS por páginas; informar
media/máximo, intervalo y número de muestras. Comprobar PID estable/NRestarts=0,
socket 0600 y adaptadores deshabilitados. No ejecutar QUERY/tasks/timers/alertas.

Usar try/finally para detener sólo esta unidad si el supervisor la inició;
watchdog de 90 s para que un fallo del verificador no deje el servicio activo.
Parada con systemctl stop (SIGTERM, TimeoutStopSec=5s/KillMode=control-group de la
unidad existente), registrar cualquier SIGKILL de respaldo sin presentarlo como
parada limpia. No pkill/killall. Verificar cgroup vacío, sin PID/hijos/socket,
unidad inactive/disabled, doctor READY y configuración/Vault/historial/secretos
conservados. Separar resultados IPC/recursos/parada/preservación/logging.

Analizar sólo el segmento añadido al log, preservando prefijo e inodo: cuatro
mensajes operativos INFO de inicio/disponibilidad IPC/parada/cierre; permisos 0600,
sanitización sin volcar texto privado. El archivo ya existe vacío: se verifica
append/apertura segura, no se elimina para simular creación. Creación desde
archivo ausente ya tiene cobertura aislada. No abrir el REPL ni probar login/KDE.

Este ensayo no está autorizado por el despliegue de código y no fue ejecutado.

## 27. Ensayo operativo autorizado mediante systemd --user

**PASS_WITH_DEVIATIONS.** Funcionalidad, logging y parada limpia satisfactorios;
RSS ligeramente superior al objetivo. Un único arranque, sin enable/restart ni
cambios de unidad/entorno permanente. Se mantuvo deshabilitado durante el ensayo.

Precondiciones PASS: LoadState=loaded, ActiveState=inactive, MainPID=0,
UnitFileState=disabled; doctor READY en namespace readonly; 61 fuentes y los dos
hashes corregidos coincidentes con snapshot auditado, identidades/modos verificados;
ExecStart /usr/bin/python3 -B /home/okami/.local/share/siegfried-daemon/bin/siegfried-daemon;
ninguna instancia/socket. Unidad original sin drop-ins/EnvironmentFiles, switches
KWin/SESSION no =1 en entorno efectivo; confirmado también en environ del PID.
Log previo 0 bytes/0600, propietario 1000:1000; metadatos registrados sin volcar
contenido ni persistir huellas privadas.

Se ejecutó systemctl --user start siegfried.service. Watchdog independiente armado
antes del start: orden stop a los 80 s, dejando margen para TimeoutStopSec=5s y el
límite total de 90 s. try/finally envió stop al concluir normalmente; watchdog
cancelado sólo tras comprobar unidad inactiva. No fue necesario activar su stop
por deadline. Duración desde orden start hasta stop confirmado: **63,395086 s**.
Supervisor público /tmp/siegfried-f55-systemd-trial.py, hash registrado en evidencia.

Unidad active, MainPID **236021**, PID estable, NRestarts=0 y socket 0600 del usuario.
PING y STATUS iniciales y tras muestreo devolvieron OK desde cliente oficial
importado de snapshot privado, en procesos separados sin REPL. El cierre de los
clientes no detuvo el daemon. Sólo esas dos consultas; sin tareas/temporizadores/
agenda/QUERY, modelos, Cloud ni adaptadores habilitados. Ausencia de hijos y
ninguna inferencia solicitada; no se presenta esto como traza exhaustiva de red.

### Recursos reales en reposo

13 muestras, 12 intervalos objetivo de 5 s, **60,000572 s**, tras 3 s de estabilización;
2 threads y 0 hijos en todas las muestras. CPU por deltas utime+stime del PID,
100 ticks/s y tiempo monotónico, porcentaje respecto a un núcleo. RSS por statm
multiplicado por tamaño de página; se usan MB decimales.

| Métrica | Resultado | Objetivo / interpretación |
|---|---|---|
| CPU media | 0,199998093 % | Numéricamente < 0,2 %, casi en el límite; sin margen robusto demostrado |
| CPU máximo por intervalo de 5 s | 0,400017095 % | Pico de intervalo; no medición instantánea |
| RSS medio | 30.011.392 bytes = 30,011392 MB | Superior a 30 MB |
| RSS máximo | 30.011.392 bytes = 30,011392 MB | Exceso 11.392 bytes, aproximadamente 0,038 % |
| Procesos hijos / threads máximos | 0 / 2 | Sin motor pesado iniciado |

La CPU acumuló 12 ticks en 60 s; resolución de un tick sobre ese intervalo
aproximadamente 0,016667 puntos porcentuales. No utilizar la diferencia ínfima
frente a 0,2 % como certificación con margen. RSS no se reclasifica usando MiB.
No se alteró arquitectura ni se repitió el ensayo para obtener un número favorable.

### Logging corregido y parada

INFO comprobado mientras estaba activo. Archivo daemon.log conservó prefijo,
inodo, UID:GID, modo 0600 y nlink=1; sólo append de cuatro mensajes exactos:
inicio, disponibilidad IPC, parada y cierre correcto. No warnings/errors ni
campos extra fuera de la allowlist. Validación del segmento nuevo y journald en
RAM, sin imprimir contenido privado: cuatro registros del InvocationID del ensayo,
PRIORITY=6 y mensajes operativos coincidentes. Sin tokens/prompts/títulos privados/
excepciones sensibles en esos registros; no se afirma detector universal regex.

systemctl --user stop siegfried.service terminó en **0,124992 s**, exit 0.
Result=success, unidad inactive/dead/MainPID=0/disabled, NRestarts=0. Sin PID/hijos,
cgroup residual o socket. Sin SIGKILL del supervisor, sin eventos kill/timeout en
journald y parada muy por debajo de los 5 s del respaldo systemd. Doctor final READY.

El verificador inicial marcó clean_shutdown=false al exigir ExecMainCode=1 después
de stop. La unidad inactiva devolvió ExecMainCode=0, ExecMainStatus=0 e InvocationID
vacío: esa lectura no demuestra fallo de cierre. Se corrigió **la evaluación de
evidencia mediante lectura**, contrastando Result=success, stop exit 0, mensaje de
cierre correcto, journald y ausencia de residuos. Defecto del verificador; ningún
defecto de parada demostrado. No se reinició el servicio ni se modificó producto.

### Preservación, desviaciones y resultado

Comparación O_NOATIME antes/después: únicos cambios del runtime en daemon.log,
size/mtime/ctime/hash por append operativo. Configuración, secretos, Vault, agenda
historial y demás archivos conservaron contenido, inodo, propietario, permisos y
timestamps; historial actual también atime. Incidente previo continúa UNRESOLVED,
sin restauración/reconstrucción. Snapshots/unidad/wrappers/Autostart sin alteración
funcional; acceso de lectura a fuentes públicas puede actualizar atime. No commits,
push, activación persistente ni consulta/inferencia de motores.

Veredicto del ensayo PASS_WITH_DEVIATIONS por RSS 30,011392 MB y CPU sin margen
robusto. Logging/IPC/parada/preservación PASS. Gate F5.5 todavía BLOCKED para cierre
operativo por verificaciones visuales/login y autorizaciones pendientes. Regresión
no repetida: ninguna modificación de código del producto; baseline 678 PASS y
cinco micro-SLOs aislados conservados, distintos de estos objetivos de recursos.

## 28. Propuestas previas para visual KDE y Autostart (supersedidas por sección 29)

**Preparadas, no ejecutadas.** Primero autorización visual para una notificación
pública del presenter del snapshot Boot Briefing y un launcher inocuo de Konsole
que sólo escribe un marcador sintético temporal. No abre REPL real ni inicia
servicio. El helper deriva del verificador oficial; única adaptación: REPO apunta
al snapshot privado Boot, con imports desde allí. Hash en ambos manifiestos;
comprobarlo otra vez antes de ejecutarlo. Usar obligatoriamente --manual:

```bash
timeout -s INT -k 5s 30s /usr/bin/python3 -B /tmp/siegfried-f55-visual-manual.py --manual
```

Antes: Wayland, Konsole, pantalla desbloqueada, propietario D-Bus del usuario,
org.freedesktop.Notifications, capacidades actions y propiedad Inhibited. Si DND
está activo, conservarlo y reportar impedimento; no modificarlo. Solicitar al
usuario confirmación humana de aparición, texto público correcto, botón «Abrir
sesión» y clic físico. --manual no invoca ActionInvoked programáticamente. Notificación
expira a los 20 s; espera 21 s. Aceptación Notify no equivale a aparición visual.
Apertura del REPL real desde acción necesita otra autorización y daemon disponible;
ninguna aprobación del launcher inocuo la incluye.

**Posteriormente**, sólo con autorización independiente de persistencia y después
de revisar la desviación RSS/CPU y la evidencia visual, preparar activación del
servicio deshabilitado y entrada XDG propia. No se cierra sesión automáticamente.
Dry-run oficial de Autostart PASS y desktop-file-validate PASS en fixture temporal,
exec exclusivamente privado, sin escribir entrada real.

El checkout contiene dos fuentes nuevas respecto del Boot snapshot conservado:
usar el instalador CLI por defecto intentaría preparar un snapshot diferente y
rechaza correctamente esa preimagen. Se preparó la API oficial con repository
igual al Boot privado auditado (57 hashes comprobados); así se conserva sin
recopiarlo. No actualizar silenciosamente el Boot ni debilitar validadores.

Comandos propuestos, **sin ejecución**:

```bash
systemctl --user enable siegfried.service
cd /media/okami/Mio/Siegfried
/usr/bin/python3 -B - <<'PY_AUTOSTART'
from pathlib import Path
from tools.install_boot_briefing import manage_autostart
home = Path.home()
boot = home / '.local/share/siegfried-boot'
manage_autostart(home, operation='install', dry_run=False, repository=boot)
PY_AUTOSTART
```

Al autorizar, volver a validar hashes/identidades, destino libre, desktop, permiso
HOME y una instancia; registrar sólo la entrada propia y el enlace enable. No
start ni enable --now: ensayo de login por el usuario, con nueva comprobación.
Rollback de esa etapa: systemctl --user disable siegfried.service y desactivar la
entrada propia mediante manage_autostart(operation='disable', repository=boot),
con controles de ownership. No uninstall general: borraría el Boot preexistente.
Preservar ambos snapshots y datos privados. Si se necesita detener el servicio o
probar REPL/login, delimitarlo explícitamente en esa autorización futura.

Ningún Notify visual, Konsole, REPL, enable ni archivo XDG real ejecutado/creado
por esta preparación. El usuario conserva la decisión sobre ambos pasos.

## 29. Precomprobación visual autorizada: bloqueo antes de Notify

**BLOCKED_BEFORE_NOTIFY.** El helper coincide exactamente con su SHA auditado
f4ba98a887654c57875824959c3e5d22992b5e0314a288938e5e6803c7a71586,
pero su cuerpo público fija «Buenos días, Señor. Prueba F5.3 sin información
privada.». No llama al compositor de Boot Briefing. A las 17:09 hora local Lima,
el compositor instalado selecciona «Buenas tardes» (regla 12 <= hora < 19).
Ejecutar esta versión no validaría el saludo determinista correspondiente solicitado.
La preparación anterior debió detectar esta limitación; defecto del helper de
validación, sin defecto demostrado del compositor del producto.

Precondiciones de lectura superadas: unidad loaded/inactive/MainPID=0/disabled,
ningún daemon/socket, sesión 3 KDE/Wayland active=yes/state=active, ScreenSaver
GetActive=false. Servicio Notifications propietario :1.31, UID 1000,
Inhibited=false, GetCapabilities incluye actions. No se modificó DND ni ninguna
propiedad. Konsole disponible. Snapshot Boot 57 fuentes y daemon 61 coincidentes
con sus inventarios auditados. Doctor READY con checkout oculto y runtime readonly.

Revisión del helper: imports del Boot privado; --manual espera clic humano, no
agenda tarea para InvokeAction programático. Launcher usa un script temporal que
sólo escribe marcador opened; no REPL ni acceso a tareas/agenda/Vault/historial.
Presenter dispone de CloseNotification/desuscripción y TemporaryDirectory retira
su fixture al salir. Ninguno de esos recursos llegó a crearse: no se ejecutó el
comando visual, envió Notify, abrió Konsole ni creó marcador. Visibilidad, texto,
botón/clic y duplicados humanos pendientes; no se pidió al usuario confirmar una
notificación que no fue enviada.

No se cambió código por la restricción explícita «No cambiar código». Corrección
mínima **propuesta, no aplicada**: sólo /tmp/siegfried-f55-visual-manual.py, usar el
compositor puro ya instalado con defaults públicos, sin cargar datos del runtime:

```python
from datetime import datetime
from siegfried.core.briefing import BriefingContext, compose_briefing
# Antes del único presenter.send:
public_text = compose_briefing(BriefingContext(now=datetime.now()))
assert presenter.send(public_text, public_text, expiry_ms=20000 if manual else 3000)
```

Composición sin tarea, descanso, clima o datos personales; selecciona el saludo
según hora local y mantiene el daemon no confirmado. No modifica repositorio,
snapshots ni runtime. Preimagen y SHA del candidato calculado únicamente en RAM
registrados en proposed_helper_correction de ambos manifiestos. No se escribió
una nueva versión. Necesita autorización explícita para adaptar el helper,
verificar su nuevo SHA y ejecutar el mismo ensayo manual de máximo 30 s.

Comparación O_NOATIME antes/después de las comprobaciones: runtime, ambos snapshots
y Autostart conservaron contenidos y metadatos de archivos. Entrada propia ausente;
historial actual preservado, incidente anterior UNRESOLVED. Ningún proceso de prueba
porque no se lanzó. Sin commits/push ni cambios de producto. git diff --check PASS.

### Alcance futuro de Autostart actualizado

La instrucción más reciente pide entrada XDG **inicialmente deshabilitada**,
únicamente después de PASS visual. Queda supersedida la propuesta anterior de
systemctl enable + instalación habilitada de sección 28. No habilitar servicio,
no iniciar sesión real ni activar briefing en esa próxima autorización.

El instalador existente manage_autostart(operation='install') publica Hidden=false;
no cumple por sí solo el estado inicial pedido. Tampoco publicar enabled y después
disable: existiría una fase habilitada. La preparación posterior debe garantizar
publicación inicial atómica de la plantilla oficial desktop_content(boot,
enabled=False), con propietario/modo 0600, hash/identidad y rollback de sólo la
entrada creada; validar en HOME temporal antes de solicitar autorización concreta.
No se implementó ni instaló esa adaptación ahora, ni se amplió el alcance del
ensayo visual. Preservar Boot privado y no usar uninstall general sobre él.

## 30. Helper corregido y única ejecución visual manual autorizada

**PENDING_HUMAN_CONFIRMATION_NO_ACTION_MARKER.** No se declara PASS visual.
El usuario autorizó expresamente adaptar sólo el helper temporal y una ejecución.
Preimagen SHA verificada contra ambos manifiestos. Se inspeccionaron firma y retorno
reales desde snapshot Boot: BriefingContext(now, available=False, title='Señor',
critical_task=None, expose_task=False, unlocked=False, rest=None, weather=None)
construye un contexto; compose_briefing(context: BriefingContext) -> str. Retorno
str confirmado en ejecución pura, no asumido. Horarios sintéticos 08:00/15:00/22:00
produjeron Buenos días/Buenas tardes/Buenas noches, sin enviar notificaciones.

Único cambio del helper: imports datetime/compositor oficial y sustitución del
cuerpo fijo por compose_briefing(BriefingContext(now=datetime.now())) inmediatamente
antes de presenter.send. Sin lectura de agenda/historial/Vault/secretos por esa
composición, sin clima, descanso o tareas. Ningún otro comportamiento modificado.
SHA nuevo **61df96aa4056b239ba18306e73302529c49edc71661aea2e1c4c86eacf6725c6**,
coincidente con candidato previamente calculado; registrado/verificado antes de
la ejecución. Fuentes del repositorio y snapshots sin modificaciones.

Aviso explícito en commentary y terminal de estar frente a pantalla durante 20 s;
solicitud humana asíncrona de visibilidad, saludo/texto, botón, clic físico/apertura
y duplicados antes de enviar. Comando autorizado ejecutado **una sola vez**:

```bash
timeout -s INT -k 5s 30s /usr/bin/python3 -B /tmp/siegfried-f55-visual-manual.py --manual
```

Inicio 2026-10-09T22:15:33.420512 UTC / 17:15:33.420524 hora Lima. Saludo del contexto
público: Buenas tardes, Señor. Daemon no confirmado. Notificación ID 214, acciones
soportadas, Inhibited=false; aceptación Notify a los 50,012 ms. Ese tiempo sólo
mide aceptación API, **no aparición visual**. Una llamada inicial Notify, sin
clima/reenvío solicitado; ausencia de duplicados en pantalla necesita confirmación
humana. No ActionInvoked programático: --manual conserva el camino de clic físico.

Duración del proceso **19,965349 s**, exit 0, stderr vacío. Resultado del helper:
native_notify=PASS, native_action=PENDING_HUMAN_ACTION,
konsole_test_executable=PENDING_HUMAN_ACTION, action_count=0, automatic_launch=false.
No se recibió acción ni se creó marcador; no consta apertura de Konsole por esta
prueba. La expiración/cierre terminó el ensayo, dentro del máximo de 30 s. No
se repitió ni se reemplazó la evidencia ausente por un clic simulado.

Verificaciones posteriores: unidad loaded/inactive/MainPID=0/disabled, ningún
daemon/socket ni proceso residual asociado al helper/launcher; ninguna fixture
siegfried-f53-* nueva restante. Doctor READY en namespace readonly. Runtime conservó
metadatos de archivos, incluido historial actual; no se leyó/fingerprintó contenido
privado desde supervisor para esta comparación. No se afirma nueva comparación
criptográfica privada. Ambos snapshots conservaron SHA y todos los metadatos;
Autostart preservado/ausente. No datos modificados, daemon/REPL iniciados, DND
cambiado, Cloud, enable, commits/push. Incidente histórico UNRESOLVED conservado.

Confirmación humana pendiente: visibilidad, texto/saludo, botón, clic, Konsole y
duplicados. Una respuesta posterior puede aclarar el resultado observado, pero
no sustituye el marcador técnico inexistente. No certificar la acción hasta
resolver esa discrepancia si el usuario informa que sí hizo clic. Cualquier
nuevo ensayo requerirá autorización: se agotó la única ejecución de esta ronda.

La propuesta de entrada XDG inicialmente Hidden=true sigue condicionada a completar
la validación visual. No instalar/habilitar nada ahora; tampoco ejecutar la
propuesta anterior que publicaba Hidden=false o habilitaba el servicio.

## 31. Repetición visual única autorizada (PASS humano en sección 32)

**TECHNICAL_PASS_PENDING_HUMAN_CONFIRMATION.** El usuario informó estar frente a
pantalla y autorizó una sola repetición. Helper sin cambios de código, SHA
61df96aa4056b239ba18306e73302529c49edc71661aea2e1c4c86eacf6725c6
coincidente con manifiestos. KDE/Wayland activo y desbloqueado, Inhibited=false,
unidad loaded/inactive/MainPID=0/disabled antes del ensayo. Sin instancia/socket.
Se conservó la evidencia pública anterior en
/tmp/siegfried-f55-visual-execution-result-round1.json; no se repitió automáticamente
ni se confundieron las dos autorizaciones. Se reutilizó el supervisor sin editarlo.

Comando ejecutado una vez en esta ronda: timeout -s INT -k 5s 30s /usr/bin/python3 -B
/tmp/siegfried-f55-visual-manual.py --manual. Inicio UTC
2026-10-09T22:27:56.705994 / Lima 17:27:56.706008. Cuerpo del compositor público con
saludo Buenas tardes, sin tareas/clima/descanso. ID de notificación 215, actions=true,
Inhibited=false. Aceptación API 46,346 ms; duración total **4,816471 s**, exit 0,
stderr vacío. Ninguno de esos tiempos demuestra latencia de aparición visual.

Resultado técnico: native_notify=PASS, native_action=PASS,
konsole_test_executable=PASS, **action_count=1**, automatic_launch=false. El callback
KDE inició el launcher inocuo y el helper comprobó exit 0 más marcador opened
antes de retirar su fixture. No se invocó ActionInvoked programáticamente. La señal
y el marcador corroboran la acción técnica, pero la certificación del clic físico,
visibilidad/saludo/texto/botón/apertura y duplicados requiere respuesta humana.
Se solicitó esa confirmación al acabar; permanece pendiente.

Después: 0 procesos asociados al helper/launcher y 0 nuevas fixtures restantes,
unidad inactive/disabled/MainPID=0, ningún daemon/socket, doctor READY readonly.
Runtime conservó metadatos sin lectura de contenido privado por supervisor;
ambos snapshots conservaron SHA y metadatos. Autostart ausente, historial actual
sin modificaciones, incidente anterior UNRESOLVED. Sin código/servicios/REPL
real/DND/Cloud habilitados, commits o push. git diff --check PASS.

No se declara PASS visual hasta la confirmación humana. No habrá tercer ensayo sin
nueva autorización. La preparación de instalación inicial Hidden=true conserva
su condición de validación visual; ninguna autorización de persistencia implícita.

## 32. Confirmación humana: validación visual KDE PASS

**PASS de presentación y acción con launcher inocuo.** El usuario confirma la
prueba satisfactoria: una sola notificación visible, botón «Abrir sesión» funcional,
clic físico y apertura de Konsole. El cierre casi inmediato era esperado por el
marcador sintético. Se combina con ID 215/action_count=1/marcador comprobado de
sección 31. No se simula el clic ni se atribuye precisión subsegundo a la observación.
No se confirmó una latencia visual medida; visible_ms permanece desconocido.

Esto certifica presenter/acción KDE con contenido público y launcher inocuo,
**no el REPL real**, autoinicio, briefing por login ni rendimiento desde login.
La primera ronda sin clic de sección 30 permanece registrada; no se transforma
retrospectivamente en PASS. Incidente del historial sigue UNRESOLVED, actual
conservado por instrucción explícita. Autostart ausente y servicio disabled.

## 33. Preparación del ensayo REPL real: pendiente de autorización

**PREPARED_DRY_RUN_PASS.** Snapshot daemon: 61 hashes coincidentes con el inventario
actual; runtime doctor READY ejecutado readonly; unidad loaded/inactive/MainPID=0/
disabled; ausencia de daemon/socket. Wrapper ~/.siegfried/bin/siegfried resuelve
únicamente a ~/.local/share/siegfried-daemon/bin/siegfried. No código del checkout
compartido utilizado como entrypoint persistente. PING/STATUS vivos todavía no
se ejecutan: requieren el arranque específicamente pendiente de autorización.

Hallazgo relevante: el REPL añade comandos deterministas a readline y al salir
llama _save_history → write_history_file + chmod. Ejecutarlo sin aislamiento
modificaría el historial real, contrario al alcance. No se cambió el producto,
readline ni las configuraciones para evitarlo. Se prepara una vista de archivos
para el proceso REPL: root readonly, /media oculto, /tmp temporal y tmpfs vacío
sobre ~/.siegfried/data. El historial se crea únicamente dentro de ese tmpfs y
se descarta al salir; el original y Vault quedan ocultos. El daemon temporal
seguiría usando su runtime real, con append INFO operativo en daemon.log.

Prueba aislada de preparación PASS: socket echo sintético en fixture de
/run/user/1000 (ningún daemon real), cliente desde namespace conectado al socket
del host; historial/Vault originales ocultos, historial sintético escribible en
tmpfs y checkout inexistente. Fixture cerrada/eliminada. Wrapper privado --help
PASS en ese namespace; doctor sobre runtime original readonly READY. No se
inició el REPL real ni se abrió Konsole para esta prueba de preparación.

Supervisor revisable: /tmp/siegfried-f55-repl-supervised.py, SHA registrado en
real_repl_pilot_preparation de ambos manifiestos. Sin argumentos sólo dry-run;
--run inicia el ensayo exclusivamente después de autorización. Dry-run real PASS.
Secuencia preparada: watchdog independiente en sesión separada antes del start,
stop a los 80 s dejando margen para máximo 90 s; try/finally adicional. Unidad
identificada por hash/ExecStart, sin drop-ins o switches de adaptadores habilitados.
Sólo start/stop, sin enable/restart/reload o cambio de variables del manager.

Tras autorizar: PING/STATUS desde cliente oficial privado antes del REPL;
Konsole --separate con proceso foreground real ~/.siegfried/bin/siegfried dentro
del namespace. Se limpia PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONINSPECT y se
inhibe user-site/bytecode en el entorno del cliente, sin afectar manager.
Confirmación humana del prompt [Siegfried] > estable y terminal interactiva.
Usuario prueba sólo ayuda, status y salir; no tareas ni consultas de inferencia.
No se captura ni guarda transcripción con posibles payloads personales.

Al salir: Konsole exit 0, unidad con mismo MainPID active, nuevos PING/STATUS OK;
sólo entonces stop propio. Si el usuario no termina antes de 70 s, el supervisor
cierra únicamente el grupo Konsole creado e identificado y detiene unidad propia.
Watchdog asegura orden stop independiente si el supervisor falla. Comprobar
cgroup/PID/socket ausentes, doctor READY, unidad inactive/disabled y comparación
O_NOATIME de datos/configuración/Vault/historial; sólo append operacional del log
permitido. Historial real conservará contenido/inodo/propietario/modo/timestamps.

Comando concreto pendiente de autorización específica para iniciar servicio,
append operativo al log, abrir/cerrar Konsole real y consultar IPC:

```bash
/usr/bin/python3 -B /tmp/siegfried-f55-repl-supervised.py --run
```

No ejecutado. No nuevos features, commits/push, cambios del repositorio/snapshots,
servicios iniciados, datos modificados ni Autostart. La confirmación humana del
REPL y su independencia del daemon permanecen pendientes; Gate no cerrado.
Sólo tras PASS se preparará publicación de entrada XDG inicialmente Hidden=true,
sin instalación habilitada transitoria ni systemctl enable.

## 34. Ensayo REPL real autorizado: técnico PASS, humano pendiente

**TECHNICAL_PASS_PENDING_HUMAN_CONFIRMATION.** El usuario autorizó arranque
systemd/REPL por máximo 90 s, append operativo y comandos ayuda/status/salir.
Supervisor SHA-256 0a5daca81cbb0bb1f133b7d32370d8aa011188837e88e985a075aea1776445e6
coincidente antes/después; contenido revisado sin modificación. Dry-run previo:
doctor READY, 61 fuentes SHA coincidentes, unidad loaded/inactive/MainPID=0/disabled,
ausencia de daemon/socket y switches KWin/SESSION no habilitados. Router privado
verificado: ayuda respuesta directa, status IPC STATUS; salir termina antes del
router. Ninguno de esos comandos activa QUERY, Cloud o local.

Avisos explícitos en commentary y terminal antes de abrir Konsole, con unos 70 s
para interacción y máximo total 90 s. Comando autorizado ejecutado una sola vez:

```bash
/usr/bin/python3 -B /tmp/siegfried-f55-repl-supervised.py --run
```

Inicio UTC 2026-10-09T22:40:24.002208. Watchdog independiente/sesión separada armado
antes de start, stop a los 80 s, más try/finally. No hubo trigger del watchdog.
Unidad real active/MainPID **245353**, NRestarts=0, disabled; PING y STATUS OK desde
cliente oficial privado antes de abrir Konsole. Konsole --separate PID **245358**,
foreground real wrapper ~/.siegfried/bin/siegfried → snapshot privado, checkout
oculto y archivos readonly; data en tmpfs vacío para historial efímero.
No lectura/copia/restauración del historial real por el REPL ni Vault visible
al cliente; no edición del producto ni de sus rutas por defecto.

Konsole permanecía viva tras 1 s; se informó al usuario del prompt esperado y los
tres comandos. Eso es evidencia de proceso interactivo vivo, no confirmación de
texto visual. Sin transcripción privada, no se atribuyen comandos tecleados sin
respuesta humana. Observación auxiliar inicialmente vio launcher bwrap y no un
argv con el entrypoint del paquete (por eso el primer flag fue false); éste usa
el wrapper privado permitido. No hay evidencia de ejecución desde checkout.

La terminal terminó con **exit 0**. Después de cerrar cliente, daemon todavía
active con el mismo MainPID; PING/STATUS posteriores OK. Sólo entonces systemctl
stop propio: final inactive/MainPID=0/disabled, Result=success, NRestarts=0.
Duración total start→stop/confirmación: **34,772388 s**. La salida no demuestra por
sí sola qué gesto/comando humano cerró el cliente: confirmar específicamente salir.

Sin PID Konsole/daemon ni procesos con wrappers/entrypoint real residuales,
cgroup vacío y socket retirado. Doctor final READY. Comparación O_NOATIME del
runtime: configuración/secretos/Vault/historial y demás archivos distintos de log
conservaron contenido y todos los metadatos; historial real incluido atime/inodo/
modo/owner. Único cambio permitido append al log, propietario/modo/inodo conservados.
Huellas privadas mantenidas sólo en RAM, no persistidas ni impresas. Historial
efímero descartado con namespace. Incidente histórico anterior sigue UNRESOLVED.
No tareas/temporizadores/agenda editados, inferencia solicitada, enables, Autostart,
commits/push ni nueva funcionalidad. Supervisor conserva su SHA original.

La confirmación humana del prompt estable, ayuda, status y salir permanece
pendiente; no se declara todavía PASS del REPL. Se solicitó al usuario durante
el ensayo. Ausencia de interacción no se trataría como defecto del producto;
en esta ejecución no venció el plazo. No reintento automático autorizado.
Tras confirmación satisfactoria, preparar únicamente entrada XDG inicialmente
Hidden=true y validación, sin fase enabled transitoria ni inicio de sesión real.

## 35. Confirmación humana del REPL real: PASS

El usuario confirma prompt [Siegfried] > visible, ayuda/status correctos y salida
con salir. Se combina con exit 0 de Konsole, daemon con mismo PID y PING/STATUS
posteriores OK, parada en 34,772388 s y preservación completa del runtime de sección
34. **PASS del REPL auténtico**, separado del launcher inocuo del PASS visual.
No se repite el ensayo. Incidente histórico UNRESOLVED, historial actual preservado.

## 36. XDG Autostart inicialmente deshabilitado: PASS

Instalación realizada el 2026-10-09 a las 22:52 UTC, sin ejecutar hook, Konsole,
daemon ni activar servicios. Precondiciones y cierre: doctor READY; unidad loaded,
inactive, MainPID=0, disabled; sin daemon ni socket. Ambos snapshots íntegros,
propietarios/modos/ACL seguros y ausencia previa de entradas propias/en conflicto.

El instalador oficial admite ahora `--disabled` exclusivamente para install y
`manage_autostart(..., enabled=False)`. El valor predeterminado habilitado se
conserva. Primera publicación atómica sin sobrescritura: enlace del temporal
completo y sincronizado, ya con Hidden=true y modo 0600; retirada del temporal y
fsync del directorio. Un destino ajeno aparecido concurrentemente se rechaza.
No se modifica código de producto ni contratos congelados.

Dry-run CLI `python3 -B tools/install_boot_briefing.py --disabled --dry-run` PASS.
Para preservar el snapshot Boot existente (incluidas sus fuentes históricas),
la publicación real utiliza la API oficial con repository apuntando a ese mismo
snapshot, tras comparar sus 57 fuentes contra el manifiesto. No copia ni actualiza
fuentes. Operación ejecutada:

```python
from pathlib import Path
from tools.install_boot_briefing import manage_autostart
manage_autostart(Path.home(), operation="install", dry_run=False,
                 repository=Path.home()/".local/share/siegfried-boot", enabled=False)
```

Archivo único creado: `/home/okami/.config/autostart/org.siegfried.BootBriefing.desktop`.
UID/GID 1000:1000, modo 0600, inodo 1582265, 321 bytes, un enlace, ACL POSIX básica.
SHA-256: `985a5a4d5d5334b36ced08dff2446b39c56de8a16689f6cc6af4bf7045da2da6`.
Type=Application, Hidden=true desde primera publicación, Terminal=false,
OnlyShowIn=KDE; Exec entrecomillado hacia `/usr/bin/python3` y
`/home/okami/.local/share/siegfried-boot/scripts/boot_hook.py`.
Sin referencias ejecutables al checkout compartido ni contenido privado.
`desktop-file-validate` PASS. Segunda operación idéntica: unchanged, mismo
contenido y todos los metadatos; no entradas duplicadas ni recursos temporales.

Once pruebas nuevas cubren publicación inicialmente deshabilitada, CLI dry-run y
apply temporal, permisos, idempotencia, instalaciones habilitadas existentes,
rechazo de ajenos/symlinks, carrera de publicación, fallo parcial, rollback sólo
propio y operaciones incompatibles. Sin ejecución del hook y con runtime temporal
preservado. Primera regresión detectó tres fallos de una fixture F5.4 que simulaba
fallo de os.replace: la publicación inicial usa ahora os.link. Se ajustó únicamente
el punto de inyección de esa fixture, conservando sus comprobaciones.
Resultado final: **111 pruebas específicas PASS; 689 pruebas PASS en 38,675 s**,
frente a 678 anteriores. Suite ejecutada exclusivamente con run_isolated_tests.py:
HOME/XDG temporales y Landlock ABI 8, acceso al runtime real denegado.
Cinco SLOs PASS (P95 router 0,0016 ms; Vault 2,5322 ms; CLI 29,95 ms;
orquestador 0,0048 ms; agregador heurístico 1,9743 ms).
Boot composición P95 0,000342 ms; hook dry-run P95 115,705 ms.
Estos tiempos no certifican latencia visible desde login ni consumo sostenido.

Comparaciones O_NOATIME antes/después: runtime completo y ambos snapshots sin
cambios de contenido ni metadatos; historial, Vault, configuración y secretos
preservados. Huellas privadas sólo en RAM. Incidente histórico sigue UNRESOLVED.
No restauración, nuevos permisos, enable, inferencia, commits ni push.
El supervisor temporal tuvo un error de importación antes de cualquier operación;
se corrigió su sys.path y la única publicación autorizada terminó correctamente.

Rollback preparado, no ejecutado: `/tmp/siegfried-f55-remove-own-autostart.py`.
Dry-run por defecto; --apply requiere instrucción de retirada. Comprueba archivo
regular sin symlink, propietario/grupo, inodo/dispositivo, SHA, modo y enlace único
contra el manifiesto, antes de unlink mediante descriptor del directorio seguro.
Retira exclusivamente este desktop intacto; conserva runtime, snapshots y unidad.
No usar uninstall general: afectaría el snapshot Boot preexistente.

## 37. Estado final y siguiente autorización independiente

**PASS de instalación XDG deshabilitada**. PASS humano visual y REPL real ya
registrados. Gate operativo completo pendiente de activación/login real y acción
notificación→REPL real; no se declara PASS global. Persisten el incidente histórico
UNRESOLVED y la desviación RSS del ensayo anterior.

Propuestas separadas, ninguna ejecutada:

1. Ensayo supervisado acotado: daemon temporal por systemd, watchdog previo,
   PING/STATUS y clic físico que abra REPL privado con historial temporal aislado;
   parada garantizada, servicio sigue disabled. Requiere autorización específica.
2. Tras aprobar ese ensayo, autorización para `systemctl --user enable siegfried.service`
   (sin --now) y API oficial `manage_autostart(Path.home(), operation="enable",
   dry_run=False, repository=Path.home()/".local/share/siegfried-boot")`.
   Verificar identidad previa y conservar respaldo del desktop Hidden=true para
   rollback; no arrancar componentes durante esa preparación.
3. Cierre e inicio de sesión manual por el usuario. Validar instancia única,
   briefing único, clic/REPL, privacidad, CPU/RSS y timestamps desde sesión gráfica
   efectiva hasta hook/Notify/aceptación KDE. Visibilidad humana registrada como
   observación humana; objetivo <2 s desde login pendiente, sin precisión inventada.

Estado Git: HEAD 8cba97bdfaf759eb1d6bee156b6cbf3af4622e00, rama main;
modificados los tres documentos F5.5, instalador y fixture F5.4; nuevo archivo de
once tests. Sin cambios en fuentes del daemon ni snapshots, sin commit/push.

## 38. Integración KDE → REPL real: evidencia técnica satisfactoria

Ensayo único autorizado, 2026-10-09 23:06:39 UTC, duración 36,823756 s (<120 s).
Preflight READY, snapshots SHA conforme manifiestos, unidad loaded/inactive/disabled,
MainPID=0, sin daemon/socket; integridad del desktop Hidden=true. KDE Wayland,
adaptador oficial confirma acciones e Inhibited=false sin modificar DND.
Supervisor temporal nuevo basado en los dos helpers auditados; código del producto
exclusivamente desde snapshots privados. Watchdog separado preparado antes de start,
parada a 105 s con timeout 8 s, finally y plazo de interacción 95 s.

Una llamada Notify pública mediante KDEBriefingPresenter privado; compositor oficial
BriefingContext(now=datetime.now()), hora local efectiva 18: saludo de tarde.
Sin lectura de agenda/Vault/historial/secretos para componer. Notification ID 216.
Recepción de ActionInvoked validada por adaptador; sin invocación programática.
Clic abre Konsole separado con wrapper privado ~/.siegfried/bin/siegfried vía bwrap:
checkout /media oculto, raíz readonly, data real oculta por tmpfs temporal y Python
-I -B -S. Historial real inaccesible en ese entorno. No modificación del producto.

Daemon PID 251324; PING/STATUS OK antes y después; socket 0600. Konsole PID 251383
terminó exit 0, daemon permaneció active con mismo PID tras cierre. Stop propio
finaliza unidad inactive/MainPID=0/disabled, Result=success, NRestarts=0; cgroup
vacío, sin daemon ni socket residual. Watchdog cancelado tras parada confirmada.
Doctor final READY. Comparación O_NOATIME: runtime no-log incluido historial,
Vault, secretos y configuración sin cambios de contenido ni metadatos; log operativo
conserva modo/owner/inodo. Huellas privadas sólo en RAM. Autostart y snapshots
conservan SHA auditados. Historial histórico UNRESOLVED, sin intento de restauración.

Resultado: **TECHNICAL_PASS_PENDING_HUMAN_CONFIRMATION**. Notify aceptado no
certifica visibilidad, prompt ni texto tecleado. Se solicita confirmar notificación
única/saludo, clic físico, prompt, ayuda/status/salir. No se transcribe entrada
privada ni se declara todavía PASS integral. Sin inferencia solicitada, tareas,
agenda o temporizadores editados, integración KWin/session, enables ni commits.
No cambios de código; baseline 689 pruebas/cinco SLOs conservado, sin repetir suite.
Propuestas de habilitación y login manual de sección 37 pendientes, sin ejecutar.

## 39. Confirmación humana: PASS integral de integración

El usuario confirma formalmente el PASS humano de la integración completa ensayada en la sección 38:

- Una sola notificación visible en KDE Plasma.
- Saludo correcto según la franja horaria.
- Botón «Abrir sesión» funcional.
- Konsole abrió el REPL real auténtico.
- Comandos `ayuda`, `status` y `salir` funcionaron correctamente.
- El daemon permaneció operativo tras el cierre del REPL.
- Runtime READY y datos privados preservados íntegramente.

Última regresión registrada: 689 PASS y cinco SLOs PASS.
El incidente histórico de `.history` permanece UNRESOLVED por decisión explícita de conservación; archivo actual conservado sin modificar.

## 40. Activación persistente controlada: systemd --user y XDG Autostart

Autorización exclusiva ejecutada para activar los componentes propios de Siegfried sin cerrar sesión ni reiniciar el equipo.

### 40.1 Preflight obligatorio (PASS)
- Runtime: `Estado: READY` verificado mediante `~/.siegfried/bin/siegfried doctor` (exit code 0).
- Snapshots privados:
  - `~/.local/share/siegfried-daemon`: 61 archivos auditados coincidentes con SHA-256 (0 discrepancias).
  - `~/.local/share/siegfried-boot`: 57 archivos auditados coincidentes con SHA-256 (0 discrepancias).
- Unidad `siegfried.service`: cargada (`LoadState=loaded`), inactiva (`ActiveState=inactive`), deshabilitada (`UnitFileState=disabled`), `MainPID=0`, `DropInPaths=` (sin drop-ins desconocidos ni servicios equivalentes).
- Sin procesos ni sockets previos de Siegfried (`pgrep` limpio, socket inexistente).
- Archivo XDG propio: `~/.config/autostart/org.siegfried.BootBriefing.desktop` presente, modo 0600, UID:GID 1000:1000, 1 hard link, `Hidden=true`, validado con `desktop-file-validate`.
- Sin enlaces simbólicos inesperados en runtime, snapshots ni configuración (0 symlinks).
- Sin activaciones duplicadas, drop-ins desconocidos ni servicios equivalentes.
- Adaptadores KWin (`isScriptLoaded("siegfried_focus_watcher")=0`) y Session deshabilitados (`SIEGFRIED_ENABLE_SESSION` y `SIEGFRIED_ENABLE_KWIN` ausentes).
- Ausencia de modelos GGUF o llamadas Cloud automáticas en el arranque.
- Flujo del Boot Briefing y launcher auditados: launcher invoca el REPL real desde el snapshot privado (`~/.local/share/siegfried-boot/bin/siegfried`) vía Konsole; Briefing es puramente determinista sin inferencia ni comandos destructivos.

### 40.2 Procedimiento de recuperación preparado y verificado
Se registró el estado previo completo en `/tmp/siegfried-f55-preflight-state.json`.
Se preparó y verificó en dry-run el script `/tmp/siegfried-f55-recovery-procedure.py`:
1. Deshabilita únicamente `siegfried.service`: `systemctl --user disable siegfried.service`.
2. Devuelve la entrada XDG a `Hidden=true` mediante API oficial: `manage_autostart(Path.home(), operation="disable", dry_run=False, repository=Path.home()/".local/share/siegfried-boot")`.
3. Detiene únicamente el daemon propio: `systemctl --user stop siegfried.service`.
4. Preserva snapshots, wrappers y datos del runtime sin eliminaciones.
5. No modifica servicios ni configuraciones ajenas.

### 40.3 Activación de systemd --user (PASS)
Ejecución de los comandos oficiales:
```bash
systemctl --user enable siegfried.service
systemctl --user start siegfried.service
```
Resultados y verificaciones:
- Enlace creado: `~/.config/systemd/user/default.target.wants/siegfried.service` → `~/.config/systemd/user/siegfried.service`.
- `LoadState=loaded`.
- `ActiveState=active` (running).
- `SubState=running`.
- `UnitFileState=enabled`.
- `MainPID=253749`.
- `Result=success`, `NRestarts=0`.
- IPC verificado:
  - `ping` → `[OK] {'pong': True, 'state': 'IDLE'}`.
  - `status` → `[OK] {'state': 'IDLE', ...}`.
- Socket privado: `/run/user/1000/siegfried.sock`, modo `0600`, UID:GID 1000:1000.
- Ausencia de procesos de inferencia no autorizados; proceso único en CGroup.
- Logging operativo: `~/.siegfried/logs/daemon.log` en modo 0600, append de dos líneas INFO operativas sin exposición de contenido privado o tokens.

### 40.4 Activación de XDG Autostart (PASS)
Ejecución mediante la operación oficial de `tools/install_boot_briefing.py`:
```python
from pathlib import Path
from tools.install_boot_briefing import manage_autostart
manage_autostart(Path.home(), operation="enable", dry_run=False,
                 repository=Path.home()/".local/share/siegfried-boot")
```
Resultados y verificaciones:
- Modificación atómica exclusiva de `~/.config/autostart/org.siegfried.BootBriefing.desktop`.
- `Hidden=false` establecido.
- Modo 0600, UID:GID 1000:1000, inodo 1583681, tamaño 322 bytes.
- SHA-256: `13a581d8ac2094a37b86f63322e9f1a1b3ee1e6cab4eaf8c8b5cbf65c438360d`.
- Validación sintáctica: `desktop-file-validate` PASS (código de salida 0).
- Confirmado: no se ejecutó inmediatamente el hook (`boot_hook` no ejecutado).
- Confirmado: no se generó notificación duplicada en esta sesión (`/run/user/1000/siegfried_briefing.lock` inexistente).
- Confirmado: no se abrió Konsole inesperadamente.

### 40.5 Postchecks globales (PASS)
- Daemon activo y habilitado (`UnitFileState=enabled`, `ActiveState=active`, `MainPID=253749`).
- XDG Autostart habilitado (`Hidden=false`, `desktop-file-validate` PASS).
- Runtime READY (`siegfried doctor` exit 0).
- PING y STATUS correctos vía socket Unix privado 0600.
- Historial real `.history`: contenido, tamaño (6056 B), inodo (1575285) y hash SHA-256 (`e88445ad86f3692be8b6f7545ac828de3d6a0349a5fa1fd4f823f151b4e5ead6`) rigurosamente intactos.
- Configuración (`core_profile.json`, `active_agenda.json`), secretos (`secrets.env`) y Vault (`siegfried_vault.jsonl`) preservados.
- Snapshots íntegros: 61/61 daemon y 57/57 boot verificados sin modificaciones.
- Ausencia de procesos o servicios duplicados.
- Ausencia de apertura inesperada de Konsole o notificaciones durante la activación.

## 41. Estado final y guía para la prueba manual de inicio de sesión

**PASS_PENDING_LOGIN_TRIAL.** La activación persistente de Siegfried quedó completada y verificada de manera controlada.
El servicio `siegfried.service` se encuentra activo y habilitado en `systemd --user`, y el Boot Briefing está habilitado en XDG Autostart de KDE.

Conforme a las directrices contractuales:
- **No se declara PASS final del Gate F5.5 hasta probar un inicio de sesión real (logout/login).**
- **No se autorizó cerrar sesión ni reiniciar automáticamente el equipo.**
- El sistema no fue modificado más allá de los componentes propios de Siegfried.
- No se realizaron commits ni push a git.

### Guía histórica de logout/login (sustituida por la decisión de sección 43; no ejecutar el clic):
Cuando el usuario decida realizar la prueba de cierre e inicio de sesión en KDE Plasma:
1. Cerrar la sesión gráfica actual de KDE Plasma y volver a iniciar sesión con la misma cuenta (`okami`).
2. Observar la aparición de una sola notificación en pantalla del Boot Briefing (tiempo esperado <2 s desde inicio de sesión).
3. Verificar el saludo apropiado según la hora local («Buenos días», «Buenas tardes» o «Buenas noches»).
4. Pulsar el botón «Abrir sesión» de la notificación.
5. Confirmar que se abre una ventana de Konsole con el REPL auténtico de Siegfried.
6. Probar en el REPL los comandos básicos: `ayuda`, `status` y `salir`.
7. Verificar tras salir del REPL que el servicio daemon permanece operativo mediante:
   ```bash
   systemctl --user status siegfried.service
   ```
8. Verificar que no existen procesos huérfanos o duplicados.

## 42. Gate final: preparación del login real y corrección de auditoría

La autorización actual cubre preparación, comprobaciones de lectura y herramienta
de diagnóstico. No cubre logout/reinicio, cambios nuevos de producción, paquetes,
commits, ni activación de LLM, Cloud, KWin o Session. Recuperación sólo revisada.

### 42.1 Evidencia previa al logout

`tools/verify_login_f55.py` consulta el servicio y proceso efectivo privado,
cgroup/duplicados, IPC PING/STATUS sin publicar payloads privados, socket 0600,
XDG habilitado con Exec privado y desktop-file-validate, runtime mediante validador
oficial del snapshot con lecturas O_NOATIME, ambos snapshots (61/57 hashes,
marcadores, allowlist, propietario/modos y enlaces), marcador SENT de sesión,
journald de la invocación propia sin publicar mensajes, CPU/RSS y estabilidad.
No arranca servicios, ejecuta hook, notifica, abre Konsole ni modifica runtime.

Precheck: servicio active/enabled, PID 253749, NRestarts=0, una sola instancia y
cgroup con un único PID, PING/STATUS PASS, socket 0600, Autostart Hidden=false,
Exec privado, doctor READY; snapshots 61/61 y 57/57 sin discrepancias. Journald
propio: dos entradas, ningún error priority 0–3 ni warning priority 4.
Ventana inicial de 20,009 s: 21 muestras, CPU 0,199907 % de un núcleo y RSS
constante 29.851.648 bytes. CPU con resolución 10 ms, sin margen suficiente para
certificar consumo sostenido <0,2 %. El ensayo anterior de systemd con RSS
30.011.392 bytes y exceso 11.392 bytes frente a 30 MB sigue vigente como desviación.
Las ventanas posteriores de comprobación están separadas en la evidencia JSON.

Referencia privada registrada antes del logout:
`/tmp/siegfried-f55-login-reference-final.json`, modo 0600, fuera del runtime.
No se copian sus fingerprints privados a los artefactos. Compara contenido,
UID/GID/modos/inodo/dispositivo/nlink/tamaño/mtime/ctime de archivos privados,
inventario y seguridad/identidad de directorios. Logs pueden crecer; se conserva
su identidad y seguridad. Atime no se exige igual tras login/REPL, que realizan
lecturas; las lecturas del diagnóstico usan O_NOATIME sin fallback permisivo.
Integridad actual PASS: cero cambios; historial coincide con la referencia de
activación y con la primera referencia de esta preparación. UNRESOLVED permanece.
Si falta la referencia después del login, declarar BLOCKED; nunca recrearla para
obtener artificialmente PASS. No reiniciar: /tmp no garantiza persistencia al reboot.

No hay SENT previo de esta sesión: esperado porque habilitar Autostart no ejecuta
el hook. Después del login se exige sesión gráfica nueva y se compara el marcador
con la clave efectiva de esa sesión. SENT acredita envío aceptado, no visibilidad,
notificación única ni clic físico. La ausencia del marcador será evidencia faltante
que deberá contrastarse con el usuario y los registros disponibles.
Tiempo disponible: diferencia entre creación de sesión logind y arranque del daemon,
con referencias monotónicas del mismo boot. No equivale a escritorio listo ni a
login → notificación visible. `login_to_visible_ms=null`; no inventar esa métrica.
Si el servicio sobrevivió al logout, documentar persistencia sin fingir nuevo arranque.

### 42.2 Recuperación y disable/rollback

Leído `/tmp/siegfried-f55-recovery-procedure.py`; no ejecutado. Deshabilita sólo
siegfried.service, modifica sólo el desktop propio a Hidden=true y detiene sólo
ese servicio; conserva runtime, snapshots y wrappers. Sus mensajes “Verified” no
son verificaciones automáticas. Dry-run genera contenido de instalación habilitada
y no prueba la mutación disable. No usar uninstall general para rollback de activación.

Ensayo real en HOME temporal mediante API oficial: disable escribió Hidden=true;
snapshot, historial sintético y archivo foreign.desktop conservaron contenido,
inodo/modo/mtime; ningún proceso iniciado. Fallo inyectado en publicación de enable:
entrada disabled previa y archivos ajenos preservados, temporales retirados.
Este ensayo no ejecuta rollback real ni promete atomicidad entre todas sus etapas.

Cinco pruebas del diagnóstico PASS: atime y metadatos conservados; rechazo de
symlink en hoja/ancestro y de tamaño excesivo; detección de cambio del historial
con igual tamaño y de archivo nuevo; append permitido de log pero cambio de
seguridad detectado. No se repitió la regresión completa: 689 PASS corresponde
a la última regresión aislada anterior, no a un resultado nuevo.

### 42.3 Corrección del inventario Git

La afirmación histórica de sección 18 sobre “sólo tres artefactos” corresponde a
ese hito y no describe el árbol actual. Estado real actual:

- Modificados: docs/gates/GATE_F5_5_REPORT.md, GATE_F5_5_EVIDENCE.json y GATE_F5_5_RECOVERY.json.
- Modificados preexistentes: tools/install_boot_briefing.py y tests/integration/test_operational_pilot_f54.py.
- Nuevo preexistente: tests/integration/test_autostart_disabled_f55.py.
- Nuevo en esta preparación: tools/verify_login_f55.py.

El instalador contiene enabled/--disabled y publicación inicial sin reemplazo;
la fixture F5.4 adapta el fallo de publicación a os.link. Ambos cambios estaban
presentes al comenzar este trabajo y no se atribuyen a esta preparación. Ningún
cambio nuevo en src, bin, scripts, unidad instalada o snapshots. Sin commits.
La captura completa de git status --short queda en ambos manifiestos.

### 42.4 Conflicto de preservación y prueba del REPL

La auditoría del REPL instalado muestra readline.add_history para ayuda/status/salir
y _save_history al salir, con escritura y chmod del historial real. Por tanto,
la prueba humana solicitada puede cambiar contenido y metadatos aunque el daemon
y el diagnóstico preserven datos. La prueba anterior evitó esto mediante historial
aislado; el launcher normal de Autostart usa el runtime real.

El usuario resolvió la elección: preservación estricta del historial real.
No se autoriza añadir, truncar, reemplazar ni reconstruir `.history` y no se cambia
el REPL. Cadena notificación→REPL auténtico y comandos aprobados técnicamente y
por el usuario en el ensayo anterior con historial temporal (secciones 38–39).
Esa evidencia se conserva; no se registra como repetida durante el nuevo login.
El launcher persistente no garantiza aislamiento: no pulsar Abrir sesión durante
esta prueba. Ver decisión vigente y alcance pendiente en sección 43.

### 42.5 Procedimiento posterior preparado

Logout/login exclusivamente manual desde KDE Plasma. El usuario observará
notificación única, saludo correcto y presencia del botón Abrir sesión, sin
pulsarlo ni abrir el REPL mediante el launcher persistente. Al regresar, ejecutar:

```bash
/usr/bin/python3 -B tools/verify_login_f55.py --post-login --reference /tmp/siegfried-f55-login-reference-final.json --seconds 20
```

Solicitar observaciones físicas del usuario; evaluar independientemente inicio
automático daemon, briefing, interacción REPL, integridad, recursos/estabilidad
y tiempos fiables. Actualizar los tres artefactos; emitir PASS sólo con criterios
obligatorios completos, PASS_WITH_DEVIATIONS con funcionalidad correcta y
excepciones documentadas, BLOCKED ante evidencia indispensable ausente, FAIL ante
fallo real. Histórico de RSS y del historial siempre visible. Hasta entonces,
Gate BLOCKED; preparación técnica lista, ningún cierre de sesión ejecutado.

## 43. Decisión final: historial estricto y login manual preparado

Decisión explícita del usuario: preservar estrictamente el historial real.
Referencia privada existente conservada, sin recreación ni reemplazo; modo 0600,
propietario correcto y registro de `.history` presentes. El verificador está en
el repositorio persistente y usa stdlib más el snapshot privado existente; no
depende de helpers temporales para ejecutar el diagnóstico. La referencia de
/tmp se conserva para logout/login sin reboot; si falta al volver, BLOCKED,
sin inventar ni crear un nuevo baseline. Comando con ruta absoluta:

```bash
/usr/bin/python3 -B /media/okami/Mio/Siegfried/tools/verify_login_f55.py --post-login --reference /tmp/siegfried-f55-login-reference-final.json --seconds 20
```

Nueva ejecución de lectura con la referencia existente: PASS técnico previo.
Servicio active/enabled, PID 253749, NRestarts=0, proceso/cgroup único, PING/STATUS
PASS y socket seguro 0600; Autostart Hidden=false/Exec privado; runtime READY;
snapshots íntegros (61/57). Historial y demás archivos privados iguales a la
referencia (cero cambios). Journald de la invocación propia sin errores/warnings.
Ventana de comprobación 5,002 s: seis muestras, CPU media 0,199913 % de un núcleo,
RSS constante 29.851.648 bytes; no certificación sostenida. Desviación histórica
RSS y UNRESOLVED de historial siguen visibles, sin recuperación ejecutada.

No se modifican servicio, Autostart, REPL, lógica de historial, snapshots ni
producción. Sin instalación, commits, recuperación, logout ni reinicio.

Observación humana autorizada para el login: una sola notificación, saludo
correcto y presencia de Abrir sesión. No pulsar el botón ni abrir el launcher
persistente mientras su aislamiento no esté garantizado. Aprobación previa de
notificación→REPL real y ayuda/status/salir permanece PASS en el ensayo aislado;
la apertura dentro del nuevo login queda NOT_REVALIDATED. El procedimiento
anterior que pedía clic/REPL en el login queda sustituido por esta decisión.

La cadena completa durante ese mismo login era parte de la guía anterior:
queda pendiente si se exige ese alcance completo para el cierre, sin atribuirle
PASS por el ensayo previo. Posteriormente se preparará una prueba aislada si
resulta indispensable; no se ejecuta ni cambia producción en esta preparación.
La evaluación final debe distinguir este alcance omitido de un fallo funcional,
y justificar si implica BLOCKED o una desviación documentada. No se declara
PASS global antes de la evidencia de login y las observaciones humanas.

Al regreso se ejecutará el diagnóstico de lectura, se comparará el runtime con
la referencia anterior y se solicitará confirmación humana de notificación,
saludo/botón y cualquier desviación. Actualizar los tres artefactos evaluando
por separado daemon automático, briefing automático, REPL previamente aprobado,
integridad estricta, recursos/estabilidad y tiempos sólo con evidencia fiable.

**PREPARADO PARA LOGIN MANUAL.** Gate pendiente de comprobación posterior;
ningún cierre de sesión ejecutado por Codex.

## 44. Login real: confirmación humana y bloqueo de integridad

El usuario confirma PASS humano del Boot Briefing tras logout/login manual en KDE:
una notificación automática, apareció una vez, título «Siegfried — Inicio» y texto
«Buenas noches, Señor. Siegfried está preparado». Captura reportada a las 19:42;
el usuario confirma que el saludo correspondía a la hora. No pulsó «Abrir sesión»
para preservar estrictamente el historial. La cadena notificación→REPL sigue PASS
por el ensayo aislado previo; no se volvió a probar en este login.

Se intentó exclusivamente el validador de solo lectura con la referencia previa:

```text
/usr/bin/python3 -B tools/verify_login_f55.py --post-login \
  --reference /tmp/siegfried-f55-login-reference-final.json --seconds 20
Exit 2: {"technical_result":"BLOCKED","reason":"FileNotFoundError"}
```

La referencia `/tmp/siegfried-f55-login-reference-final.json` no existe tras volver
del login. No se recreó: una referencia capturada ahora no puede certificar cambios
ocurridos durante logout/login. El validador terminó antes de evaluar servicio,
instancias, IPC, permisos del socket, runtime, Autostart, snapshots, archivos
privados, recursos o journald. Esas verificaciones posteriores figuran como
NOT_VERIFIED, no como PASS. No se leyeron ni escribieron datos del runtime durante
este intento. El diagnóstico previo al logout sí pasó, pero no reemplaza evidencia
post-login.

Veredicto F5.5: **BLOCKED** por pérdida de la referencia indispensable. PASS humano
del briefing registrado; integridad post-login y estabilidad no certificadas.
No se certifica el SLO login→visible <2 s: la hora 19:42 en la captura no establece
el instante de login ni la latencia. La desviación histórica RSS (30.011.392 bytes,
11.392 por encima del objetivo 30.000.000) permanece; resolución CPU y duración
acotada tampoco certifican consumo sostenido. El incidente histórico de `.history`
permanece **UNRESOLVED**. El usuario no abrió REPL durante este login, por lo que
la decisión de preservar el historial se respetó; esta ejecución no leyó ni alteró
el historial. No se ejecutaron notificaciones, REPL, recuperación, cambios de
producción o commits.

## 45. Recuperación de evidencia post-login

Se buscaron las rutas registradas de referencia y preparación. Los dos JSON de
referencia bajo `/tmp`, el resultado precheck y manifiestos temporales ya no existen.
Los artefactos guardan el resultado agregado del prelogout (`changed_file_count=0`,
historial intacto), pero no el inventario privado por archivo. Estado del baseline
global: **PRE_LOGIN_BASELINE_UNAVAILABLE**. No se reconstruyeron fingerprints desde
el estado actual.

Sí existe evidencia de historial en `controlled_persistent_activation_actions`:
el hash y metadata se midieron antes del logout. Una lectura actual O_NOATIME confirma
que el contenido de `.history` coincide y también los campos históricos disponibles:
UID, GID, modo, inodo, tamaño y mtime. ctime, dispositivo, nlink y atime no estaban
en esa referencia. El dato privado no se imprimió ni se incorporó a este reporte.
Esto certifica `.history` contra aquel checkpoint anterior, no todos los archivos
privados a través del login. Vault, secretos y configuraciones pasan validación
actual READY, pero su preservación byte a byte a través de este login no puede
compararse sin el inventario desaparecido. No se lanzó REPL.

### Diagnóstico operativo actual

El verificador de solo lectura terminó su ventana de 20,009 s. Servicio active y
enabled, PID 2704, cero reinicios, Exec desde snapshot privado y único proceso en
el cgroup. PING/STATUS PASS; socket del usuario 0600; runtime READY; Autostart
Hidden=false y Exec privado. El daemon arrancó 237,715 ms después de la creación
de la sesión logind en el mismo boot. Esto mide creación de sesión → daemon, no
login → notificación visible. Marcador SENT coincide con la sesión; la confirmación
humana independiente acredita una notificación visible. No se certifica SLO visible
<2 s por falta de un origen temporal fiable para su latencia.

Journald del servicio en el boot actual: dos entradas, ambas prioridad 6 (información),
cero errores prioridad 0–3 y cero warnings prioridad 4. Se consultó también la
invocación activa; dos entradas, sin error/warning. Sólo se comunicaron conteos,
sin mensajes.

CPU media 0,199911 % de un núcleo y RSS constante 28.688.384 bytes en 21 muestras
durante 20,009 s. RSS cumple el límite de 30.000.000 en esta ventana; resolución
CPU 10 ms y ventana acotada no certifican consumo sostenido <0,2 %. La medición
anterior de 30.011.392 bytes excedió el objetivo por 11.392 y permanece como
desviación histórica.

Snapshots: daemon 61/61 fuentes PASS. Boot: los 57 hashes fuente coinciden, pero
el inventario estricto informa 39 discrepancias: 33 archivos `.pyc` adicionales
en seis directorios `__pycache__`, cuyos directorios tienen modo 0775. El precheck
antes del logout había encontrado el paquete Boot limpio; las caches aparecieron
tras ejecutar el hook en el login. No se alteraron ni limpiaron. Los directorios
padre del snapshot mantienen su modo privado; aun así el inventario/modo del paquete
Boot ya no coincide con el manifiesto, por lo que se registra como desviación de
integridad del snapshot. No se cambia producción bajo esta autorización.

### Referencia persistente y necesidad de otro login

Como protección para una futura prueba de preservación, se añadió al diagnóstico
la captura segura `--save-persistent-reference`. Se guardó la referencia actual en
`~/.local/share/siegfried-gate-f55/next-login-reference.json`, directorio 0700,
archivo 0600. Contiene nombres privados, metadatos y hashes, nunca contenidos ni
secretos en claro. Su procedencia está marcada por su fecha actual; no se hace pasar
por el baseline perdido. Puede usarse con `--reference` en una prueba futura.

No hace falta repetir login para demostrar inicio automático del daemon ni briefing:
el usuario confirmó el briefing y el journal/monotonicidad confirma el inicio del
daemon en esta sesión. Una prueba posterior sólo sería necesaria si se exige certificar
preservación completa de runtime a través de otro logout/login; usaría la referencia
persistente y acreditaría ese intervalo futuro, no recuperaría el intervalo ya pasado.

### Confirmación humana y veredicto

PASS humano registrado: notificación automática, única, título «Siegfried — Inicio»,
saludo «Buenas noches, Señor. Siegfried está preparado», consistente con las 19:42
que muestra la captura según confirma el usuario. No pulsó Abrir sesión para proteger
el historial. El flujo notificación→REPL se conserva como PASS del ensayo aislado
anterior, no como validación de este login.

Estado final del Gate: **BLOCKED**. Autoinicio daemon, briefing, IPC, runtime READY,
fuentes snapshot y `.history` contra el checkpoint disponible pasan. Quedan la
falta de inventario global prelogin auténtico y los bytecodes/modos adicionales del
snapshot Boot. Vault, secretos y configuración no se declaran preservados a través
del login sin comparación histórica. La desviación RSS y el incidente histórico del
historial permanecen expresamente documentados; el incidente sigue **UNRESOLVED**.
No hubo REPL, notificación nueva, escritura de runtime, recuperación, logout, reboot,
instalación, commit ni push.

## 46. Cachés Boot: remediación preparada, no aplicada

Causa demostrada en HOME temporal: desktop sin -B; workers con -S (no desactiva
bytecode), sin -B y con env explícito que descarta PYTHONDONTWRITEBYTECODE; flags
del padre no se heredan. Launcher REPL inicia otro Python sin -B. Entorno actual
del manager/shell sin PYTHONDONTWRITEBYTECODE/PYTHONPATH; daemon protegido por
PYTHONDONTWRITEBYTECODE=1. Hook actual ausente. No se conserva su entorno/PID de
escritura histórico: la ruta capaz de recreación queda probada, no cada actor exacto.

Inventario preciso en [cache-inventory.json](f55_cache_remediation/cache-inventory.json):
57 fuentes y marcador coincidentes, 33 bytecodes 0600 del mismo UID/GID y nlink=1,
seis directorios __pycache__ 0775; cero archivos desconocidos, symlinks, hardlinks,
propietarios inesperados o permiso incorrecto en fuentes. Cada pyc coincide con
magic CPython, timestamp/tamaño de fuente y body recompilado desde la fuente
original y path efectivo, sin ejecutarlo. Raíz y padres privados 0700 conservados;
los 0775 internos no permiten acceso desde otras cuentas a través de esos padres,
pero incumplen el inventario/permisos requeridos. Limpieza sola volvería a crear
caches en una ejecución futura de la ruta sin protección.

Propuesta mínima [prevent-bytecode.patch](f55_cache_remediation/prevent-bytecode.patch):
-B en generación del desktop, en argv de workers (conservar -S) y en argv Python
del launcher REPL. Tres líneas de producción propuestas; ninguna aplicada al
repositorio productivo. Despliegue Boot propuesto: sólo dos módulos de integraciones
y el desktop efectivo. No modificar compositor, lógica determinista/inferencia,
bin/siegfried o snapshot daemon. -B no aísla historial ni autoriza abrir el REPL.
Desktop original SHA y UID/GID/0600/nlink/contenido exacto verificados contra el
manifiesto de activación; candidato conserva Hidden=false y resto de campos.

Doce comprobaciones HOME temporal PASS: recreación por parent/worker original,
prevención por tres intérpretes independientes, instalación/desktop-file-validate
KDE temporal, launcher interceptado sin Konsole/REPL, limpieza exacta de copia33/6
con backup y fuentes/archivo ajeno preservados. Rechazos probados: cambio de cache,
symlink, hardlink, archivo desconocido, directorio reemplazado y hook activo.
No cambios de producción: no se repitió regresión completa ni login. Si se autoriza
el patch real, ejecutar regresión completa aislada antes de desplegar.

Procedimiento completo y recuperación en [PROPOSAL.md](f55_cache_remediation/PROPOSAL.md).
[check_boot_cache_f55.py](../../tools/check_boot_cache_f55.py) tiene dry-run por defecto;
precheck real PASS/applied=false, mismo inventario 33/6 y mismas identidades/hashes.
--apply requiere autorización específica y backup exclusivo privado previo. Valida
árbol entero, origen/candidatos aprobados, root, archivos/tipos/UID/GID/modos,
inodos/nlink/timestamps/hash, ausencia de symlinks/mounts/entradas desconocidas/hook.
Revalida tras backup y antes de cada unlink; rmdir sólo sobre seis dirs vacíos.
No toca fuentes, datos ni archivos externos. No promete atomicidad global ni
exclusión transaccional frente a escritor malicioso del mismo UID. Pausar ante fallo,
conservar evidencia y backup; no continuar con borrados generales. Restauraciones
sólo sobre destinos propios aún idénticos al candidato de esta operación. Caches
son derivadas; copiarlas desde backup no recupera inodo/ctime originales.

Estado real antes de entrega: active/enabled/PID2704/NRestarts0, Autostart
Hidden=false. Runtime comparado en lectura O_NOATIME con referencia persistente
reciente: cero archivos cambiados, incluido historial. Ninguna comparación reciente
se etiqueta retroactivamente como baseline completo del primer login.

PASS humano de login/Boot y PASS técnico daemon conservados; historial actual
preservado, incidente histórico UNRESOLVED; PRE_LOGIN_BASELINE_UNAVAILABLE para
runtime completo en el primer login; exceso RSS histórico 11.392 bytes conservado;
SLO login→visible <2 s no certificado. Gate BLOCKED, remediación pendiente de
aprobación específica. Sin limpieza real, cambio instalado, notificación, REPL,
logout/reboot, restart/disable, commit/push o recuperación ejecutados.


## 47. Intento autorizado de prevención de bytecode: bloqueado antes del despliegue

Se repitió el preflight contra la propuesta y el inventario: 57 fuentes originales, 33 `.pyc` y seis directorios mantuvieron hashes/identidades auditados; no hubo symlinks, hardlinks inesperados, rutas desconocidas ni propietarios ajenos. La entrada XDG efectiva coincidió con el archivo propio instalado y `Hidden=false`; la referencia persistente privada estaba accesible. No había Boot Hook en ejecución. Siegfried es una unidad **systemd de usuario** (la consulta inicial al gestor de sistema devolvió `not-found`, que quedó explicado al consultar el gestor correcto): active/enabled, PID 2704, `NRestarts=0`; PING/STATUS PASS. Runtime READY. El snapshot daemon tuvo cero discrepancias; el snapshot Boot conserva las 39 discrepancias inventariadas.

Se aplicó al worktree únicamente el patch de tres líneas autorizado: `-B` en el Exec generado, el worker (conservando `-S`) y el intérprete del launcher REPL. La suite aislada completa ejecutó 689 pruebas y terminó **FAIL: 688 PASS, 1 FAIL**. La única falla es `integration.test_boot_briefing_f53.TestDKDE.test_launcher_fixed_argv`, cuya expectativa comprueba el sufijo antiguo `[sys.executable, bin/siegfried]`; el argv preventivo ahora lleva `-B` entre ambos. No se modificó esa prueba porque queda fuera del patch exacto autorizado. Según la condición del Gate, no se publicó ningún candidato.

Los benchmarks aislados PASS: `tools/benchmark.py` cumplió sus SLO; `tools/benchmark_boot_briefing.py` registró dry-run cold-start P95 115.553 ms. No midió la visibilidad de una notificación KDE (`null`). `git diff --check` PASS. Las 12 pruebas temporales de la propuesta y `desktop-file-validate` PASS.

No se crearon respaldos operativos, no se actualizó el snapshot Boot ni el desktop instalado, no se eliminaron caches, no se ejecutó hook/REPL ni se alteraron servicio, Autostart, runtime, historial, Vault, secretos o configuración. Los tres `-B` permanecen sólo como cambios exactos del worktree para revisión; el snapshot de producción permanece igual. Por el fallo de regresión, el despliegue y la limpieza están **retenidos** hasta que una ejecución posterior autorizada pase la suite completa.

Se mantienen: PASS humano del Boot Briefing tras login y PASS operativo del daemon; `PRE_LOGIN_BASELINE_UNAVAILABLE` para la comparación histórica completa; incidente histórico del historial `UNRESOLVED`; desviación histórica RSS de 11.392 bytes; SLO login→visible <2 s no certificado. Ninguna integridad global retroactiva se infiere.


## 48. Aserción corregida y remediación aplicada

La única falla de la ejecución anterior era `TestDKDE.test_launcher_fixed_argv`: comprobaba el sufijo del contrato anterior y asumía que el intérprete aparecía inmediatamente antes del script. Se actualizó exclusivamente esa aserción en `tests/integration/test_boot_briefing_f53.py`. Ahora exige el argv completo y ordenado `[konsole, --separate, -e, sys.executable, -B, repository/bin/siegfried]` y conserva la prohibición de `shell`; no se quitó ni debilitó cobertura.

La prueba focalizada pasó (1/1). Pasaron las suites aisladas relacionadas de Boot/launcher (90), Autostart (11) y despliegue privado (11). La regresión completa aislada pasó **689/689** en 41.931 s. `tools/benchmark.py` pasó todos los SLO medidos: router P95 0.0015 ms, escritura Vault P95 2.4068 ms, CLI cold-start P95 29.55 ms, routing P95 0.0047 ms, agregador heurístico P95 1.9572 ms y exhaustivo P95 8.0968 ms. `tools/benchmark_boot_briefing.py` pasó; dry-run de hook cold-start P95 115.521 ms. No mide entrega ni visibilidad KDE (`notification_visible_ms=null`). `git diff --check` PASS.

Preflight inmediatamente previo al host: inventario íntegro de 57 fuentes más marcador, 33 bytecodes y seis directorios; sin elementos extra, symlinks, hardlinks inesperados ni propietarios ajenos. El servicio **systemd de usuario** siguió active/enabled con PID 2704 y `NRestarts=0`; PING/STATUS PASS. No había Boot Hook activo. El desktop instalado conservaba identidad/hash auditados, `Hidden=false`, y la referencia privada estaba accesible con permisos 0600/0700.

Antes de publicar se guardaron y verificaron en `~/.local/share/siegfried-gate-f55/` (directorio 0700) respaldos recuperables con archivos 0600: dos módulos Boot y desktop (`predeployment-modules-f55.json`, SHA-256 `4cec13bc…b4b6935`); los 33 `.pyc` exactos (`predeployment-caches-f55.json`, `a8f31158…f9a9f5d6fa`). El helper creó y verificó además su respaldo previo a la eliminación (`cache-backup-f55.json`, `5794913c…f498f6810`). Los SHA completos constan en `GATE_F5_5_EVIDENCE.json` y `GATE_F5_5_RECOVERY.json`.

Se publicaron atómicamente sólo `~/.local/share/siegfried-boot/src/siegfried/integrations/boot_briefing.py` (SHA-256 anterior `2ff6c8fb…f81556e8`, nuevo `bafa71dd…ee81ff4`), `briefing_kde.py` (anterior `29151f26…316fb0b5`, nuevo `10d69267…6478a6`) y `~/.config/autostart/org.siegfried.BootBriefing.desktop` (anterior `13a581d8…c438360d`, nuevo `981f7cf6…bbb62d9e`). Hashes completos en los dos JSON. El desktop sigue `Hidden=false`; `Exec` usa `/usr/bin/python3 -B`. Los tres caminos Python quedan protegidos por `-B` (worker conserva `-S`).

Después de publicar, el helper revalidó identidades y retiró exactamente las 33 rutas `.pyc` y los seis directorios enumerados en [cache-inventory.json](f55_cache_remediation/cache-inventory.json); los directorios se quitaron sólo vacíos, sin borrado recursivo. Inventario final exacto: 58 archivos permitidos, 13 directorios de fuentes, cero bytecodes/cache dirs adicionales; los dos módulos usan los nuevos SHA autorizados. `desktop-file-validate` PASS. Snapshot daemon intacto. Servicio continúa con PID 2704, active/enabled y sin reinicio; PING/STATUS, socket 0600 y Runtime READY PASS. La comparación privada persistente informa cero archivos cambiados e historial preservado; Vault, secretos y configuración del alcance comparado pasan. Journal de la invocación actual: cero errores prioridad 0–3, sin revelar mensajes. No se inició hook, REPL ni notificación.

Muestra posterior de 5 s: CPU 0.20 % (sin desviación en esa muestra), RSS 29.945.856 bytes (bajo límite actual de 30.000.000). Se conserva la desviación histórica RSS de 30.011.392 bytes, excedente 11.392, y la muestra anterior de CPU 0.40 % frente al objetivo 0.20 %. Se mantienen `PRE_LOGIN_BASELINE_UNAVAILABLE` para el baseline global histórico, el incidente histórico de `.history` `UNRESOLVED` y el SLO login→visible <2 s no certificado. El PASS humano del login/briefing permanece; no se repitió login. Veredicto F5.5: **PASS_WITH_DEVIATIONS**; las limitaciones históricas no se presentan como resueltas.
