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
* **Arranque sin red:** Ping de 0.5 s a `1.1.1.1`. Si no hay red, emitir saludo determinista precalculado en <100 ms.
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

---

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
