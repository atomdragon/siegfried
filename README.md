# Siegfried — Asistente Personal & Mayordomo Estratégico

> **Mayordomo digital estratégico y centinela de salud con núcleo determinista en Python stdlib y vía cognitiva híbrida (DeepSeek / llama.cpp).**

---

## 1. Visión y Filosofía

- **Identidad:** Siegfried.
- **Trato formal:** *"Señor"* o *"Joven"*.
- **Filosofía Doom:** Latencia mínima (P95 <10 ms Fast-Path), arranque instantáneo (<50 ms CLI), modularidad estricta y control quirúrgico de recursos.
- **Regla Cardinal:** **“El LLM interpreta y recomienda; el núcleo determinista valida, calcula y ejecuta.”**

---

## 2. Estructura del Repositorio de Código vs. Runtime del Usuario

### A. Repositorio de Desarrollo (`Siegfried/`)

```text
Siegfried/
├── bin/                       # Entrypoints ejecutables (siegfried, siegfried-daemon)
├── src/siegfried/             # Paquete modular Python (Zero PyPI runtime dependencies)
│   ├── contracts/             # Event Schema v1, IPC Protocol v1, Config v1, States
│   ├── core/                  # Máquina de estados determinista, Clock, Errores
│   ├── storage/               # Paths centralizados, Vault JSONL, atomic_json con lockfile
│   ├── ipc/                   # Protocolo newline-delimited, servidor selectors y cliente
│   ├── daemon/                # Daemon application y Monotonic timers
│   ├── cli/                   # REPL loop, fast-path router y CLI app
│   ├── inference/             # Placeholders e interfaces cloud (DeepSeek) y local (llama.cpp)
│   ├── integrations/          # Wrappers para PipeWire, notificaciones KDE y KWin
│   └── observability/         # Structured logging y harness de benchmarks
├── schemas/                   # JSON Schemas formales v1
├── scripts/                   # kwin_focus_watcher.js, boot_hook.py
├── systemd/                   # siegfried.service para systemd --user
├── tests/                     # Tests unitarios e integración (Vertical Slice M0)
└── tools/                     # benchmark.py (validación de SLOs)
```

### B. Runtime del Usuario (`~/.siegfried/`)

El estado del usuario se aísla completamente del repositorio de código:

```text
~/.siegfried/
├── config/
│   ├── secrets.env            [chmod 600 - API Keys]
│   ├── core_profile.json      [Perfil permanente y límites]
│   ├── active_agenda.json     [Embudo: 1 tarea crítica, 2 secundarias]
│   └── active_agenda.lock     [Cerrojo fcntl para reemplazo atómico]
├── data/
│   ├── siegfried_vault.jsonl  [Bitácora histórica pura Event Schema v1]
│   └── .history               [Historial del CLI REPL]
├── assets/sounds/
│   └── leaver.ogg             [Alerta auditiva umbral 60 min]
└── logs/
    └── daemon.log
```

Los recursos efímeros se ubican en `/run/user/$UID/`:
- `/run/user/$UID/siegfried.sock` (Socket UNIX)
- `/run/user/$UID/siegfried_llama.pid` (PID custodia)

---

## 3. Ejecución y Desarrollo

### Requisitos

- Linux (Kubuntu / KDE Plasma sobre Wayland recomendado).
- Python 3.10+: núcleo exclusivamente Standard Library, sin dependencias PyPI. Los adaptadores KWin/sesión optativos usan bindings nativos ajenos a stdlib; véase la excepción autorizada del Gate F5.2.

### Ejecutar Pruebas Automatizadas

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
```

### Ejecutar Benchmark Harness de SLOs

```bash
python3 tools/benchmark.py
```

### Iniciar el Daemon en Desarrollo

```bash
./bin/siegfried-daemon
```

### Inicializar y Diagnosticar el Runtime del Usuario

```bash
# Inicialización segura e idempotente del entorno ~/.siegfried/:
./bin/siegfried init

# Diagnóstico no destructivo de configuración, permisos y seguridad:
./bin/siegfried doctor
```

### Usar la CLI Interactiva o Directa

```bash
# Modo interactivo REPL:
./bin/siegfried

