# SIEGFRIED v1.0 — GATE F5.5

Fecha: 2026-10-09. Actualización: corrección de observabilidad probada sólo en el repositorio; 676 PASS. Verificación UTC: 2026-10-09T20:43:16.989762+00:00.

**Veredicto: BLOCKED.** Corrección de logging PASS, no desplegada. Piloto manual funcional con desviación de observabilidad; servicio real, clic físico y login pendientes. La primera regresión alteró el historial real por dos fixtures heredadas sin rutas inyectadas; incidente comunicado y no restaurado. No se certifican preservación global de datos ni uso diario.

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

- Logging corregido desplegado únicamente al snapshot daemon y verificado sin arranque (sección 25). Su emisión real mediante systemd sigue pendiente. Snapshot Briefing conservado con fuentes anteriores; no se amplió el alcance.
- Incidente de history: UNRESOLVED tras auditoría de sección 22; reemplazo demostrado y contenido anterior no disponible. Ninguna restauración autorizada. Regresiones posteriores protegidas por sección 23.
- Verify exit 0 con aviso externo de spice-vdagent; unidad ajena preservada. Hubo rollback limitado del primer intento por una comprobación de stderr demasiado estricta, seguido de instalación satisfactoria.
- La inicialización preservó el historial entonces existente; la primera regresión posterior lo reemplazó. No se declara intacto su contenido anterior. Configuración, secretos y Vault se conservan según las comprobaciones registradas.
- RSS después de la ruta cognitiva supera 30 MB decimales en la observación, sin convertirlo en PASS por usar MiB.
- Arranque real de unidad/hardening, adaptadores optativos, popup/clic y login no demostrados.
- Mismo UID y fuente compartida forman parte del límite de confianza local; hashes no son firma independiente.
- No hay estimación de descanso entre reinicios sin evidencia contractual suficiente; se conserva F5.2 y se omite esa duración.
- No se demuestra exactly-once global ante pérdida del marcador o crash entre Notify y marcado.
- Python 3.10 sólo comprobado sintácticamente, ejecución real en 3.14.4.

## 19. Estado final Git

HEAD 0c8a088fcefafaaee8dbd10fd405f4062c347333, rama main; no commits/push. Dos fuentes de producción modificadas (observability/logging.py y daemon/app.py), dos fixtures heredadas corregidas, tres archivos nuevos de pruebas, dos herramientas nuevas de aislamiento, README y tres artefactos de reporte/evidencia/recuperación actualizados. Staging vacío. Contratos congelados sin cambios. git diff --check y JSON de ambos manifiestos PASS.

Snapshot daemon actualizado con las dos fuentes corregidas; snapshot Briefing preservado. Inventarios anterior y actual registrados por separado. Log real vacío e intacto. Ningún arranque adicional del daemon real, REPL, KWin, start/enable de systemd o Autostart en esta corrección.

## 20. Veredicto y siguiente autorización

**BLOCKED.** Corrección de observabilidad PASS y desplegada al daemon (sección 25); piloto manual medido conservado. Incidente del historial UNRESOLVED (sección 22), con decisión explícita de conservar el actual. Actualización limitada completada; pendiente autorización de ensayo systemd y validaciones operativas. Regresión aislada 678 PASS (sección 23). Unidad loaded/inactive/MainPID=0/disabled, Autostart ausente; faltan arranque con hardening systemd, visual/clic y login.

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

## 26. Siguiente autorización propuesta: ensayo supervisado con systemd

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
