# Plan Maestro de Implementación — Siegfried MVP (Fases 0 a 5)

---

## Fase 0: Contratos, Esquemas, Protocolos de Comunicación y Observabilidad

### 0.1 Objetivo y Alcance Técnico
Definir y congelar los contratos formales de datos, comunicación entre procesos y observabilidad antes de escribir código funcional de almacenamiento, CLI o daemon. Garantizar que todos los componentes compartan especificaciones rigurosas de eventos, IPC y métricas, estableciendo la suite base de benchmarks y telemetría.

### 0.2 Tareas de Ingeniería
1. **Especificación de `Event Schema v1` (`vault_schema_v1.json`):**
   * Estructura universal JSON para `siegfried_vault.jsonl`: campos obligatorios `v` (entero), `ts` (float epoch en segundos), `type` (string enum) y `data` (objeto).
   * Definición del catálogo formal de eventos: `pomodoro_started`, `pomodoro_completed`, `break_started`, `break_interrupted`, `posture_limit_reached`, `postpone_granted`, `postpone_rejected`, `sleep_initiated`, `wake_detected`, `window_focus_sampled`.
2. **Especificación de `IPC Protocol v1` (`ipc_spec.md`):**
   * Protocolo de framing sobre socket UNIX (`\n`-delimited JSON o TLV).
   * Esquemas de solicitud/respuesta: `command`, `args`, `request_id` y respuestas con `status` (`ok`, `error`, `rejected`), `payload` y `error_msg`.
3. **Especificación de `Config Schema v1`:**
   * Esquemas JSON formales para `core_profile.json` y `active_agenda.json` con validaciones de tipos, rangos y valores por omisión.
4. **Definición de Máquina de Estados Determinista:**
   * Estados formales: `IDLE`, `POMODORO_RUNNING`, `POSTPONE_RUNNING`, `BREAK_RUNNING`, `CRITICAL_BREAK_REQUIRED`.
   * Matriz de transiciones permitidas, condiciones de guarda e invariantes estrictas (el centinela de 60 minutos bloquea cualquier prórroga).
5. **Formato Estándar de Logs y Benchmark Suite Base:**
   * Formato de log estructurado por proceso (`timestamp [LEVEL] [component] message`).
   * Harness de benchmark para medir cold-start, latencia de socket IPC y rendimiento del parser JSON.

### 0.3 Matriz de Riesgos y Mitigación
* **Desalineación de esquemas en fases posteriores:** Validar cada payload contra el esquema en pruebas unitarias automáticas.
* **Sobrecarga de serialización en IPC:** Mantener payloads mínimos (<1 KB) para no penalizar la latencia P95.

### 0.4 Estimación Temporal
* Definición formal de esquemas (Event, Config, IPC): 2.5 h.
* Modelado y matriz de transiciones de máquina de estados: 1.5 h.
* Estándar de logging y harness base de benchmarks: 2.0 h.
* **Subtotal Fase 0: 6.0 horas.**

### 0.5 Criterio de Verificación
Validadores de esquemas en Python stdlib capaces de validar payloads sintéticos de prueba para todos los tipos de eventos y comandos IPC sin fallos.

---

## Fase 1: Cimientos del Sistema de Archivos, Esquemas y Persistencia Atómica Concurrente — COMPLETADO (Gate F1.4: PASS)

### 1.1 Objetivo y Alcance Técnico (Subfase 1.1: COMPLETADO | Subfase 1.2: COMPLETADO | Subfase 1.3: COMPLETADO | Gate F1.4: PASS)
Establecer la infraestructura de almacenamiento local en `~/.siegfried/`, garantizando inicialización segura, validación no destructiva de configuración, permisos POSIX estrictos (0700/0600), protección contra symlinks y concurrencia libre de corrupción entre múltiples procesos (demonio y CLI) mediante primitivas del kernel de Linux (`fcntl`), aislando credenciales con `siegfried init` y diagnosticando con `siegfried doctor`.

### 1.2 Tareas de Ingeniería
1. **Scaffolding de Directorios y Control de Permisos (`initialization.py`, `siegfried init`):**
   * Crear el árbol: `~/.siegfried/{config,data,assets/sounds,logs}`.
   * Inicializar `secrets.env` con máscara `0o077` antes de la creación (`os.umask(0o077)`) para forzar permisos strictly `600` (`-rw-------`).
   * Validar y preservar configuraciones `core_profile.json`, `active_agenda.json` y `siegfried_vault.jsonl` de forma idempotente.
2. **Validación Estricta de Configuración, Permisos y Seguridad (`validation.py`, `siegfried doctor`):**
   * Validación centralizada y no destructiva de `core_profile.json`, `active_agenda.json`, `secrets.env` y `siegfried_vault.jsonl`.
   * Distinción de estados: `READY`, `NOT_INITIALIZED`, `INVALID_CONFIG`, `INSECURE_PERMISSIONS`, `UNSAFE_PATH`, `STORAGE_ERROR`.
   * Integración en CLI (`siegfried doctor`) y verificación previa al arranque del daemon (`SiegfriedDaemon.start`).
3. **Persistencia, Durabilidad y Recuperación ante Fallos (`vault.py`, `atomic_json.py`, Gate F1.3):**
   * Auditoría de Vault no destructiva (`audit_vault()`, `VaultAuditResult`, detección de truncamientos de fin de archivo vs corrupción intermedia).
   * Protección contra concatenación de líneas truncadas en `Vault.append()`.
   * `fsync` en directorio padre tras `os.replace` para garantizar durabilidad POSIX en inodos y entradas de directorio.
   * Detección no destructiva de archivos temporales huérfanos (`.tmp.*`) en `config/` y `data/`.
   * Pruebas exhaustivas de inyección de fallos (`test_persistence_faults.py`) y recuperación de caídas con `SIGKILL` (`test_crash_recovery.py`).
