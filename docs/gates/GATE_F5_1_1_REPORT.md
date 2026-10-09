# SIEGFRIED — GATE F5.1.1

Fecha de auditoría: 2026-10-09 UTC (2026-10-08 en America/Lima durante la ejecución).
Repositorio: `/media/okami/Mio/Siegfried`.

**VEREDICTO: PASS_WITH_DEVIATIONS. F5.2 permanece sin iniciar.**

Este informe sustituye las conclusiones excesivas del reporte F5.1 sobre autenticación, dependencias, suspensión y garantías del benchmark. Se conserva el informe anterior como registro histórico. No se modificó `SPECIFICATION.md`, no se instalaron paquetes ni se realizaron commits. Los scripts y ventanas del ensayo fueron temporales; no se instaló un KPackage ni se cambió configuración de KWin, systemd o del usuario.

## 1. Causas y correcciones mínimas

| Hallazgo | Corrección / límite |
|---|---|
| `dbus`, `dbus.service`, `dbus.mainloop.glib`, `gi.repository` se usaban en una funcionalidad anunciada como exclusivamente stdlib | Importación diferida dentro de `FocusDBusAdapter.start()`, desactivado por defecto. Habilitación explícita: `SIEGFRIED_ENABLE_KWIN=1`. Sin bindings, bus o KWin devuelve `False`; núcleo operativo. Excepción nativa propuesta, no aprobada unilateralmente. |
| Cualquier proceso del mismo bus podía invocar `WindowChanged` | `sender_keyword` obtiene el nombre único del emisor. Sólo se acepta el propietario fijado de `org.kde.KWin`, con UID local y PID positivo obtenidos del bus. Se vigila `NameOwnerChanged` del propio bus y se cierra la carrera entre consulta y suscripción. Cambio de propietario desactiva el adaptador y descarta el intervalo incierto. |
| El truncado a 128 caracteres no era validación de tamaño en bytes | Firma real `s`, tipo string y máximo 64 caracteres ASCII válidos; exceso o contenido sospechoso rechazados, sin truncar ni persistir. No se acepta transporte TCP ni listas de direcciones alternativas: sólo una dirección `unix:`. |
| JavaScript eliminaba caracteres y recortaba cadenas potencialmente privadas, convirtiéndolas en IDs aparentemente válidos | Rechazo de cadenas fuera de la whitelist y de longitud excesiva. Ambas copias del watcher son idénticas. No se accede a caption/título/URL/rutas. Se emite también el estado nulo inicial. |
| No había backpressure explícito; el callback D-Bus hacía append/fsync | Cola máxima de 64 transiciones; 20/s con capacidad inicial de 40. Duplicados consecutivos no ocupan entradas. Un trabajador persiste con tiempos de llegada, fuera del callback D-Bus. Pérdida por saturación descarta la cola y reinicia el intervalo incierto, priorizando pérdida de datos frente a duraciones falsas. |
| Parada incompleta del nombre/conexión/hilo; cierre del tracker anterior a posibles callbacks pendientes | Conexión privada, `do_not_queue=True`, eliminación de objeto/match, liberación de nombre, cierre de conexión y espera acotada de hilos. Primero se drena la cola y luego se cierra el intervalo. El daemon incorpora el resultado de limpieza de foco en su resultado de parada. Un hilo superviviente se informa como fallo de limpieza. |
| El E2E de recuperación detectó que una notificación tardía de desconexión del bus antiguo podía detener el nuevo | Callback condicionado a la identidad de la conexión actual; la conexión se separa del estado del adaptador antes del cierre intencional. Regresión automatizada y reinicio real PASS. |
| `on_suspend()` y `on_resume()` se presentaban como integración del sistema comprobada | Se mantiene la lógica funcional y su simulación. No existe suscripción automática a logind/bloqueo: se documenta pendiente. No se suspendió el equipo ni se bloquearon/desconectaron KWin o el bus compartido. |
| Benchmark describía ~2 años y latencia de producción no medida; atribuía exactitud universal | 20,000 eventos a 120 s representan ~27.8 días. Se eliminan garantías y extrapolaciones. P95 observado y pruebas de corrección se documentan por separado. |

El tracker original y su `RLock` se conservan; no se reconstruyó F5.1. El núcleo no adquiere dependencia PyPI.