# Comandos directos deterministas (Fast-Path):
./bin/siegfried init
./bin/siegfried doctor
./bin/siegfried ping
./bin/siegfried status
./bin/siegfried focus 25 -t "Arquitectura de Software"
./bin/siegfried ack
./bin/siegfried cancel

# Consulta cognitiva híbrida (Cloud / Local):
./bin/siegfried ask "¿Cuál es la mejor estrategia para organizar mis tareas críticas?"
./bin/siegfried ask "¿Qué recomiendas para este bloque?" --policy LOCAL_ONLY
```

### Configuración del Cliente Cloud de Inferencia

Las credenciales de inferencia se configuran en `~/.siegfried/config/secrets.env` con permisos estrictos `0600`:

```bash
# Editar archivo de secretos protegido
echo 'DEEPSEEK_API_KEY="sk-su-clave-aqui"' >> ~/.siegfried/config/secrets.env
chmod 600 ~/.siegfried/config/secrets.env
```

### Motor de Inferencia Local (llama.cpp) y Presupuesto de Recursos

Siegfried incluye supervisión autónoma de `llama-server` para contingencias offline:
- **Presupuesto GPU:** Techo máximo inmutable de **≤3.0 GiB** de VRAM asignada, reservando >5.0 GiB para IDEs y compilación.
- **Reserva de RAM del Sistema:** Mínimo 2.0 GiB garantizados para el sistema operativo; si la RAM libre es insuficiente, el arranque se cancela limpiamente con `InsufficientResourcesError`.
- **Modo CPU-Only:** Soporte nativo y automático si no se detecta GPU dedicada o ante configuraciones de bajo consumo.
- **Idle Eviction:** Si transcurren 15 minutos sin solicitudes activas, el supervisor envía `SIGTERM` (y `SIGKILL` como salvaguarda) exclusivamente a su propio proceso, liberando el 100% de la VRAM.
- **Seguridad de Red:** Enlace estricto a loopback (`127.0.0.1`); rechazo automático de binds públicos y colisiones de puerto.

### Integración Funcional CLI / REPL / Daemon (Gate F2.4) y Hardening de Concurrencia (Gate F2.4.1)

La experiencia de conversación y comando unifica el control determinista y cognitivo:
- **Recorrido A (Fast-Path):** Comandos de alta frecuencia (`status`, `focus 25`, `cancelar`, `ack`) son interceptados por `CommandRouter` y enviados vía IPC directo al demonio. No intervienen LLMs, respondiendo en submilisegundos (P95: 0.0013 ms).
- **Recorrido B (Consulta Cognitiva):** `siegfried ask "<pregunta>"` y preguntas libres en el REPL (`[Siegfried] > ...`) se envían vía comando `QUERY` del protocolo IPC v1. El demonio atiende la consulta mediante `InferenceOrchestrator`, seleccionando Cloud o Local según la política (`CLOUD_PREFERRED`, `LOCAL_PREFERRED`, `LOCAL_ONLY`, `CLOUD_ONLY`).
- **Política de Capacidad y Backpressure (F2.4.1):** Límite estricto de **3 trabajadores cognitivos simultáneos** (`MAX_COGNITIVE_WORKERS = 3`) y **cero cola de espera**. La 4ta consulta se rechaza de inmediato en < 1 ms con código `INFERENCE_BUSY` (*"El motor de inferencia está ocupado. Inténtalo nuevamente."*), evitando saturación de memoria o tormentas de hilos.
- **Reactor Daemon No Bloqueante:** El bucle `selectors` del demonio delega las consultas `QUERY` admitidas a hilos de trabajo asíncronos (`_handle_async_client`), manteniendo los cronómetros `MonotonicTimer` y las solicitudes concurrentes de estado completamente inmunes a latencias de inferencia.
- **Apagado Seguro e Idempotente:** Protocolo `stop()` que detiene nuevas conexiones en el socket de escucha, rechaza admisiones tardías, espera a trabajadores activos de forma acotada (timeout 2.0 s) y reporta con transparencia cualquier trabajador residual.
- **Privacidad y Seguridad:** Sanitización de trazas en respuestas de error al usuario, con reemplazo garantizado de secretos `sk-*` o encabezados `Bearer`.

### Supervisión systemd --user, Recuperación Operativa e Instalación Sudo-Free (Gate F3.1)

El daemon opera como servicio de usuario de Linux con garantías deterministas de fiabilidad y seguridad:
- **Arranque Fail-Closed:** El daemon valida el entorno (`~/.siegfried`) antes de aceptar conexiones. Runtimes no inicializados o configuraciones corruptas impiden el inicio limpiamente con código de salida `78` (`EX_CONFIG`) sin alterar ni crear archivos silenciosamente.
- **Seguridad y Propiedad de Sockets:** El socket UNIX (`/run/user/$UID/siegfried.sock`) se crea con permisos privados `0600` (`umask 0o177`) en un directorio perteneciente exclusivamente al UID del usuario. Se rechazan enlaces simbólicos, archivos no-socket y sockets de otros usuarios. Los sockets obsoletos de procesos caídos se detectan y eliminan de forma segura tras verificar el rechazo de conexión.
- **Supervisión systemd Resiliente:** La unidad `systemd/siegfried.service` aplica `Restart=on-failure`, `RestartSec=2s`, y `RestartPreventExitStatus=78` para detener el bucle de reinicios ante configuraciones erróneas, junto con `TimeoutStopSec=5s` y rate limiting (`StartLimitIntervalSec=30s`, `StartLimitBurst=5`).
- **Instalación Sudo-Free:** El script `tools/install_user_service.py` instala los binarios en `~/.siegfried/bin/` y la unidad en `~/.config/systemd/user/siegfried.service` sin privilegios de root, verificando la sintaxis con `systemd-analyze verify`:
  ```bash
  # Instalación en el HOME del usuario:
  python3 tools/install_user_service.py

  # Habilitación y arranque del servicio de usuario:
  systemctl --user daemon-reload
  systemctl --user enable --now siegfried.service
  ```
- **Inmunidad de Temporizadores:** `MonotonicTimer` utiliza `time.monotonic()`, garantizando conteo exacto ante suspensiones o cambios de hora del reloj de pared.

### Gestión Quirúrgica de Alertas, Notificaciones KDE Plasma y Audio (Gate F3.2)

Siegfried integra un sistema de alertas sensoriales determinista y no bloqueante con supervisión estricta de subprocesos:
- **Alertas Visuales KDE Plasma:** Invocación segura de `notify-send` con argumento sanitizado (redacción de secretos `sk-*` y `Bearer`), sin shell (`shell=False`) y con timeout acotado de 2.0 s. Si la sesión gráfica no está disponible (Wayland/X11 ausente), degrada elegantemente a modo headless registrando la auditoría de entrega sin afectar la operación del daemon.
- **Control Quirúrgico de Audio:** Backend nativo descubierto dinámicamente (`pw-cat`, `pw-play`, `paplay`, `aplay`). Custodia atómica del descriptor `subprocess.Popen` y su PID. Se prohíbe taxativamente `pkill` o `killall`. El comando de interrupción envía `SIGTERM` con 0.5 s de gracia, escala a `SIGKILL` de ser necesario y recolecta el descriptor vía `wait()` para prevenir procesos zombi.
- **Coordinador Asíncrono de Alertas:** El reactor `selectors` no realiza I/O bloqueante hacia el bus de notificaciones; delega eventos a una cola acotada (`maxsize=16`) atendida por un hilo dedicado (`siegfried-alert-worker`). Incluye ventana de deduplicación de 5.0 s para prevenir tormentas de alertas y desalojo de baja prioridad ante saturación.
- **Centinela Determinista de Postura:** Conteo continuo de tiempo sentado durante bloques de enfoque y prórrogas. Genera aviso al minuto 50 (`POSTURE_WARNING`) y activa la barrera dura a los 60 minutos (`POSTURE_LIMIT_REACHED` + `CRITICAL_BREAK_REQUIRED`), momento en que se rechazan prórrogas adicionales hasta registrar un descanso activo con `ACK_BREAK`.

### Endurecimiento de Alertas, Idempotencia y Cierre de Fase 3 (Gate F3.3)

Garantías finales de fiabilidad y concurrencia certificadas para el cierre formal de la Fase 3:
- **Idempotencia Atómica y Sin Falso Marcado:** Comprobación y reserva de `event_id` atómica bajo lock; alertas rechazadas por saturación de cola jamás se marcan como admitidas, permitiendo reintentos legítimos posteriores.
- **Identidad Determinista de Sesión:** `event_id` generado mediante `f"{event.type}:{event.ts:.6f}:{self._event_sequence}"` garantizando no-colisión en runtime preservando inmutable `Event Schema v1`.
- **Caché Acotada en Runtime:** `OrderedDict` FIFO limitado a `MAX_IDEMPOTENT_EVENT_IDS = 1000` (< 100 KB RAM). Transparencia en garantías: deduplicación efímera en memoria; los reinicios del daemon no reproducen alertas pasadas del Vault.
- **Capacidad Crítica y Apagado Verificable:** Cola fija de 16 procesada por 1 trabajador. Desalojo de baja urgencia (`LOW`/`NORMAL`) ante alertas críticas. Rechazo explícito con auditoría `NotificationAttempt` si la cola contiene 16 alertas críticas, protegiendo tanto las alertas críticas existentes como el evento persistido en el Vault. Apagado limpio que drena la cola y recolecta subprocesos sin procesos residuales.
- **Optimización de CLI Cold-Start:** `FastHelpFormatter` en `argparse` elimina importaciones dinámicas lentas de `_colorize` y `shutil` en Python 3.14, manteniendo el cold-start P95 < 50 ms.

### Shell Interactivo REPL, Enrutamiento Determinista y Pre-agregador Histórico (Gate F4.1)

Consolidación de la experiencia conversacional interactiva y agregación analítica de alta velocidad:
- **Consola REPL Fluida y Segura:** Navegación por historial con teclas de dirección vía `readline` persistida en `~/.siegfried/data/.history`. Captura limpia de `Ctrl+D` (cierre elegante), `Ctrl+C` en prompt (retención de sesión sin aborto anómalo) y `Ctrl+C` durante una consulta cognitiva en curso (cancelación inmediata de la espera del cliente sin afectar al demonio).
- **Sondeo Rápido y Notificación No Bloqueante:** Al arrancar el REPL, verificación con timeout de 0.2 s hacia el socket; si el demonio no está activo, se emite una advertencia clara sin bloquear la consola.
- **Silenciado Rápido de Emergencia:** Presionar `Enter` o `Espacio` en el REPL mientras una alarma auditiva o postura crítica está activa (`CRITICAL_BREAK_REQUIRED` o audio reproduciéndose) despacha automáticamente `ACK_BREAK`, silenciando el audio e iniciando el descanso de inmediato.
- **Visualización Formateada del Estado:** El comando `status` presenta un resumen estructurado y legible con estado del sistema, cuenta regresiva de enfoque, tarea activa, tiempo continuo sentado y proximidad a los límites posturales (50m y 60m).
- **Contexto Conversacional Efímero Acotado:** El REPL mantiene en memoria un búfer de hasta `max_context_turns * 2` mensajes (`InferenceMessage`) que se transmiten al demonio en cada turno para dar continuidad conversacional. Este contexto es estrictamente volátil en memoria del proceso CLI; **nunca se escriben transcripciones de chat en `siegfried_vault.jsonl`**, preservando la pureza de la bitácora (`Event Schema v1`).
- **Pre-agregador Histórico Determinista:** `HistoricalAggregator` lee en reversa por bloques de 64 KB. El modo heurístico permite corte temprano con tolerancia limitada al desorden; las consultas IPC diarias/semanales usan explícitamente `allow_early_exit=False` para recorrer exhaustivamente el log. Computa enfoque, bloques, descansos, prórrogas y advertencias. Las latencias observadas de ambos modos se presentan por separado abajo; no son garantías universales.

### Integración Segura del Contexto Histórico en la Inferencia Híbrida (Gate F4.2)

Consolidación de la ruta cognitiva determinista alimentada con telemetría del Vault y garantías de privacidad reforzadas:
- **Detección Determinista de Consultas Históricas:** `CommandRouter` detecta automáticamente preguntas sobre productividad («¿cuántas horas trabajé hoy?», «resumen de esta semana», «cuántos pomodoros llevo») clasificando la ventana temporal (`today` o `week`) mediante expresiones regulares precompiladas sin latencia perceptible.
- **Inyección Determinista de Telemetría sin Alucinación:** El daemon calcula métricas exactas con `HistoricalAggregator` y genera el bloque determinista `<metricas_historicas>`, inyectándolo como mensaje del sistema previo a la consulta del usuario. Ante bóveda vacía o sin eventos, se suministran métricas en cero, evitando invención de números.
- **Privacidad Cloud Deny-by-Default:** Toda consulta que involucre telemetría privada impone forzosamente `InferencePolicy.LOCAL_ONLY`. Si el usuario solicita explícitamente `--policy CLOUD_ONLY`, el daemon rechaza la petición con `IPCResponse.rejected`. Si el motor local falla, se prohíbe taxativamente el fallback a la nube para evitar fugas de telemetría personal.
- **Resiliencia Temporal y Tolerancia a Desorden:** `HistoricalAggregator` incorpora ventana de tolerancia a desorden (`lookback_tolerance_count` y `max_skew_seconds`), permitiendo capturar eventos con pequeños desajustes de reloj NTP o escrituras tardías sin truncar prematuramente la lectura.
- **Defensa contra Inyecciones de Prompt:** En `format_prompt_block()`, los nombres de tareas se sanean con `xml.sax.saxutils.escape`, se eliminan caracteres de control y saltos de línea, se truncan a 80 caracteres, se limitan a las 15 tareas principales (acumulando el resto en «Otras») y se incluye un comentario XML explícito que advierte al modelo que las métricas son de sólo lectura y los nombres de tareas son texto no confiable.
- **Privacidad Estricta en Readline:** `SiegfriedREPL` desactiva el guardado automático de líneas (`set_auto_history(False)`), asegurando que sólo comandos deterministas y comandos de salida se persistan en `~/.siegfried/data/.history` con permisos estrictos `0600`, excluyendo cualquier prompt cognitivo privado.

### Integración Event-Driven con KWin/Wayland, Privacidad y Telemetría de Foco (Gate F5.1)

Integración nativa con KDE Plasma 6 y KWin sobre Wayland para captura de foco sin sobrecarga ni invasión de privacidad:
- **Watcher Event-Driven en KWin (`kwin_focus_watcher.js`):** Enganche nativo a `workspace.windowActivated` en el compositor Wayland. **Cero polling periódico**, cero capturas de pantalla y cero invocaciones lentas en el hilo del compositor.
- **Privacidad Estricta por Diseño:** Prohibición absoluta de capturar o persistir títulos de ventanas (`caption`), URLs, nombres de archivos, comandos de consola o argumentos de procesos. El watcher extrae únicamente identificadores normalizados (`desktopFileName` o `resourceClass`) y los sanea con whitelist estricta (`[a-z0-9_.-]`, máx. 64 caracteres). Aplicaciones no reconocidas o entradas sospechosas se clasifican deterministamente como `desconocida`.
- **Adaptador D-Bus optativo:** Desactivado por defecto. La excepción autorizada en F5.2 permite `python3-dbus`, `python3-gi` y GLib de la distribución; `dbus`, `dbus.service`, `dbus.mainloop.glib` y `gi.repository` **no son stdlib**. No se instalaron paquetes; `SPECIFICATION.md` formaliza la autorización. Se habilita explícitamente con `SIEGFRIED_ENABLE_KWIN=1`. Sin bindings o sin bus/KWin, el núcleo continúa funcionando.
- **Validación del canal:** Emisor igual al nombre único propietario de `org.kde.KWin`, UID local, firma `s`, identificador ASCII de hasta 64 bytes, cola de 64 transiciones y límite de 20/s con ráfaga de 40. Persistencia en trabajador separado. Esto autentica la conexión propietaria; no aísla frente al mismo usuario que pueda cargar scripts en KWin o apropiarse del nombre si KWin está ausente. El límite del payload se aplica después de la recepción nativa, no es un límite del mensaje completo del bus.
- **FocusTracker Determinista con Tiempos Monotónicos:** La duración de cada bloque de ventana activa se calcula exclusivamente con `time.monotonic()`, garantizando valores no negativos y resistencia ante ajustes de reloj NTP.
- **Foco nulo y suspensión:** El foco nulo cierra el intervalo. F5.2 conecta señales de logind y bloqueo KDE mediante el adaptador de sesión optativo; su trabajador cierra el intervalo y bloquea nuevos focos durante suspensión/bloqueo/inactividad o estado desconocido. La suspensión y el bloqueo físicos siguen pendientes de prueba con autorización.
- **Ciclo de vida:** La pérdida de conexión/propietario descarta el intervalo incierto. Recuperar una conexión requiere reinicio explícito; recargar el watcher permite muestrear una ventana que siga activa. Tras desbloqueo/reanudación se espera una nueva señal válida; el watcher entrega también activaciones repetidas de la misma aplicación y Python deduplica dentro de cada generación. Para desactivar, deshabilitar el tracker y detener el adaptador, además de descargar el script; descargar sólo el watcher no comunica un cierre al daemon.
- **Conformidad con Event Schema v1 y Compatibilidad Total:** Los eventos se registran como `window_focus_sampled` en `siegfried_vault.jsonl` sin alterar la integridad de `HistoricalAggregator`, la máquina de estados ni los temporizadores de Pomodoro.

---

## 4. Métricas y Service Level Objectives (SLOs) Verificados

Mediciones finales F5.2, ejecutadas después de la regresión de 544 pruebas. Dataset histórico de 20,000 eventos sintéticos (~27.8 días), consulta de últimas 24 h y caché caliente. El [reporte F5.2](docs/gates/GATE_F5_2_REPORT.md) detalla muestras y límites; no son garantías universales de latencia ni demuestran por sí solas exactitud.

| Componente | Métrica Objetivo | Medición Real (P50) | Medición Real (P95) | Veredicto |
|---|---|---|---|---|
| **Fast-Path Regex Router** | P95 < 10.0 ms | 0.0009 ms | 0.0016 ms | **PASS** |
| **Orchestrator Routing Microbenchmark** | P95 < 1.0 ms | 0.0099 ms | 0.0123 ms | **PASS** |
| **Vault Event Append** (`flock` + `fsync`) | P95 < 10.0 ms | 1.1679 ms | 1.6960 ms | **PASS** |
| **CLI Cold-Start** (`siegfried --help`) | P95 < 50.0 ms | 25.93 ms | 33.09 ms | **PASS** |
| **Historical Aggregator (Modo Heurístico Early-Exit)** | P95 < 10.0 ms (20k eventos) | 1.9261 ms | 1.9635 ms | **PASS** |
| **Historical Aggregator (Modo Exhaustivo)** | Medición observacional, 20k eventos / 25 muestras | 8.1661 ms | 8.2328 ms | Sin SLO propio |

---

## 5. Documentación Arquitectónica

Para consultar los documentos maestros detallados:
- [SPECIFICATION.md](file:///media/okami/Mio/Siegfried/SPECIFICATION.md): Especificación arquitectónica, SLOs, hardware y diseño del sistema.
- [PLAN.md](file:///media/okami/Mio/Siegfried/PLAN.md): Plan de fases (Fase 0 a 5), cronograma y criterios de aceptación.
- [GATE_F2_5_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F2_5_REPORT.md): Informe de auditoría integral y cierre formal de la Fase 2.
- [GATE_F3_1_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_1_REPORT.md): Auditoría integral, consolidación del daemon, supervisión systemd y cierre formal de Gate F3.1.
- [GATE_F3_2_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_2_REPORT.md): Auditoría integral, gestión quirúrgica de alertas, notificaciones KDE Plasma y audio (Gate F3.2).
- [GATE_F3_3_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_3_REPORT.md): Auditoría integral, endurecimiento de alertas, idempotencia y cierre formal de Fase 3 (Gate F3.3).
- [GATE_F4_1_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F4_1_REPORT.md): Auditoría integral, consolidación del REPL interactivo, enrutamiento determinista y pre-agregador histórico (Gate F4.1).
- [GATE_F4_2_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F4_2_REPORT.md): Auditoría integral, integración segura del contexto histórico determinista e inferencia híbrida (Gate F4.2).
- [GATE_F5_1_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F5_1_REPORT.md): Auditoría integral, integración event-driven con KWin/Wayland, privacidad y telemetría de foco (Gate F5.1).



### Gate F5.1.1 — auditoría final

**PASS_WITH_DEVIATIONS**: 480 pruebas PASS y E2E real KWin/Wayland con dos aplicaciones temporales. Estado histórico antes de F5.2: núcleo stdlib preservado, excepción nativa y conexión de sesión aún pendientes en ese momento. F5.2 autoriza e implementa esos puntos; la prueba física sigue pendiente. Informe histórico: [GATE_F5_1_1_REPORT.md](docs/gates/GATE_F5_1_1_REPORT.md), que corrige las garantías excesivas del reporte F5.1 anterior.

Prueba E2E reproducible, únicamente con autorización de acceso a la sesión: `python3 tools/verify_kwin_f511.py`. Abre dos diálogos de prueba, usa un Vault temporal y descarga sus scripts al terminar. No suspende el equipo.


### Gate F5.2 — sesión KDE y descanso estimado

El adaptador de sesión es opcional y permanece **desactivado por defecto**. Con `SIEGFRIED_ENABLE_SESSION=1` recibe señales de suspensión/reanudación/apagado de logind y bloqueo de KDE; el foco usa además `SIEGFRIED_ENABLE_KWIN=1`. No se instalaron ni activaron servicios, paquetes o scripts en el HOME real. Sin bindings, KDE, logind o bus, daemon, CLI, Vault, temporizadores y alertas permanecen operativos. Con observabilidad de sesión incompleta, la telemetría de foco se conserva cerrada y no se infiere actividad.

La ausencia debe estar corroborada y alcanzar **90 minutos**. El cálculo puro resta **25 minutos**: ausencia de 8 horas → ventana estimada de **7 h 35 min**. Esto no confirma sueño, biometría ni calidad de descanso. `SessionController.rest_estimate` expone los estados internos `ESTIMATED`, `INSUFFICIENT_DATA`, `NOT_APPLICABLE` e `INVALID_INTERVAL`; ninguna estimación se envía al LLM ni a Cloud. Suspensión se mide con `CLOCK_BOOTTIME`, no sólo con `time.monotonic()`. Entre arranques se requiere validación independiente del intervalo de pared, que el daemon no inventa ni reconstruye.

No se añadieron eventos ni campos a contratos congelados. Las estimaciones de suspensión se mantienen en memoria: perder el daemon o el bus invalida la continuidad. Bloquear pantalla o retirar la sesión no constituye una observación de sueño. `scripts/boot_hook.py` delega el cálculo puro al estimador; no se implementa Boot Briefing F5.3.

Pruebas específicas (sin suspensión ni bloqueo reales):

```bash
PYTHONPATH=src timeout -s INT -k 5s 60s python3 -X faulthandler -m unittest tests.integration.test_session_rest_f52 -v
```

Verificación operativa explícita: `PYTHONPATH=src timeout -s INT -k 5s 60s python3 -X faulthandler tools/verify_session_f52.py`. Lee el estado y suscribe/cancela señales del host; después genera señales sintéticas en un bus D-Bus aislado y un Vault temporal. No bloquea ni suspende el host.

Informe y límites: [GATE_F5_2_REPORT.md](docs/gates/GATE_F5_2_REPORT.md). Veredicto **PASS_WITH_DEVIATIONS**: implementación y pruebas permitidas completas; suspensión, bloqueo y reinicio físicos pendientes. No se implementó F5.3.

Certificación final: **64 pruebas F5.2**, también con `python3 -S`; **544 pruebas** de regresión en 34.812 s, cinco SLO del harness PASS y `git diff --check` PASS. Tres ciclos de suscripción/cancelación nativa en el host y transiciones sintéticas en bus aislado liberaron conexiones, hilos y el proceso hijo de prueba.
