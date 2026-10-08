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

# Comandos directos:
./bin/siegfried init
./bin/siegfried doctor
./bin/siegfried ping
./bin/siegfried status
./bin/siegfried focus 25 -t "Arquitectura de Software"
./bin/siegfried ack
./bin/siegfried cancel
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

### Orquestador Híbrido, Routing Determinista y Fallback Seguro (Gate F2.3)

El subsistema de inferencia se coordina mediante `InferenceOrchestrator` (`siegfried.inference.orchestrator`):
- **Políticas de Selección:**
  - `CLOUD_PREFERRED`: Intenta Cloud (DeepSeek); ante fallos recuperables y margen de deadline disponible, realiza fallback transparente a Local.
  - `LOCAL_PREFERRED`: Intenta Local (`llama-server`); ante fallos locales o falta de recursos, realiza fallback seguro a Cloud.
  - `LOCAL_ONLY`: Confinamiento estricto en la máquina local. Cero tráfico externo bajo cualquier circunstancia; si Local falla, la operación concluye con error controlado sin fuga de datos.
  - `CLOUD_ONLY`: Ejecución exclusiva en Cloud sin activar el gestor local.
- **Presupuesto Temporal Global:** Deadline monotónico inmutable (10.0s) evaluado en la entrada y compartido de forma no reiniciable entre el intento primario y el fallback.
- **Límite de Reintentos:** Máximo estricto de 1 intento primario y 1 fallback; previene tormentas de reintentos en fallas de red.
- **Aislamiento del Fast-Path:** Comandos deterministas (`iniciar bloque de 50`, `status`, `cancelar`, `ack`) se resuelven en submilisegundos por regex (`CommandRouter`) sin invocar ni bloquear sobre modelos de lenguaje.

---

## 4. Métricas y Service Level Objectives (SLOs) Verificados

| Componente | Métrica Objetivo | Medición Real (P50) | Medición Real (P95) | Veredicto |
|---|---|---|---|---|
| **Fast-Path Regex Router** | P95 < 10.0 ms | 0.0009 ms | 0.0013 ms | **PASS** |
| **Orchestrator Routing Microbenchmark** | P95 < 1.0 ms | 0.0045 ms | 0.0050 ms | **PASS** |
| **Vault Event Append** (`flock` + `fsync`) | P95 < 5.0 ms | 1.0282 ms | 2.2407 ms | **PASS** |
| **CLI Cold-Start** (`siegfried --help`) | P95 < 50.0 ms | 37.15 ms | 44.91 ms | **PASS** |

---

## 5. Documentación Arquitectónica

Para consultar los documentos maestros detallados:
- [SPECIFICATION.md](file:///media/okami/Mio/Siegfried/SPECIFICATION.md): Especificación arquitectónica, SLOs, hardware y diseño del sistema.
- [PLAN.md](file:///media/okami/Mio/Siegfried/PLAN.md): Plan de fases (Fase 0 a 5), cronograma y criterios de aceptación.