## 2. Dependencias efectivas y propuesta arquitectónica

El único uso de esos módulos externos en `src`, `tests` y `scripts` está en las cuatro importaciones diferidas de `src/siegfried/daemon/focus.py`. `dbus.service` exporta objeto/método y reserva el nombre; `dbus.mainloop.glib.DBusGMainLoop` integra la conexión con GLib; `gi.repository.GLib` proporciona el loop nativo. **Ninguno es Python Standard Library.**

Paquetes ya presentes, leídos con `dpkg-query`, sin instalación:

| Paquete | Versión observada | Uso |
|---|---|---|
| `python3-dbus` | 1.4.0-1build2 | Bindings D-Bus y servicio Python |
| `python3-gi` | 3.56.2-1 | Introspección GObject / importación GLib |
| `libglib2.0-0t64` | 2.88.0-1ubuntu0.1 | Loop nativo GLib |

Rutas observadas: `/usr/lib/python3/dist-packages/dbus/__init__.py` y `/usr/lib/python3/dist-packages/gi/__init__.py`. Otros sistemas pueden requerir el paquete GIR de GLib que corresponda a su distribución. Python observado: 3.14.4. Esto no constituye certificación en todas las versiones Python admitidas por el proyecto.

**Propuesta pendiente de aprobación:** admitir esos bindings de distribución exclusivamente para el adaptador KDE optativo, manteniendo el núcleo stdlib y sin dependencias PyPI. Es el cambio menos complejo respecto de la integración existente: conserva el servicio, el contrato y el loop probado. Implementar D-Bus binario a mano con sockets añadiría complejidad de autenticación, marshalling y recursos; `qdbus6`/`busctl` como subprocesos no sustituyen por sí solos un receptor exportado y autenticado. No se propone modificar la restricción aprobada del núcleo.

Evidencia independiente: importación con `python3 -S` del módulo y ejecución del adaptador habilitado sin bindings produce `False`, con `dbus` y `gi` ausentes de `sys.modules`. La suite completa final también se ejecuta con `-S`, sin cargar site-packages. Pruebas específicas verifican que el adaptador desactivado ni siquiera intenta importarlos y que su ausencia al habilitarlo degrada limpiamente.

## 3. E2E real KWin/Wayland

Herramienta reproducible: `tools/verify_kwin_f511.py`. Se ejecutó con acceso a la sesión autorizado después de que el sandbox devolviera `Operation not permitted`. Usa Vault temporal, nombres únicos de scripts, dos procesos de prueba y bloques `finally` para descarga/cierre. La comprobación inicial del bus identificó `kwin_wayland`, PID 2660, UID 1000 y propietario `:1.16`. La sesión real es Wayland. No se reinició el compositor ni el bus.

| Requisito | Evidencia | Resultado / alcance |
|---|---|---|
| Transición entre dos aplicaciones | Ventanas nuevas de kdialog y zenity; activación por PID desde un script KWin temporal | PASS: kdialog → zenity → kdialog por la señal real `windowActivated` |
| Duración de ambas | Vault: 1.31 s y 0.81 s; referencia monotónica del controlador: 1.291573 s y 0.958120 s | PASS dentro de tolerancia 0.4 s. La referencia incluye latencia de control/sleep, no mide exactamente el instante compositor. Precisión determinista adicional en pruebas simuladas. |
| Duplicados | Ocho llamadas idénticas inducidas desde el proceso KWin; no se añaden intervalos | PASS transporte real y deduplicación. No demuestra que KWin produzca espontáneamente esa ráfaga nativa. |
| Foco nulo | `workspace.activeWindow = null` en script temporal; tracker queda sin aplicación | PASS foco nulo real inducido. No equivale a un ensayo de bloqueo de pantalla. |
| Recuperación | Se cierra sólo la conexión privada del adaptador, se cambia foco durante la ausencia, se reinicia y se recarga el watcher | PASS recuperación explícita; no reconexión automática. La recarga vuelve a muestrear la ventana aunque no haya otra transición. |
| Desactivación / descarga | Descarga con `unloadScript`, comprobación `isScriptLoaded=False`, desactivación de tracker y parada de adaptador | PASS: cambio posterior no llega desde watcher descargado; nombre D-Bus libre e hilos terminados. Descargar sólo el watcher no cierra por sí mismo el intervalo del daemon. |
| Privacidad | Todos los eventos tienen exclusivamente `aplicacion`, `categoria`, `duracion`; no contienen títulos de prueba, texto de diálogo, ruta del Vault temporal ni URL de prueba | PASS para este ensayo y contrato del origen. Los IDs aportados por clientes no son una prueba universal de ausencia de secretos. |
| Suspensión/reanudación | No se provocó suspensión real. Simulación de 120 s de actividad, 4 h suspendido y 30 s de nueva actividad | PASS simulado; validación real y conexión automática de señales pendientes |

