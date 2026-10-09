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
- Python 3.10+ (**exclusivamente Standard Library**; cero dependencias `pip` en runtime).

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

---

## 4. Métricas y Service Level Objectives (SLOs) Verificados

| Componente | Métrica Objetivo | Medición Real (P50) | Medición Real (P95) | Veredicto |
|---|---|---|---|---|
| **Fast-Path Regex Router** | P95 < 10.0 ms | 0.0024 ms | 0.0034 ms | **PASS** |
| **Orchestrator Routing Microbenchmark** | P95 < 1.0 ms | 0.0086 ms | 0.0098 ms | **PASS** |
| **Vault Event Append** (`flock` + `fsync`) | P95 < 10.0 ms | 1.3485 ms | 2.5492 ms | **PASS** |
| **CLI Cold-Start** (`siegfried --help`) | P95 < 50.0 ms | 37.77 ms | 47.25 ms | **PASS** |

---

## 5. Documentación Arquitectónica

Para consultar los documentos maestros detallados:
- [SPECIFICATION.md](file:///media/okami/Mio/Siegfried/SPECIFICATION.md): Especificación arquitectónica, SLOs, hardware y diseño del sistema.
- [PLAN.md](file:///media/okami/Mio/Siegfried/PLAN.md): Plan de fases (Fase 0 a 5), cronograma y criterios de aceptación.
- [GATE_F2_5_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F2_5_REPORT.md): Informe de auditoría integral y cierre formal de la Fase 2.
- [GATE_F3_1_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_1_REPORT.md): Auditoría integral, consolidación del daemon, supervisión systemd y cierre formal de Gate F3.1.
- [GATE_F3_2_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_2_REPORT.md): Auditoría integral, gestión quirúrgica de alertas, notificaciones KDE Plasma y audio (Gate F3.2).
- [GATE_F3_3_REPORT.md](file:///media/okami/Mio/Siegfried/docs/gates/GATE_F3_3_REPORT.md): Auditoría integral, endurecimiento de alertas, idempotencia y cierre formal de Fase 3 (Gate F3.3).