4. **Definición y Validación de Esquemas JSON Base:**
   * `core_profile.json`: Estructura para registrar datos biológicos, límite postural (60 min), cursos activos, materias críticas y stack (.NET/C#).
   * `active_agenda.json`: Estructura del embudo con llaves fijas: `critical_task` (objeto o `null`), `secondary_tasks` (array de hasta 2 objetos), y `backlog` (array de tareas diferidas).
   * `siegfried_vault.jsonl`: Archivo inicial sin encabezados no-JSON (JSONL puro desde la primera línea con `Event Schema v1`).
5. **Módulo `storage.py` (Librería Estándar de Python):**
   * **Escritura Atómica en JSONL (`append_vault`):** Apertura en modo append (`a`), obtención de bloqueo exclusivo `fcntl.flock(fd, fcntl.LOCK_EX)` sobre el archivo de bitácora, serialización JSON pura (incluyendo `v`, `ts`, `type`, `data`) + salto de línea, vaciado forzado de buffers a disco (`f.flush()`, `os.fsync(fd)`) y liberación del cerrojo (`fcntl.LOCK_UN`).
   * **Reemplazo Atómico con Lockfile Dedicado para JSON (`atomic_write_json`):**
     * Para archivos modificables (`active_agenda.json`), usar un archivo de cerrojo independiente (`active_agenda.lock`).
     * Flujo estricto: `flock(lock_fd, LOCK_EX) → escribir archivo .tmp → flush() → os.fsync() → os.replace(.tmp, final) → fsync(parent_dir) → flock(lock_fd, LOCK_UN)`.
     * Esto resuelve definitivamente el problema de pérdida o desincronización de cerrojos `fcntl` provocado por el reemplazo atómico de inodos en POSIX.
   * **Lectura Segura con Bloqueo Compartido (`read_vault_tail` / `read_json`):** Para JSON mutables, sincronizar con el lockfile correspondiente usando `fcntl.flock(lock_fd, fcntl.LOCK_SH)`. Para `siegfried_vault.jsonl`, bloqueo compartido directo sobre el fd.

### 1.3 Matriz de Riesgos y Mitigación
* **Deadlock en `fcntl.flock`:** Usar bandera no bloqueante combinada `fcntl.LOCK_EX | fcntl.LOCK_NB` dentro de un bucle de reintento con backoff exponencial (máximo 5 reintentos de 10 ms). Abortar con excepción controlada en lugar de colgar el proceso.
* **Líneas corruptas en `vault.jsonl`:** `os.fsync()` obligatorio antes de liberar el candado. Si una línea no parsea con `json.loads()`, registrar alerta en log estructurado y derivar la línea defectuosa a `vault.corrupt.log` sin frenar la bitácora.
* **Fuga de permisos en `secrets.env`:** Validación en tiempo de arranque en `storage.py`. Si los permisos no son `600`, lanzar error fatal y detener el inicio.

### 1.4 Estimación Temporal
* Scaffolding y permisos: 1.5 h.
* Módulo `storage.py` con lockfiles dedicados (`.lock`), reemplazo atómico y JSONL versionado: 3.5 h.
* Pruebas de concurrencia (1,000 escrituras y reemplazos paralelos): 2.0 h.
* **Subtotal Fase 1: 7.0 horas.**

### 1.5 Criterio de Verificación
20 procesos escribiendo 50 eventos en paralelo sobre `siegfried_vault.jsonl` y 10 procesos actualizando concurrentemente `active_agenda.json` bajo carga:
1. `wc -l ~/.siegfried/data/siegfried_vault.jsonl` debe reportar exactamente 1,000 líneas válidas verificables con `jq .` (todas con `v: 1`).
2. Cero colisiones, corrupciones o lecturas incompletas en `active_agenda.json`.

---

## Fase 2: Motor de Inferencia Híbrido, Presupuesto de Recursos y Fallback Local

### 2.1 Objetivo y Alcance Técnico (Subfase 2.1: COMPLETADO — Gate F2.1: PASS | Subfase 2.2: COMPLETADO — Gate F2.2: PASS | Subfase 2.3: COMPLETADO — Gate F2.3: PASS)
Desarrollar el subsistema de inferencia sin dependencias externas (`urllib.request`), implementando la política de tolerancia de red con deadline estricto de 10 segundos, gestor de ciclo de vida perezoso (*lazy loading*) de `llama-server` bajo presupuesto estricto de VRAM (≤3.0 GiB), enrutador regex sub-milisegundo (Fast-Path), orquestador híbrido con fallback seguro determinista y observabilidad protegida.

### 2.2 Tareas de Ingeniería
1. **Cliente de Inferencia Cloud con Timeouts Granulares (`cloud.py`, Gate F2.1: COMPLETADO):**
   * Payload HTTP POST compatible con OpenAI/DeepSeek vía `urllib.request`, `ssl` y `json`.
   * **Contratos formales de inferencia (`inference.py`):** `InferenceMessage`, `InferenceRequest`, `InferenceResponse`, `InferenceUsage`.
   * **Cero timeouts globales:** Prohibido terminantemente `socket.setdefaulttimeout()`. Cada request y conexión configuran timeouts granulares independientes.
   * **Deadline Monotónico con `time.monotonic()`:** Presupuesto temporal inmutable evaluado antes del inicio, durante la conexión y entre bloques de lectura.
   * **Seguridad y Privacidad Estricta:** HTTPS obligatorio para proveedores externos con TLS verification forzada (`CERT_REQUIRED`), bloqueo de `CERT_NONE`, `SafeRedirectHandler` para prevenir degradación de protocolos, lectura acotada por tamaño (`max_response_bytes`) para evitar DoS por memoria, y sanitización total de API keys/Bearer tokens en logs, representaciones (`__repr__`) y excepciones (`InferenceAuthError`, `InferenceConfigError`, `InferenceHTTPError`, `InferenceRateLimitError`, `InferenceTimeoutError`, `InferenceResponseError`).
   * **Cargador de Secretos (`secrets.py`):** Lectura protegida de `secrets.env` con permisos `0600` e inspección sin symlinks inseguros.

2. **Supervisor de Proceso Local y Presupuesto de Recursos (`llama_manager.py`, `resources.py`, `local.py`, Gate F2.2: COMPLETADO):**
   * **Control de `llama-server` vía `subprocess.Popen` (`shell=False`):** Custodia estricta del PID en `/run/user/$UID/siegfried_llama.pid`. Terminación quirúrgica propia (`SIGTERM` con escalamiento a `SIGKILL` tras timeout) sin usar `pkill` ni `killall`.
   * **Máquina de Estados del Servidor Local:** `UNAVAILABLE`, `STOPPED`, `STARTING`, `READY`, `BUSY`, `STOPPING`, `FAILED`.
   * **Detección de Recursos y Presupuesto:** Inspección no destructiva de RAM y GPU (`/proc/meminfo`, `/sys/class/drm`), soporte CPU-only, techo estricto de VRAM GPU de **≤3.0 GiB** y reserva innegociable de RAM para el sistema operativo (2.0 GiB).
   * **Health Checks y Loopback Security:** Verificación de salud acotada sobre `http://127.0.0.1:{port}/health` con detección instantánea de terminación prematura. Rechazo de binds públicos (`0.0.0.0`) y colisiones de puerto (`PortInUseError`).
   * **Idle Eviction (15 min):** Apagado automático del servidor y liberación al 100% de la VRAM tras 900 segundos de inactividad, con protección ininterrumpida de inferencias activas.
   * **Cliente HTTP Local (`local.py`):** Integración compatible con OpenAI API de `llama-server`, reutilizando contratos de F2.1 con deadlines monotónicos y lectura acotada de respuestas.

3. **Orquestador Híbrido, Routing Determinista y Fallback Seguro (`orchestrator.py`, Gate F2.3: COMPLETADO):**
   * **Políticas de Selección Explícitas:** `CLOUD_PREFERRED`, `LOCAL_PREFERRED`, `LOCAL_ONLY` y `CLOUD_ONLY`.
   * **Presupuesto Temporal Monotónico Compartido:** Deadline global absoluto (10.0s) evaluado una única vez al ingresar y compartido entre primary y fallback sin reinicios de reloj. Fallback acotado por margen de seguridad (`min_fallback_margin_seconds=0.5s`) y estimación de arranque local (`estimated_local_startup_seconds=2.0s`).
   * **Fallback Seguro y Clasificación Estricta:** Máximo 1 intento primario + 1 fallback (cero tormentas o bucles de reintentos). Errores recuperables (timeouts, transporte/red, HTTP 429/5xx, caída de proceso local) vs errores no recuperables (violaciones de seguridad, esquemas inválidos, contratos corruptos, límites de deadline).
   * **Precedencia Absoluta de Privacidad:** Confinamiento estricto en `LOCAL_ONLY` (cero transmisiones externas bajo cualquier circunstancia; fallos locales devuelven error controlado y nunca degradan a Cloud).
   * **Observabilidad Segura:** Sanitización total de trazas, excepciones y registros; cero fuga de prompts de usuario, respuestas del modelo, Bearer tokens o claves privadas.
   * **Microbenchmark Routing:** Decisión del orquestador en memoria verificada en P95 de 0.0050 ms (< 1.0 ms).

4. **Integración Funcional CLI / REPL / IPC / Daemon / Inferencia Híbrida (`app.py`, `repl.py`, `router.py`, `server.py`, Gate F2.4: COMPLETADO):**
   * **Recorrido A (Fast-Path Determinista):** CLI / REPL -> `CommandRouter` -> IPC (`STATUS`, `FOCUS`, etc.) -> Reactor Daemon -> Core determinista -> Respuesta directa en submilisegundos (P95: 0.0013 ms).
   * **Recorrido B (Consulta Cognitiva):** `siegfried ask "<pregunta>"` y entrada conversacional libre en REPL -> `CommandRouter` -> IPC (`QUERY`) -> Daemon -> `InferenceOrchestrator` -> Cloud/Local -> Respuesta normalizada.
   * **Reactor Asíncrono no Bloqueante:** Despacho de consultas `QUERY` a hilos de trabajo dedicados en `IPCServer._handle_async_client()`, liberando inmediatamente el bucle `selectors` para que los cronómetros `MonotonicTimer` y comandos fast-path se ejecuten a tiempo y sin latencia.
   * **Confinamiento de Privacidad y Fallback Seguro:** Respeto estricto del parámetro `--policy` (`LOCAL_ONLY` garantiza cero llamadas de red externas); degradación resiliente con mensajes limpios al usuario sin trazas de Python ni fugas de tokens `sk-*` o `Bearer`.
   * **Suite de Pruebas de Integración:** 38 tests automatizados en `test_functional_integration_f24.py` validando ambos recorridos, no-bloqueo del reactor, degradación, privacidad y UX CLI/REPL (Suite acumulada: 252 tests PASS).

5. **Hardening de Concurrencia, Backpressure y Apagado Seguro (`server.py`, `errors.py`, Gate F2.4.1: COMPLETADO):**
   * **Política de Capacidad Acotada:** `MAX_COGNITIVE_WORKERS = 3`, `MAX_PENDING_COGNITIVE_QUERIES = 0`. Máximo 3 consultas cognitivas admitidas concurrentemente; reserva atómica no bloqueante mediante `threading.BoundedSemaphore(3)`.
   * **Backpressure Inmediato:** Una cuarta consulta cognitiva se rechaza inmediatamente en <1 ms con error tipado `InferenceBusyError` (`IPCStatus.REJECTED`, código `"INFERENCE_BUSY"`, mensaje `"El motor de inferencia está ocupado. Inténtalo nuevamente."`) sin crear hilos adicionales, sin encolar y sin bloquear el reactor.
   * **Independencia del Fast-Path:** Comandos deterministas (`STATUS`, `PING`, `START_FOCUS`, etc.) no compiten por cupos cognitivos y responden en submilisegundos (P95: 0.0013 ms) incluso bajo saturación total de las 3 consultas.
   * **Liberación de Capacidad:** Garantizada exactamente una vez en bloques `finally` ante éxito, error HTTP, excepción del orquestador o desconexión del cliente.
   * **Protocolo de Apagado Seguro e Idempotente:** `stop(timeout_seconds=2.0)` cierra el socket de escucha para impedir nuevas conexiones, rechaza admisiones tardías con `SERVER_STOPPING`, espera a los trabajadores activos de forma acotada y reporta el estado real (`clean_shutdown` vs incompleto con registro de `residual_workers`).
   * **Limitaciones Reales de Cancelación:** En Python stdlib, llamadas bloqueantes de socket/red no disponen de interrupción forzada segura; el protocolo registra transparentemente cualquier trabajador residual en lugar de ocultarlo con `daemon=True`.
   * **Suite de Pruebas de Concurrencia:** 38 tests automatizados en `test_ipc_backpressure_f241.py` con sincronización determinista (`Event`, `Barrier`) sin sleeps arbitrarios (Suite acumulada: 290 tests PASS).

6. **Frontera Determinista / LLM y Calibrador de Tono:**
   * Aplicación estricta de la regla: **“El LLM interpreta y recomienda; el núcleo determinista valida, calcula y ejecuta.”**
   * Entradas <7 palabras inyectan directiva `[MODO: EJECUTIVO]`.
   * Entradas >25 palabras inyectan `[MODO: MAYORDOMO_COMPLETO]`.

7. **Auditoría Integral, Pruebas de Resiliencia y Cierre Formal (Gate F2.5: COMPLETADO / PASS):**
   * Auditoría técnica independiente documentada en `docs/gates/GATE_F2_5_REPORT.md`.
   * Blindaje contra fuga de credenciales en redirecciones cross-host (`SafeRedirectHandler` elimina cabecera `Authorization` hacia dominios ajenos).
   * Regresión completa de 291 pruebas PASS (0 fallos, 0 errores en 9.24s).
   * Verificación de SLOs: Fast-Path Router P95 = 0.0024 ms, Vault Append P95 = 2.24 ms, CLI Cold-Start P95 = 45.75 ms, Orchestrator Routing P95 = 0.0049 ms.
   * Cierre formal de la Fase 2 aprobado.

### 2.3 Matriz de Riesgos y Mitigación
* **Proceso Zombie de `llama-server` reteniendo VRAM:** Manejador de salida con `atexit.register()`, captura de `SIGTERM` y guardado de PID en `/run/user/$UID/siegfried_llama.pid`.
* **Falsos positivos en regex:** Delimitadores estrictos (`^...$`) y longitud máxima de 12 palabras. Si la frase es más larga, derivar a la Vía Cognitiva (LLM).
* **Interferencia en sockets concurrentes:** Prohibir terminantemente `socket.setdefaulttimeout()` para que las llamadas de inferencia no alteren el comportamiento de sockets de IPC u otros módulos.
* **Tormentas de reintentos en degradación de red:** Tope estricto de 2 intentos totales por solicitud gobernados por un deadline inmutable.
* **Bloqueo del reactor durante inferencias lentas:** Delegación de comandos `QUERY` a worker threads dedicados en el servidor IPC, desregistrando el cliente del reactor para mantener latencia P95 < 10 ms en comandos deterministas y disparos exactos de timers.
* **Proliferación descontrolada de hilos:** Backpressure estricto mediante semáforo acotado con tope de 3 trabajadores cognitivos y cero cola de espera; la 4ta consulta se rechaza de inmediato con `INFERENCE_BUSY`.
* **Fuga de tokens en redirecciones HTTP:** `SafeRedirectHandler` elimina `Authorization` en cualquier redirección que altere el hostname de destino.

### 2.4 Estimación Temporal
* Cliente HTTP con timeout granular y deadline monotónico: 2.5 h.
* Gestor lazy load / idle timeout de `llama-server` con monitoreo de presupuesto VRAM: 4.0 h.
* Regex Fast-Path y calibrador de tono: 2.5 h.
* Orquestador híbrido, políticas y fallback seguro: 3.5 h.
* Integración funcional CLI / REPL / IPC / Daemon / Worker threads (F2.4): 3.0 h.
* Hardening de concurrencia, backpressure y apagado seguro (F2.4.1): 2.0 h.
* Auditoría integral, resiliencia y cierre formal (Gate F2.5): 1.5 h.
* Pruebas de descarga de VRAM con `nvidia-smi` y benchmarking de latencia: 2.0 h.
* **Subtotal Fase 2: 21.0 horas.**

### 2.5 Criterio de Verificación
1. Entrada *"iniciar bloque de 50"* responde cumpliendo SLO Fast-Path P95 `<10 ms` (0.0013 ms).
2. Al desconectar la interfaz de red, el deadline de 10s monotonic expira con precisión, activa `llama-server` manteniéndolo dentro del presupuesto de VRAM (≤3.0 GiB) y el idle timeout de 15 min lo apaga limpiamente.
3. Fallback seguro desde Cloud a Local ejecuta en menos del tiempo restante y preserva estricta privacidad bajo `LOCAL_ONLY`.
4. El comando `siegfried ask` y el REPL conversacional obtienen respuestas de inferencia híbrida a través del daemon sin bloquear el bucle de timers ni las consultas de estado concurrentes.
5. Bajo 3 consultas cognitivas concurrentes, una 4ta consulta se rechaza inmediatamente sin encolar y sin crear hilos adicionales, manteniendo las consultas deterministas de estado respondiendo en < 10 ms.
6. Fase 2 formalmente auditada y cerrada mediante Gate F2.5 (291 tests PASS, cero regresiones).


---

## Fase 3: Demonio en Segundo Plano (`siegfried-daemon`), Timers y Gestión Quirúrgica de Alertas

### 3.1 Objetivo y Alcance Técnico
Construir el servicio autónomo administrado por `systemd --user` que orqueste la máquina de estados determinista de salud, administre el tiempo de asiento continuo (límite innegociable de 60 minutos), escuche conexiones IPC (`IPC Protocol v1`) vía socket Unix y controle las alarmas visuales y auditivas custodiando quirúrgicamente los subprocesos de audio.

### 3.2 Tareas de Ingeniería
1. **Servidor de Socket Unix (`daemon.py`):**
   * Socket en `/run/user/$UID/siegfried.sock`.
   * Bucle reactivo no bloqueante con `selectors` para atender la CLI y los cronómetros en el mismo hilo.
   * Despacho y validación estricta de mensajes según `IPC Protocol v1`.
2. **Máquina de Estados Determinista y Centinela de Postura:**
   * Estados: `IDLE`, `POMODORO_RUNNING`, `POSTPONE_RUNNING`, `BREAK_RUNNING`, `CRITICAL_BREAK_REQUIRED`.
   * Contador postural continuo: Aviso al minuto 50. Permite prórroga hasta el minuto 60. Al cumplirse 60 min, **barrera dura**: rechaza prórrogas y pasa a `CRITICAL_BREAK_REQUIRED`. Las transiciones son exclusivamente deterministas.
3. **Notificador y Alerta Sensorial Quirúrgica (`notifier.py`):**
   * Notificación en KDE Plasma vía `notify-send` o `kdialog`.
   * Reproducción sonora asíncrona: `pw-cat -p ~/.siegfried/assets/sounds/leaver.ogg` (fallback a `paplay`).
   * **Eliminación total de `pkill pw-cat`:** `notifier.py` almacena la instancia exacta de `subprocess.Popen` y su PID. Para cancelar la alarma, invoca `process.terminate()` directamente sobre dicho proceso, aplicando `process.kill()` si no responde tras 500 ms.
4. **Servicio Systemd de Usuario:**
   * Unidad `~/.config/systemd/user/siegfried.service` (`Restart=always`, `RestartSec=2s`). Cumplimiento del SLO: daemon idle CPU `<0.2 %`, RSS `<30 MB`.

### 3.3 Matriz de Riesgos y Mitigación
* **Address already in use en Socket Unix:** Probar conexión con `connect()`; si no responde, desvincular con `os.unlink()` antes de `bind()`.
* **Drift en timers por suspensión de la laptop:** Prohibido usar `sleep()` para cronometrar. Comparar siempre marcas de tiempo absolutas contra `time.monotonic()`.
* **Muerte de procesos de audio no deseados:** Al evitar comandos globales como `pkill`, se garantiza que Siegfried jamás interrumpa reproductores de audio externos del usuario.

### 3.4 Estimación Temporal
* Servidor Socket Unix con `selectors` e IPC Protocol v1: 3.0 h.
* Máquina de estados determinista y centinela de 60 min: 4.0 h.
* Integración audio/notificación con gestión quirúrgica de PID: 2.5 h.
* Configuración y pruebas con `systemd --user`: 2.5 h.
* **Subtotal Fase 3: 12.0 horas.**

### 3.5 Criterio de Verificación
Simular 60 minutos sentado:
1. Se reproduce el sonido de alerta sin congelar el demonio.
2. Notificación visible en KDE.
3. Rechazo determinista automático de nuevas prórrogas.
4. Comando `ACK_BREAK` mata únicamente el proceso hijo del reproductor (verificado por PID) sin afectar otros procesos del sistema.

### 3.6 Consolidación del Daemon, Supervisión systemd --user y Recuperación Operativa (Gate F3.1: COMPLETADO / PASS)
1. **Contrato de Arranque Determinista y Fail-Closed:**
   * El daemon valida exhaustivamente el runtime en arranque (`_validate_startup_runtime`).
   * Runtimes no inicializados o directorios faltantes lanzan `RuntimeNotInitializedError` sin crear silenciosamente directorios.
   * Configuraciones JSON corruptas o ilegibles (`core_profile.json`, `active_agenda.json`) abortan el inicio de forma no destructiva sin alterar ni sobrescribir archivos.
   * Entrypoints (`bin/siegfried-daemon` y `python3 -m siegfried.daemon`) capturan `StorageError` y finalizan con código `EX_CONFIG` (78).
   * Operación 100% independiente de claves API de Cloud o modelos locales descargados.
2. **Propiedad y Seguridad del Socket IPC:**
   * El socket se crea en `/run/user/$UID/siegfried.sock` con máscara privada atómica (`umask 0o177` -> `0600`).
   * Validación estricta del directorio padre: pertenencia al UID actual y permisos privados sin acceso de grupo ni otros.
   * Protección anti-symlink: rechazo explícito con `UnsafePathError` si la ruta del socket es un enlace simbólico.
   * Protección de tipo de archivo: rechazo explícito si existe un archivo regular u otro tipo que no sea un socket UNIX.
   * Protección de pertenencia: rechazo con `InsecurePermissionsError` si el socket pertenece a otro UID; queda terminantemente prohibido desvincular sockets ajenos.
   * Recuperación segura de sockets obsoletos: intento de conexión con timeout corto (0.5s); si la conexión es rechazada (`ConnectionRefusedError`), se confirma la terminación del proceso anterior y se elimina de forma segura el socket propio antes del bind. Si la conexión responde, se aborta con error para evitar instancias duplicadas.
   * Parada limpia: `stop()` verifica tipo y UID antes de desvincular el socket, asegurando idempotencia.
3. **Supervisión de systemd --user y Política de Fallos:**
   * Sustitución de `Restart=always` por `Restart=on-failure` en `systemd/siegfried.service`.
   * Prevención de bucles de reinicio ante fallos permanentes mediante `RestartPreventExitStatus=78`.
   * Delimitación de tasa de reinicios: `StartLimitIntervalSec=30s`, `StartLimitBurst=5`, `RestartSec=2s`.
   * Parada acotada: `TimeoutStopSec=5s`, `KillMode=control-group`.
   * Hardening de unidad: `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=%h/.siegfried /run/user/%U`.
4. **Instalador Reproducible sin Sudo (`tools/install_user_service.py`):**
   * Despliegue en espacio de usuario (`~/.siegfried/bin` y `~/.config/systemd/user/siegfried.service`) con permisos estrictos.
   * Soporte de aislamiento con `--home <dir>` para pruebas y CI.
   * Verificación estática con `systemd-analyze verify` sin requerir sudo ni alterar servicios reales del usuario.
5. **Temporizadores Monotónicos y Resiliencia de Eventos:**
   * Conteo estricto con `time.monotonic()` inmune a variaciones y saltos del reloj de pared (`time.time()`).
   * Recuperación determinista de estado en reinicio: lectura protegida de `active_agenda.json` para restaurar la tarea activa sin duplicar eventos en el Vault.
   * Persistencia en el Vault estrictamente atómica con `fcntl.flock` y `os.fsync`.
6. **Resultados de Validación:**
   * Suite de pruebas automatizada: 309 tests PASS (291 heredados + 17 Gate F3.1 + 1 E2E procesos reales OS).
   * Benchmarks de rendimiento: Fast-Path P95 = 0.0013 ms, Vault Append P95 = 2.23 ms, CLI Cold-Start P95 = 47.90 ms, Orchestrator Microbenchmark P95 = 0.0049 ms (Todos PASS).
   * Cierre formal de Gate F3.1: **PASS**.

### 3.7 Gestión Quirúrgica de Alertas, Notificaciones KDE Plasma y Audio (Gate F3.2: COMPLETADO / PASS)
1. **Contratos Formales de Alertas (`src/siegfried/contracts/alerts.py`):**
   * Tipos de alerta: `POMODORO_COMPLETED`, `POSTURE_WARNING`, `POSTURE_LIMIT_REACHED`, `BREAK_STARTED`, `BREAK_COMPLETED`, `POMODORO_CANCELLED`.
   * Niveles de urgencia Freedesktop: `LOW`, `NORMAL`, `CRITICAL`.
   * Estados de entrega auditables: `PENDING`, `DELIVERED`, `FAILED`, `SUPPRESSED`.
   * Contrato `NotificationAttempt` con registro de código de retorno, error y latencia para observabilidad.
2. **Emisor Quirúrgico de Notificaciones KDE Plasma (`src/siegfried/integrations/notifications.py`):**
   * Invocación segura mediante lista de argumentos `subprocess.run` (prohibido `shell=True`).
   * Detección de sesión gráfica activa (`WAYLAND_DISPLAY`, `DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`).
   * Fallback elegante y degradación transparente en entornos headless o sin servidor D-Bus; los eventos deterministas persisten sin interrumpir el daemon.
   * Sanitización de texto en alertas: filtrado preventivo de tokens y secretos (`sk-*`, `Bearer`).
   * Timeout de ejecución acotado (2.0 s) para evitar bloqueos del sistema.
3. **Controlador Quirúrgico de Audio PipeWire/ALSA (`src/siegfried/integrations/audio.py`):**
   * Descubrimiento determinista de backend nativo: `pw-cat` (PipeWire nativo), `pw-play`, `paplay` (PulseAudio), `aplay` (ALSA).
   * Custodia estricta de subprocesos: almacenamiento atómico del objeto `subprocess.Popen` y su PID exacto. Prohibición absoluta de comandos globales (`pkill`, `killall`).
   * Supresión de reproducciones duplicadas del mismo archivo si ya está en curso.
   * Cancelación quirúrgica: envío de `SIGTERM` con ventana de gracia (0.5 s), escalado a `SIGKILL` si no responde y recolección obligatoria del descriptor (`wait()`) para eliminar procesos zombi.
   * Validación rigurosa de rutas sonoras: archivo existente, regular y con extensiones de audio autorizadas (`.ogg`, `.wav`, `.oga`, `.flac`).
4. **Coordinador Asíncrono de Alertas (`src/siegfried/daemon/alerts.py`):**
   * Desacoplamiento total del reactor: trabajador en segundo plano (`siegfried-alert-worker`) alimentado por cola acotada (`maxsize=16`).
   * Despacho no bloqueante: la lentitud o caída del bus de notificaciones jamás congela el reactor `selectors` ni los cronómetros `MonotonicTimer`.
   * Deduplicación por ventana de idempotencia (5.0 s) para prevenir inundación sensorial.
   * Desalojo preventivo de baja urgencia ante saturación de cola en favor de alertas críticas de postura.
5. **Centinela Determinista de Postura y Gestión de Enfoque (`src/siegfried/daemon/app.py`):**
   * Acumulación monotónica continua de tiempo sentado durante `POMODORO_RUNNING` y `POSTPONE_RUNNING`.
   * Aviso al minuto 50 (`POSTURE_WARNING`) con urgencia `NORMAL`.
   * Barrera dura a los 60 minutos: transición a `CRITICAL_BREAK_REQUIRED`, sonido de alerta persistente, urgencia `CRITICAL` y rechazo automático de prórrogas.
   * Manejo determinista de comandos IPC:
     - `POSTPONE`: valida disponibilidad de prórrogas (`can_postpone`); concede 5 min adicionales o rechaza si se superan los 60 min totales de asiento continuo.
     - `ACK_BREAK`: silencia el audio de forma quirúrgica, resetea el tiempo sentado acumulado e inicia la pausa activa.
     - `CANCEL_FOCUS`: silencia el audio inmediatamente y emite confirmación de cancelación.
6. **Resultados de Validación:**
   * Suite de pruebas automatizada: 329 tests PASS (309 heredados + 20 Gate F3.2 nuevos), 0 fallos, 0 errores.
   * Benchmarks de rendimiento: Fast-Path P95 = 0.0020 ms, Vault Append P95 = 2.17 ms, CLI Cold-Start P95 = 47.86 ms, Orchestrator Microbenchmark P95 = 0.0047 ms (Todos PASS).
   * Cierre formal de Gate F3.2: **PASS**.

### 3.8 Endurecimiento de Concurrencia de Alertas, Idempotencia y Cierre de Fase 3 (Gate F3.3: COMPLETADO / PASS)
1. **Política de Capacidad y Desalojo Selectivo por Prioridad:**
   * Capacidad de cola acotada inmutable (`max_queue_size = 16`) procesada por trabajador único (`siegfried-alert-worker`).
   * Bajo saturación, una alerta `CRITICAL` de postura desaloja selectivamente alertas de menor urgencia (`LOW` primero, luego `NORMAL`) registrando el desalojo en la auditoría con `DeliveryStatus.FAILED`.
   * Si la cola se encuentra saturada exclusivamente con alertas `CRITICAL`, la nueva alerta crítica es rechazada de forma explícita sin desalojar ni descartar las alertas críticas preexistentes en la cola, registrando `NotificationAttempt(accepted=False, error_msg="Alert queue saturated exclusively with CRITICAL alerts (capacity reached)")`.
   * **Invariante de Dominio:** La saturación o fallo de entrega sensorial jamás interrumpe, altera ni corrompe el evento determinista de dominio persistido y sincronizado atómicamente con `os.fsync` en `siegfried_vault.jsonl`.
2. **Idempotencia Estricta de Eventos y Deduplicación Sensorial:**
   * Vinculación obligatoria de `event_id` desde el evento de dominio hacia el subsistema de alertas (`AlertCoordinator`).
   * Generación determinista de identidad sin colisiones durante la sesión del daemon mediante `f"{event.type}:{event.ts:.6f}:{self._event_sequence}"` preservando inmutable `Event Schema v1`.
   * Registro atómico bajo lock que reserva el identificador **únicamente tras la admisión efectiva en la cola o entrega directa**; las alertas rechazadas por saturación no se marcan en falso y admiten reintentos.
   * Acotamiento estricto de memoria en runtime mediante `OrderedDict` con política FIFO limitada a `MAX_IDEMPOTENT_EVENT_IDS = 1000` (< 100 KB de RAM).
   * Política de reinicio determinista: el daemon no reproduce alertas históricas acumuladas en el Vault al inicializarse.
   * Reseteo explícito de deduplicación postural (`reset_posture_alerts()`) tras la confirmación determinista del descanso (`ACK_BREAK`).
3. **Resiliencia Concurrente, Aislamiento de Audio y Shutdown Auditable:**
   * Protocolo de parada limpia con reporte verificable de estado (`clean_shutdown: bool`) y timeout acotado.
   * Drenaje seguro de elementos pendientes en cola ante shutdown registrando explícitamente `NotificationAttempt` con fallo de entrega para auditoría.
   * Custodia de audio por PID propio: prohibición total de `pkill`/`killall`, garantizando aislamiento de reproductores externos del usuario.
   * Fast-Path determinista (`STATUS`, `PING`) inmune a la saturación de alertas, respondiendo en submilisegundos (P95 < 10 ms).
   * Optimización de cold-start de CLI en Python 3.14 con `FastHelpFormatter`, eliminando importaciones lentas de `_colorize` y `shutil` (`compression.zstd`, `bz2`, `inspect`).
4. **Resultados de Validación y Regresión:**
   * Suite de pruebas automatizada: **373 tests PASS** (329 heredados + 44 Gate F3.3 en `tests/integration/test_alert_reliability_f33.py`), 0 fallos, 0 errores, 0 omitidas.
   * Cumplimiento de SLOs (medido en 3 ejecuciones secuenciales): Fast-Path P95 = 0.0034 ms, Vault Append P95 = 2.5492 ms, CLI Cold-Start P95 = 47.25 ms, Orchestrator Microbenchmark P95 = 0.0104 ms.
   * Cierre formal de Gate F3.3: **PASS** (Cierre formal de la Fase 3; listo para iniciar Fase 4).





---

## Fase 4: La Interfaz de Usuario REPL Interactiva y Pre-agregador Histórico

### 4.1 Objetivo y Alcance Técnico
Construir el ejecutable interactivo `siegfried` en modo REPL fluido, gestionando señales de terminal, ofreciendo atajos inmediatos (Espacio/Enter para cortar alarmas) e integrando el pre-agregador analítico de métricas con cumplimiento de SLO P95 `<10 ms` para 20,000 eventos.

### 4.2 Tareas de Ingeniería
1. **Consola REPL Interactiva (`repl.py`):**
   * Configuración de `readline` para historial con flechas y guardado en `~/.siegfried/data/.history`.
   * Prompt `[Siegfried] > ` y captura limpia de `Ctrl+C` y `Ctrl+D`.
   * Cumplimiento del SLO: CLI cold-start P95 `<50 ms`.
2. **Silenciado Rápido de Emergencia:**
   * Teclas `Enter` o `Espacio` en la terminal durante una alarma envían `ACK_BREAK` al demonio, ordenando el cese del audio por PID e iniciando la pausa activa.
3. **Módulo Pre-agregador Histórico Determinista (`aggregator.py`):**
   * Lectura en reversa con `f.seek()` desde el final del archivo en bloques para no saturar memoria.
   * Parseo de líneas bajo `Event Schema v1`.
   * Filtro por rangos temporales (*"esta semana"*, *"hoy"*), totalizando minutos netos, pausas cumplidas y desglose de horas por curso.
   * Regla determinista: El pre-agregador calcula las métricas matemáticas; el LLM solo redacta la respuesta en lenguaje natural a partir del bloque `<metricas_historicas>`.

### 4.3 Matriz de Riesgos y Mitigación
* **Degradación con archivos JSONL grandes (>100 MB):** Romper la lectura en reversa con `break` en cuanto se encuentre un timestamp fuera del rango solicitado.
* **Corrupción visual por códigos ANSI:** Delimitar secuencias ANSI con `\001` y `\002` para el cálculo exacto del ancho de línea en `readline`.
* **Daemon inactivo al abrir el CLI:** Timeout de conexión al socket de 0.2 s. Si no responde, emitir aviso elegante y continuar en modo directo sin bloquear la consola.

### 4.4 Estimación Temporal
* Bucle REPL, cold-start P95 <50 ms y señales de terminal: 3.0 h.
* Interceptación para corte sonoro seguro: 1.5 h.
* Algoritmo de lectura reversa y pre-agregación en `aggregator.py`: 3.5 h.
* Pruebas de estrés y benchmarking de agregación histórica: 1.5 h.
* **Subtotal Fase 4: 9.5 horas.**

### 4.5 Criterio de Verificación
Con 20,000 eventos sintéticos (`Event Schema v1`) en `vault.jsonl`:
1. El comando CLI inicia en frío en P95 `<50 ms`.
2. El pre-agregador totaliza las métricas en P95 `<10 ms`.
3. DeepSeek recibe los totales precalculados y formula la respuesta sin inventar números.

### 4.6 Estado y Certificación del Hito F4.1 (2026-10-09)
* **Estado:** **PASS** (100% de requisitos y garantías cumplidos).
* **Entregables Implementados:**
  1. `src/siegfried/cli/repl.py`: Shell interactivo `SiegfriedREPL` completo con soporte de `readline`, historial persistente de líneas (`~/.siegfried/data/.history`), manejo seguro de señales (`Ctrl+D`, `Ctrl+C` en prompt y durante consulta activa sin fugas ni interrupción del daemon), sondeo rápido no bloqueante (timeout 0.2 s), silenciado rápido de emergencia (`Enter` o `Espacio` ante alarma activa despacha `ACK_BREAK` silenciando el audio e iniciando el descanso), renderizado formateado y legible del estado del sistema (`STATUS`) y contexto conversacional efímero en memoria acotado a `max_context_turns * 2` mensajes.
  2. `src/siegfried/cli/router.py`: Reglas deterministas extendidas para `ping` (vía IPC) y `ayuda`/`help`/`?` (despacho local inmediato sin IPC ni LLM), garantizando precedencia estricta del Fast-Path determinista sobre la ruta cognitiva.
  3. `src/siegfried/storage/aggregator.py`: `HistoricalAggregator` y `AggregatedMetrics` con algoritmo de lectura reversa `_reverse_line_reader()` basado en bloques de 64 KB (`f.seek()`), parseo estricto de `Event Schema v1`, filtro temporal con corte anticipado (`break`) ante monotonía temporal, cálculo de métricas de salud/enfoque y formateo determinista del bloque XML `<metricas_historicas>`.
  4. `src/siegfried/storage/__init__.py`: Exportación formal de `HistoricalAggregator` y `AggregatedMetrics`.
  5. `tests/integration/test_repl_interactive_f41.py`: Suite exhaustiva con 38 pruebas (Grupos A, B, C, D, E) que validan el ciclo de vida del REPL, routing determinista, inferencia y backpressure, privacidad y agregación histórica, y E2E socket con daemon real.
  6. `tools/benchmark.py`: Microbenchmark formal de 20,000 eventos sintéticos (`Event Schema v1`) para `HistoricalAggregator`.
* **Regresión Completa:** **411 tests PASS** (373 heredados + 38 Gate F4.1), 0 fallos, 0 errores, 0 omitidos en 14.63 s.
* **Cumplimiento de SLOs:**
  - **Fast-Path Router P95:** 0.0032 ms (SLO < 10.0 ms) -> **PASS**
  - **Vault Append P95:** 2.6442 ms (SLO < 10.0 ms) -> **PASS**
  - **CLI Cold-Start P95:** 49.27 ms (SLO < 50.0 ms) -> **PASS**
  - **Orchestrator Decision P95:** 0.0094 ms (SLO < 1.0 ms) -> **PASS**
  - **Historical Aggregator P95 (20,000 eventos):** 4.2734 ms (SLO < 10.0 ms) -> **PASS**
* **Cierre Formal de Gate F4.1:** **PASS** (Listo para abordar F4.2).

### 4.7 Estado y Certificación del Hito F4.2 (2026-10-09)
* **Estado:** **PASS** (100% de requisitos y garantías cumplidos).
* **Entregables Implementados:**
  1. `src/siegfried/cli/repl.py`: Privacidad reforzada en Readline (B1: sólo comandos deterministas y comandos de salida persisten en `~/.siegfried/data/.history`, `set_auto_history(False)`, permisos `0600` estrictos, carga no destructiva del historial previo); silenciado de emergencia riguroso (B2: Enter/Espacio en silencio no altera estado determinista ni invoca `ACK_BREAK`, sólo silencia ante alarma activa).
  2. `src/siegfried/storage/aggregator.py`: Resiliencia temporal reforzada (B3: lookback tolerance window con `consecutive_older_events` y `max_skew_seconds` tolerando desorden leve y ajustes NTP sin truncamiento prematuro de lecturas); defensa robusta contra Prompt Injection y escaping XML (B4: sanitización de nombres de tareas con `xml.sax.saxutils.escape`, eliminación de caracteres de control y saltos de línea, truncado a 80 caracteres, tope a las 15 tareas principales y acumulación en 'Otras', e inclusión de comentario XML de sistema advirtiendo que las métricas son de sólo lectura y los nombres de tareas son texto no confiable).
  3. `src/siegfried/cli/router.py`: Detección determinista de consultas históricas (C1) mediante regexes precompiladas clasificando `time_window="today"` y `time_window="week"`, retornando metadatos para la ruta cognitiva sin penalizar el Fast-Path.
  4. `src/siegfried/daemon/app.py`: Inyección determinista de contexto histórico en el manejador de `IPCCommand.QUERY` (C2 y C4: cálculo de métricas deterministas e inyección de bloque `<metricas_historicas>` como mensaje de rol `system` previo a la consulta); política estricta de privacidad Cloud deny-by-default (C3: imposición de `InferencePolicy.LOCAL_ONLY` ante telemetría privada; rechazo formal con `IPCResponse.rejected` ante peticiones `CLOUD_ONLY`; prohibición total de fallback a Cloud ante fallos del motor local).
  5. `tests/integration/test_historical_inference_f42.py`: Suite exhaustiva con 20 pruebas cubriendo los 4 grupos críticos (Grupos A, B, C, D).
* **Regresión Completa:** **431 tests PASS** (411 heredados + 20 Gate F4.2), 0 fallos, 0 errores, 0 omitidos en 34.38 s.
* **Cumplimiento de SLOs:**
  - **Fast-Path Router P95:** 0.0030 ms (SLO < 10.0 ms) -> **PASS**
  - **Vault Append P95:** 2.5065 ms (SLO < 10.0 ms) -> **PASS**
  - **CLI Cold-Start P95:** 42.58 ms (SLO < 50.0 ms) -> **PASS**
  - **Orchestrator Decision P95:** 0.0078 ms (SLO < 1.0 ms) -> **PASS**
  - **Historical Aggregator P95 (20,000 eventos):** 3.6049 ms (SLO < 10.0 ms) -> **PASS**
* **Cierre Formal de Gate F4.2:** **PASS** (Listo para el siguiente hito de Fase 4).

---

## Fase 5: Integración Event-Driven con Wayland, KWin Focus Sanitizado y Boot Briefing

### 5.1 Objetivo y Alcance Técnico
Completar la integración en Kubuntu mediante un watcher de ventanas enfocado basado en eventos en KWin bajo Wayland (protegiendo la privacidad del usuario), cálculo de ventana estimada de descanso al encender el equipo y notificación matutina interactiva en KDE Plasma.

### 5.2 Tareas de Ingeniería
1. **Monitor de Ventanas Event-Driven en Wayland (`kwin_focus_watcher.js`):**
   * Sustituir el polling periódico ciego por un script KWin cargado en el compositor que se engancha a señales de cambio de foco (`workspace.windowActivated`).
   * Notifica cambios al daemon vía DBus o socket únicamente cuando ocurre una transición real de ventana.
   * **Privacidad y Sanitización Estricta:** El payload almacena únicamente `aplicacion` (ej. `code`, `firefox`), `categoria` (ej. `desarrollo`, `navegacion`) y `duracion`. **No se registran títulos de ventanas, URLs ni datos sensibles.**
2. **Cálculo de Ventana de Descanso Estimada:**
   * Script `boot_hook.py` al iniciar sesión en KDE.
   * Lee la hora del último `shutdown`/`suspend` y calcula heurísticamente:
     $$\text{ventana\_descanso\_estimada} = (\text{Hora Actual} - \text{Hora Apagado/Suspensión}) - 25\text{ minutos}$$
   * Almacenamiento formal explícito como estimación (`estimated_rest_window`), reconociendo la ausencia de biometría directa.
3. **Consulta Climática Resiliente:**
   * Endpoint de texto plano (`wttr.in/Lima?format=%c+%t+%C`) con timeout granular de 0.8 s. Si falla, continúa sin retrasar el saludo.
4. **Notificación Interactiva Matutina:**
   * `kdialog` / `notify-send` con acción interactiva. Si se confirma, lanza: `konsole -e siegfried`.

### 5.3 Matriz de Riesgos y Mitigación
* **Fallas en API de KWin Scripting:** Si el script de KWin se desactiva, el demonio funciona normalmente sin telemetría de foco sin interrumpir timers.
* **Arranque sin red:** Saludo determinista independiente de red. F5.3 omite el ping y solicita clima únicamente como opción posterior al primer mensaje, con plazo acotado; un fallo no impide saludar.
* **Suspensiones breves:** Umbral mínimo de 90 minutos para clasificar el encendido como sesión de descanso circadiano.

### 5.4 Estimación Temporal
* Script KWin event-driven sanitizado para Wayland: 3.5 h.
* Cálculo heurístico de `ventana_descanso_estimada`: 2.5 h.
* Boot briefing y consulta climática resiliente: 2.0 h.
* Lanzador `.desktop` en KDE Autostart: 1.5 h.
* Pruebas de ciclo de vida (suspensión, reinicio y validación de foco): 2.5 h.
* **Subtotal Fase 5: 12.0 horas.**

### 5.5 Criterio de Verificación
1. Al cambiar de ventana activa en KDE Plasma Wayland, el watcher notifica inmediatamente sin polling y se guarda el evento categorizado sin persistir títulos privados.
2. Tras reinicio de máquina, en <2 s aparece la notificación con la `ventana_descanso_estimada` calculada y el botón interactivo que despliega el REPL.

### 5.6 Estado histórico de Verificación de Gate F5.1
* **Componentes Implementados:**
  1. `scripts/kwin_focus_watcher.js`: Watcher event-driven para KWin 6 (KDE Plasma 6 / Wayland) suscrito a `workspace.windowActivated`. Zero polling, privacidad estricta (no captions, no URLs, no paths).
  2. `scripts/kwin_focus_watcher/metadata.json` y `contents/code/main.js`: Estructura KPackage formal para KWin 6.
  3. `src/siegfried/daemon/focus.py`: `FocusTracker` determinista con cálculo monotónico (`time.monotonic()`), manejo de foco nulo, resiliencia ante suspensión/bloqueo de pantalla y deduplicación. `FocusDBusAdapter` en el bus de sesión D-Bus (`org.siegfried.FocusWatcher`).
  4. Integración en `SiegfriedDaemon`: Inicio y parada cleanly coordinados con el reactor sin impacto en timers de Pomodoro, postura ni alertas.
  5. `tests/integration/test_kwin_focus_f51.py`: Suite exhaustiva con 26 pruebas cubriendo Grupos A, B, C, D y E.
* **Validación Real en KDE Plasma 6 Wayland:**
  - Script cargado dinámicamente en KWin 6.6 (`qdbus6 org.kde.KWin /Scripting loadScript`).
  - D-Bus connection a `org.siegfried.FocusWatcher` verificada bidireccionalmente.
  - Evento real de foco generado y persistido en `siegfried_vault.jsonl` bajo `Event Schema v1` (`window_focus_sampled`) sin fugas de títulos ni URLs.
* **Regresión Completa:** **464 tests PASS** (438 heredados + 26 Gate F5.1), 0 fallos, 0 errores, 0 omitidos en 37.31 s.
* **Cumplimiento de SLOs:**
  - **Fast-Path Router P95:** 0.0016 ms (SLO < 10.0 ms) -> **PASS**
  - **Vault Append P95:** 2.4426 ms (SLO < 10.0 ms) -> **PASS**
  - **CLI Cold-Start P95:** 31.04 ms (SLO < 50.0 ms) -> **PASS**
  - **Orchestrator Decision P95:** 0.0046 ms (SLO < 1.0 ms) -> **PASS**
  - **Historical Aggregator Heurístico P95:** 1.9426 ms (SLO < 10.0 ms) -> **PASS**
  - **Historical Aggregator Exhaustivo Exacto P95:** 8.1923 ms (20,000 eventos) -> **PASS**
* **Cierre histórico F5.1:** PASS reportado originalmente; revisado por F5.1.1. La condición anterior de no avanzar fue sustituida por la autorización explícita del Gate F5.2, que aprueba los bindings optativos e implementa la coordinación de sesión.


### 5.7 Gate F5.1.1 — auditoría de cierre

**PASS_WITH_DEVIATIONS**, con [reporte histórico F5.1.1](docs/gates/GATE_F5_1_1_REPORT.md). El reporte F5.1 se conserva como evidencia histórica, no como certificación vigente de seguridad o suspensión. Las observaciones siguientes describen aquel cierre; F5.2 resuelve la autorización nativa y la integración de sesión en 5.8.

- Núcleo Standard Library preservado. `dbus`/`gi` son bindings externos nativos, importados sólo al habilitar el adaptador con `SIEGFRIED_ENABLE_KWIN=1`; la excepción de distribución es una propuesta pendiente de aprobación. No se modificó la restricción de `SPECIFICATION.md`.
- Correcciones mínimas: identidad del propietario KWin, validación de firma/tamaño sin truncado, rechazo de identificadores sospechosos, cola acotada con control de tasa, persistencia separada y liberación de recursos. Regresión corregida de notificación tardía de desconexión durante un reinicio.
- E2E real: kdialog → zenity → kdialog, duraciones 1.31/0.81 s (tolerancia 0.4 s), duplicados inducidos desde KWin, foco nulo, rechazo de suplantación directa del mismo UID, desconexión/reinicio/recarga, desactivación/descarga y privacidad del Vault temporal.
- Regresión: 480 pruebas PASS. Cinco SLOs PASS. Exhaustivo: P95 8.2012 ms en 25 muestras de 20k eventos sintéticos (~27.8 días, caché caliente); no garantiza exactitud universal ni latencia en producción. IPC histórico diario/semanal utiliza `allow_early_exit=False`, cubierto por regresión específica.
- Pendientes: aprobación de excepción nativa; conexión automática con logind/bloqueo y validación real de suspensión, que no se provocó. Recuperación mediante recarga explícita; no se certifica reconexión automática. El mismo UID con autoridad de scripting sigue dentro de la frontera de confianza.
- Estado histórico al cerrar F5.1.1: F5.2 permanecía sin iniciar; su implementación posterior está registrada en 5.8. No se instalaron paquetes, modificó configuración persistente ni ejecutaron commits.


### 5.8 Gate F5.2 — sesión KDE, suspensión y descanso estimado

**Implementado incrementalmente; PASS_WITH_DEVIATIONS.** Informe vigente: [GATE_F5_2_REPORT.md](docs/gates/GATE_F5_2_REPORT.md).

- Excepción de `dbus-python`/PyGObject nativos **autorizada explícitamente** y formalizada en `SPECIFICATION.md`. Imports diferidos, núcleo stdlib, cero pip. Adaptadores `SIEGFRIED_ENABLE_SESSION`/`SIEGFRIED_ENABLE_KWIN` optativos y desactivados por defecto.
- logind: `PrepareForSleep`, `PrepareForShutdown`, `SessionRemoved`, cambios de propiedades de sesión. KDE: `org.freedesktop.ScreenSaver.ActiveChanged` y `GetActive` inicial. Emisores fijados, UID validado, sesión gráfica validada y pérdida de observabilidad conservadora, sin polling.
- Un único `FocusTracker`, barrera de admisión sin espera de disco, generaciones para rechazar foco obsoleto, trabajador de sesión con cola de 32 y tasa de 20/s (ráfaga 40), loop GLib compartido y cierre acotado que informa trabajadores residuales. Activaciones repetidas de la misma app se entregan para recuperar después de desbloqueo; deduplicación permanece en Python.
- Estimador puro: ausencia corroborada ≥90 min, ventana = ausencia −25 min. 8 h → 7 h 35 min. `CLOCK_BOOTTIME` incluye suspensión; `CLOCK_MONOTONIC` no. Cuatro estados internos, sin interpretación biométrica ni inferencia LLM/Cloud.
- Brecha contractual preservada: no se convierte suspensión/bloqueo en `sleep_initiated`/`wake_detected`; no se persisten eventos incompatibles ni se reconstruyen pares entre reinicios sin evidencia. El cálculo puro de `boot_hook.py` exige evidencia explícita y no añade efectos de escritorio.
- Pruebas F5.2: 64 casos, incluidos saturación/memoria, disco bloqueado, apagado concurrente, recuperación de la misma app, ausencia de KDE/logind/bindings y mantenimiento de temporizadores/alertas. Verificación nativa del host: 3 ciclos, 7 suscripciones, ambas fuentes disponibles y recursos liberados. Bus aislado: bloqueo/suspensión sintéticos y rechazo de suplantación/tipos inválidos PASS.
- Regresión final: **544 pruebas PASS en 34.812 s** (480 previas + 64 F5.2), 0 fallos/errores/omitidos. Suite F5.2 también PASS con `python3 -S`. Cinco SLO del harness PASS: P95 Fast-Path 0.0016 ms, Vault 1.6960 ms, CLI 33.09 ms, routing 0.0123 ms e histórico heurístico 1.9635 ms. Histórico exhaustivo: P95 **8.2328 ms**, 25 muestras de 20k eventos, sin garantía universal. `git diff --check` PASS.
- Suspensión, bloqueo, apagado/reinicio físicos quedan **pendientes**; no se efectuaron ni solicitaron. La reconexión tras pérdida de bus requiere reinicio explícito.
- F5.3 permanece **sin implementar**. No se realizaron commits, push, instalaciones ni cambios persistentes en el HOME real.

---

### 5.9 Gate F5.3 — Boot Briefing determinista y Autostart preparado

Implementación incremental del compositor, contexto opcional, clima HTTPS de Lima, presentación nativa KDE con acción y preparación explícita de XDG Autostart. No reconstruye F5.2 ni inicia inferencia.

- Núcleo stdlib; bindings nativos sólo dentro del presentador opcional. Sin paquetes nuevos, servicio extra o cambios en contratos v1.
- Descanso se muestra sólo con `ESTIMATED` coherente y pantalla desbloqueada. El hook independiente no recupera evidencia volátil del daemon ni añade persistencia: briefing sin duración por defecto.
- Agenda no leída por defecto; `--show-task` autoriza exposición del título validado con pantalla desbloqueada. Bloqueo/pérdida de estado retira contenido privado.
- Clima desactivado por defecto: `--weather`, destino Lima fijo HTTPS, sin proxies/redirects/ping, DNS público fijado, UTF-8 y tamaño acotados. Primer saludo antes del worker climático.
- Deduplicación efímera por sesión con flock no bloqueante. Un hook finito, acción one-shot y argv fijo a Konsole; cleanup de hijos de contexto/clima y conexiones.
- Autostart con `OnlyShowIn=KDE;`, `TryExec`, `Hidden`; dry-run por defecto. Copia privada de código por las rutas compartidas del checkout, instalación/desactivación/retirada idempotentes en HOME temporal. Autoinicio real no activado.
- Verificación nativa: KDE recibió la notificación y `InvokeAction` lanzó Konsole con ejecutable inocuo temporal una sola vez. No se abrió una sesión real de Siegfried. Aparición visual/login <2 s pendiente porque el servidor anuncia inhibición; ese ajuste se preservó.
- Certificación, pruebas y métricas finales: [GATE_F5_3_REPORT.md](docs/gates/GATE_F5_3_REPORT.md). Inventario y propuesta de recuperación: [GATE_F5_3_INVENTORY.md](docs/gates/GATE_F5_3_INVENTORY.md). No commits, push, instalaciones de paquetes ni cambios en HOME real.
- **PASS_WITH_DEVIATIONS:** 90 pruebas F5.3 (también sin site-packages), regresión **634 PASS en 34.900 s** y cinco SLO PASS. P95: Fast-Path 0.0016 ms, Vault 1.7600 ms, CLI 34.10 ms, routing 0.0074 ms, histórico heurístico 2.0248 ms; exhaustivo observacional 8.3791 ms. Composición P95 0.000527 ms; cold-start dry-run 115.144380 ms. Entrega KDE real final 37.289 ms, medida parcial: no certifica aparición desde login. `git diff --check` PASS y contratos v1 intactos.

## Cronograma Total Consolidado y Previsión Operativa

* **Fase 0 (Contratos, Esquemas, IPC y Observabilidad):** 6.0 h
* **Fase 1 (Almacenamiento Concurrente y Lockfiles):** 7.0 h
* **Fase 2 (Inferencia Híbrida y Presupuesto de Recursos):** 11.0 h
* **Fase 3 (Demonio, Máquina de Estados y Gestión Quirúrgica):** 12.0 h
* **Fase 4 (REPL y Pre-agregador Histórico):** 9.5 h
* **Fase 5 (Wayland Event-Driven y Ventana de Descanso):** 12.0 h
* **Total Desarrollo Neto:** **57.5 horas.**

### Margen de Integración, Pruebas y Estabilización
Considerando las complejidades intrínsecas de sincronización con Wayland/KWin, gestión de servicios `systemd --user`, eventos de suspend/resume en hardware de laptop, variabilidad de red y supervisión de subprocesos externos (`llama-server`, `pw-cat`), se incorpora un margen operativo del **15 % al 25 %** sobre el desarrollo neto.

* **Margen de Estabilización (+15% a +25%):** ~8.5 h – 14.5 h.
* **Previsión Operativa Total:** **60 a 65 horas.**