Se rechazaron llamadas directas desde una conexión diferente del mismo UID, un string de 4,096 caracteres y una firma de entero. Se restauró el foco mediante el ID opaco de la ventana original cuando seguía presente; el controlador registra el intento, no una medición independiente de la restauración. Todos los procesos creados terminaron. La herramienta no lee captions ni contenidos de las ventanas originales.

Repetición final sobre el código definitivo: exit 0; duraciones **1.33 s / 0.81 s**, referencias **1.332346 s / 0.957558 s**, dentro de la misma tolerancia de 0.4 s. Todos los restantes checks E2E volvieron a pasar y `test_process_cleanup=true`.

Los intentos iniciales fallidos también ejecutaron limpieza. Permitieron corregir la consulta de PID mediante `GetConnectionUnixProcessID`, seleccionar explícitamente la firma sobrecargada `loadScript(ss)` y descubrir la notificación tardía de desconexión. La lectura de `/proc/2660/exe` fue denegada por el host: la implementación final no depende de esa lectura ni afirma validar la procedencia del ejecutable.

## 4. Auditoría de seguridad D-Bus

La sesión D-Bus **no autentica por sí sola a KWin**. El nombre único proviene del bus y se compara con el propietario fijado, sin confiar en un campo del payload. `sender_keyword` es el mecanismo documentado para obtener el emisor del método. [Documentación dbus-python](https://dbus.freedesktop.org/doc/dbus-python/tutorial.html).

La identidad fijada es la conexión propietaria de `org.kde.KWin`, no una certificación criptográfica del binario. El UID/PID proceden del bus. El ensayo inspeccionó además el proceso real con `busctl`. No se supone aislamiento contra otro proceso del mismo usuario que pueda cargar un script en KWin, controlar su proceso o reservar `org.kde.KWin` cuando esté ausente. Este último riesgo no queda resuelto por comprobar el UID. No se cambió la política compartida del bus ni se ampliaron permisos.

| Superficie | Control / evidencia | Riesgo residual |
|---|---|---|
| Emisor falso | Nombre único fijado, credenciales y vigilancia de propietario; llamada de otra conexión real devuelve `False` | Autoridad de scripting y suplantación del nombre cuando KWin está ausente dentro del mismo UID |
| Mensaje malicioso | Firma y whitelist; máximo 64 bytes ASCII; rechazo antes de encolar; errores de persistencia sin texto de excepción/payload en logs | El binding recibe/deserializa antes de la validación Python |
| Saturación | Cola 64, token bucket 20/s y ráfaga 40; duplicados suprimidos; overflow descarta intervalos inciertos | No se certifica resistencia a inundación sostenida del bus ni a OOM en recepción nativa. Puede perderse telemetría bajo carga. |
| Almacenamiento lento | Trabajador separado del callback GLib; timestamps de llegada, no de escritura | Snapshot/operaciones del tracker pueden esperar su lock durante append; no se demostró latencia universal del daemon bajo fsync bloqueado |
| Desconexión / pérdida de propietario | Adaptador se desactiva y descarta intervalos; prueba de propietario simulado y reinicio de conexión real | Reinicio/recarga manual. No se cerró el bus de sesión ni se mató KWin. |
| Recursos | Conexión privada, objeto/match/nombre eliminados y join acotado; fallo si sobreviven hilos | Python no cancela forzosamente un fsync bloqueado; no se oculta un hilo residual como limpieza exitosa |
| Privacidad | Watcher consulta sólo `desktopFileName`/`resourceClass`; validación y Vault aislado inspeccionados | Un cliente puede elegir un app ID con texto sensible compatible con whitelist; el mismo UID no es frontera de confidencialidad |

El límite de aplicación de 64 bytes no es un límite de memoria previo a la recepción ni del mensaje D-Bus completo. La especificación del protocolo permite mensajes mucho mayores, hasta 128 MiB; no se alteraron cuotas locales del broker ni se realizó una prueba destructiva de inundación. [Especificación D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html).

El watcher usa propiedades y señales documentadas de KWin; el control temporal del E2E usa la propiedad escribible `activeWindow`. Eso no implica que cualquier script cargado en el compositor sea un emisor confiable del watcher concreto. [API de scripting KWin](https://develop.kde.org/docs/plasma/kwin/api/).

## 5. Exactitud histórica y benchmark

Se confirmó que IPC `QUERY` para hoy y semana llama `aggregate_today(allow_early_exit=False)` y `aggregate_week(allow_early_exit=False)`. Una regresión comprueba ambas ramas. Las pruebas heredadas incluyen desorden superior a la tolerancia y desfase mayor que la ventana heurística. La API general del agregador conserva `allow_early_exit=True` por defecto: un consumidor distinto debe solicitar explícitamente el modo exhaustivo para hablar de resultados exactos.

Exhaustivo evita el corte heurístico por desorden; **no garantiza integridad del log, ausencia de errores de esquema, exactitud de timestamps, precisión del LLM o conservación de eventos corruptos**. La rapidez medida no demuestra exactitud. No se alteraron fórmulas ni semántica funcional del agregador.

Comando: `python3 tools/benchmark.py`, exit 0. Disco verificado con `findmnt`: ext4. Valores observados:

| Medición | Muestras | P50 ms | P95 ms | Max ms | Umbral / resultado |
|---|---:|---:|---:|---:|---|
| Router | 2,500 | 0.0010 | 0.0016 | 0.0190 | <10 ms, PASS |
| Vault append / fsync | 200 | 1.1104 | 1.8718 | 3.2735 | <10 ms, PASS |
| CLI cold-start / help | 50 | 27.35 | 33.64 | 40.63 | <50 ms, PASS |
| Orquestador en memoria con stubs | 1,000 | 0.0084 | 0.0094 | 0.0524 | <1 ms, PASS |
| Histórico heurístico | 100 | 1.9245 | 1.9817 | 2.3220 | <10 ms, PASS |
| Histórico exhaustivo | 25 | 8.1450 | **8.2012** | 8.2979 | Observación, sin SLO propio |

P95 exhaustivo previo: 8.1923 ms; nueva observación: 8.2012 ms (diferencia +0.0089 ms). Dataset sintético ordenado de 20,000 eventos separados 120 s, consulta de últimas 24 h (720 eventos), lectura repetida con caché caliente en directorio temporal; se incluye `format_prompt_block`. Primero se ejecuta el modo heurístico. La implementación del percentil elige `int(0.95*(n-1))` de las muestras ordenadas: índice 22 para n=25. No hay intervalo de confianza, prueba de caché fría, escala arbitraria, todos los sistemas de archivos ni medición del workload de producción. No se atribuye el anterior «<0.8 ms en producción» ni «100% exactitud garantizada» al benchmark.

## 6. Pruebas y certificación

- Suite original F5.1: 26 pruebas PASS en 0.119 s, repetida fuera del sandbox porque éste bloqueaba los sockets Unix. Los seis errores originales del ensayo restringido fueron `PermissionError` de bind, no regresiones funcionales.
- Regresión F5.1.1: 16 pruebas nuevas, incluyendo identidad, tipo/tamaño/privacidad, cola/tasa, timestamps, duplicados, fallo de persistencia, pérdida de propietario/conexión, evento tardío tras reinicio, limpieza parcial/idempotencia, hilo residual, ausencia de imports nativos, degradación sin bindings, modo IPC exhaustivo y rechazo de transporte remoto.
- Primera suite completa ampliada: 479 pruebas PASS en 35.285 s (antes de añadir la prueba final de transporte remoto).
- Primera suite sin site-packages: 479 pruebas PASS en 36.295 s.
- Certificación final: `PYTHONPATH=src python3 -S -m unittest discover -s tests -p 'test_*.py' -q`: **480 pruebas PASS**, 0 fallos, 0 errores, 0 omitidas. Tiempo final: **30.349 s**.
- Cinco SLOs PASS en ejecución del harness; modo exhaustivo medido aparte, sin umbral de certificación propio.
- `git diff --check`: PASS. También se inspeccionaron archivos sin seguimiento; el comando Git no comprueba por sí solo su whitespace. Se comprobó whitespace de los archivos nuevos editados, identidad de las dos copias JS con `cmp` y sintaxis con `node --check`.

La suite emite logs de fallos simulados y avisos `ResourceWarning` de HTTP/socket, además de un `BrokenPipeError` en un servidor HTTP de prueba. El resultado unittest es OK; no se presenta como ausencia de advertencias ni como prueba de limpieza de todos los componentes heredados. La limpieza del adaptador se comprueba específicamente por separado.

## 7. Archivos y estado Git

Archivos intervenidos en esta auditoría:

1. `src/siegfried/daemon/focus.py`: endurecimiento y opcionalidad del adaptador; tracker funcional conservado.
2. `src/siegfried/daemon/app.py`: orden de cierre y propagación del resultado de limpieza de foco.
3. `scripts/kwin_focus_watcher.js` y `scripts/kwin_focus_watcher/contents/code/main.js`: rechazo de IDs sospechosos, foco nulo inicial y devolución de aceptación.
4. `tests/integration/test_kwin_focus_f511.py`: regresiones nuevas.
5. `tools/verify_kwin_f511.py`: E2E explícito temporal y reversible.
6. `tools/benchmark.py`: eliminación de garantías/extrapolaciones no medidas.
7. `README.md`, `PLAN.md` y este reporte: cierre, evidencia, dependencias y pendientes.

El repositorio estaba sucio al comenzar. Se preservaron cambios previos de CLI/REPL/router, exports de daemon/storage, agregador, pruebas F4.1/F4.2/F5.1 y reportes anteriores. No se revirtieron, añadieron al índice ni comprometieron. La revisión de archivos sin seguimiento identificó fuentes, tests, metadatos KPackage y reportes previos; no se encontraron paquetes descargados o artefactos de instalación en esa lista.

Estado final comprobado tras generar este reporte:

```text
 M PLAN.md
 M README.md
 M scripts/kwin_focus_watcher.js
 M src/siegfried/cli/app.py
 M src/siegfried/cli/repl.py
 M src/siegfried/cli/router.py
 M src/siegfried/daemon/__init__.py
 M src/siegfried/daemon/app.py
 M src/siegfried/storage/__init__.py
 M tools/benchmark.py
?? docs/gates/GATE_F4_1_REPORT.md
?? docs/gates/GATE_F4_2_HARDENING_REPORT.md
?? docs/gates/GATE_F4_2_REPORT.md
?? docs/gates/GATE_F5_1_REPORT.md
?? docs/gates/GATE_F5_1_1_REPORT.md
?? scripts/kwin_focus_watcher/
?? src/siegfried/daemon/focus.py
?? src/siegfried/storage/aggregator.py
?? tests/integration/test_historical_inference_f42.py
?? tests/integration/test_kwin_focus_f51.py
?? tests/integration/test_kwin_focus_f511.py
?? tests/integration/test_repl_interactive_f41.py
?? tools/verify_kwin_f511.py
```

## 8. Desviaciones y condición de avance

1. Excepción de bindings nativos **propuesta, pendiente de aprobación**; el adaptador permanece desactivado por defecto y no cambia el contrato stdlib del núcleo.
2. Suspensión/reanudación real **pendiente**; integración automática logind/bloqueo también pendiente. Los hooks manuales/simulados no permiten prometer ausencia de intervalos falsos en ese escenario real.
3. Recuperación explícita mediante reinicio y recarga; no se certifica autoconexión ni recuperación tras una caída real del bus compartido/KWin.
4. Confianza del mismo UID limitada: autenticación de conexión propietaria, no del watcher concreto o del ejecutable. Saturación nativa, IDs sensibles elegidos por clientes y almacenamiento bloqueado conservan los límites descritos.

**PASS_WITH_DEVIATIONS** certifica las correcciones mínimas, pruebas descritas y evidencia real obtenida. No libera F5.2 ni convierte pendientes en PASS. Cualquier aprobación posterior debe referirse a esta propuesta concreta y a estos límites.
