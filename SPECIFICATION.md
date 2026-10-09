# Siegfried — Asistente Personal & Mayordomo Estratégico
> *Documento Maestro de Especificación de Arquitectura, Filosofía y Diseño del Sistema (Versión Consolidada y Endurecida)*

---

## 1. Visión, Filosofía y Rol Operativo

* **Identidad:** Siegfried.
* **Arquetipo:** Mayordomo digital de alta lealtad, sobrio, formal y estratégico (inspirado en personalidades como Jarvis y Alfred Pennyworth).
* **Trato formal:** Se dirige al usuario invariablemente como *"Señor"* o *"Joven"*.
* **Propósito primordial:** Maximizar el rendimiento cognitivo y avance de proyectos del usuario mediante disciplina estructurada, **blindando de forma innegociable su salud física y psicológica**.
* **Postura emocional:**
  * **Cuidado firme sobre imposición ciega:** No penaliza con hostilidad ni genera culpa destructiva; previene activamente el agotamiento crónico (*burnout*). Si el usuario decide trasnochar o sobrecargarse, interviene con elegancia y advertencias sanitarias claras, permaneciendo disponible a su lado sin bloquear el sistema.
  * **Antídoto contra la baja autoestima:** Registra con frialdad objetiva las horas invertidas y los logros alcanzados. Cuando el usuario se desestima o sufre bloqueo, Siegfried refuta sus dudas con métricas reales e históricas de su progreso.
* **Filosofía de ingeniería y frontera determinista/LLM:**
  * **Estilo Doom:** Latencia de respuesta mínima, arranque instantáneo, modularidad estricta y uso quirúrgico de los recursos de hardware.
  * **Regla Arquitectónica Cardinal:** **“El LLM interpreta y recomienda; el núcleo determinista valida, calcula y ejecuta.”** Timers, métricas, transiciones de estado, agenda, límites biológicos y agregaciones históricas jamás dependen de la completitud o acierto de una respuesta del modelo.

---

## 2. Entorno Operativo, Stack Técnico y Hardware

| Parámetro | Especificación / Restricción |
| :--- | :--- |
| **Sistema Operativo** | Kubuntu Linux (KDE Plasma sobre Wayland). |
| **Stack de Desarrollo** | **Runtime Python sin dependencias PyPI;** el núcleo usa **Python Standard Library** (`socket`, `json`, `urllib`, `re`, `subprocess`, `pathlib`, `fcntl`, `time`, `selectors`, etc.) y se integra con componentes nativos de Linux/KDE como systemd, KWin, DBus, PipeWire y llama.cpp. *(Roadmap: Portabilidad a C# Native AOT si se requiere binario único autocontenido)*. |
| **Hardware de Referencia** | Intel Core i9-14900HX (24 núcleos), 64 GB RAM DDR5, NVIDIA RTX 4070 Laptop (8 GB VRAM). |
| **Presupuesto de VRAM** | **Presupuesto dinámico de recursos:** Mantener el proceso local de inferencia estrictamente dentro del presupuesto de VRAM de **≤3.0 GiB**, preservando >5.0 GB libres para IDEs, compilación y renderizado. El número de capas GPU, tamaño de contexto (`n_ctx`) y parámetros se ajustan dinámicamente según el hardware sin asumirse como garantías fijas. |
| **Presupuesto Cloud** | $6.00 USD en la API de DeepSeek como cerebro analítico primario. |
| **Gestión de Secretos** | Credenciales y API Keys en `~/.siegfried/config/secrets.env` con permisos UNIX `chmod 600`. |

### Excepción autorizada de integración nativa opcional — Gate F5.2

El núcleo, CLI, temporizadores, Vault, alertas y contratos de dominio conservan Python Standard Library y no requieren paquetes PyPI. Se autoriza **exclusivamente para adaptadores opcionales** el uso de `dbus-python` y PyGObject/`gi.repository`, suministrados por la distribución Linux (`python3-dbus`, `python3-gi`, GLib y sus datos de introspección). `dbus`, `dbus.service`, `dbus.mainloop.glib` y `gi.repository` **no son stdlib**. Los bindings se importan de forma diferida al iniciar el adaptador; su ausencia o la falta de un bus/origen nativo produce degradación controlada y no impide ejecutar el núcleo.

No se instalan paquetes con pip ni se crean servicios privilegiados. Los adaptadores permanecen desactivados por defecto: `SIEGFRIED_ENABLE_KWIN=1` habilita foco y `SIEGFRIED_ENABLE_SESSION=1` habilita señales de logind/KDE. Ambas opciones pertenecen al entorno del proceso, no modifican Config Schema v1 y no se activan automáticamente en el sistema del usuario. Para protección de foco frente a bloqueo/suspensión se requieren ambos adaptadores y observabilidad suficiente; estado desconocido o pérdida de señales cierra la admisión de foco.

### Objetivos de Nivel de Servicio (SLO)

En sustitución de garantías teóricas rígidas, el sistema se rige por los siguientes SLO que se auditan y validan mediante pruebas de rendimiento continuas:

* **Fast-Path Regex:** Latencia P95 `<10 ms`.
* **CLI Cold-Start:** Tiempo de inicio P95 `<50 ms`.
* **Pre-agregador Histórico:** Latencia P95 `<10 ms` para bitácoras de hasta 20,000 eventos.
* **Consumo Daemon:** CPU en reposo `<0.2 %` y RSS objetivo `<30 MB`.
* **Proceso de Inferencia Local (`llama.cpp`):** Asignación de VRAM `≤3.0 GiB`.

---

## 3. Arquitectura del Sistema: Daemon y CLI Interactivo (REPL)

```
[KDE Plasma / Wayland] ◄─── Notificaciones DBus ───┐
                                                    │
┌──────────────────────────┐             ┌─────────────────────────┐
│     siegfried (CLI)      │             │    siegfried-daemon     │
│ (Modo REPL Interactivo)  │             │  (Servicio systemd)     │
│                          │             │                         │
│ • Prompt [Siegfried] >   │──IPC Socket►│ • Timers Pomodoro       │
│ • Enrutador Rápido/Lento │◄──Unix Resp─│ • Monitor de asiento 60m│
│ • Lectura en Stream      │             │ • Event-driven KWin JS  │
└──────────────────────────┘             └─────────────────────────┘
             │                                        │
             ▼                                        ▼
   [Motor de Inferencia]                   [siegfried_vault.jsonl]
   (DeepSeek / llama.cpp)                    (Event Schema v1 / JSONL)
```

1. **`siegfried-daemon` (Servicio de Fondo):**
   * Gestionado por `systemd --user` (`siegfried.service`).
   * Consumo en reposo: RSS `<30 MB`, CPU `<0.2 %`.
   * Monitorea el tiempo continuo de asiento (umbral duro de 60 min), gestiona la máquina de estados de salud (`IDLE`, `POMODORO_RUNNING`, `POSTPONE_RUNNING`, `BREAK_RUNNING`, `CRITICAL_BREAK_REQUIRED`), cronómetros con `time.monotonic()` y recibe eventos de foco de KWin.
   * Comunicación IPC tipada (`IPC Protocol v1`) vía Socket Unix local (`/run/user/$UID/siegfried.sock`).
2. **`siegfried` (CLI de Usuario):**
   * **Modo Exclusivo REPL (Sesión Interactiva):** Se ejecuta escribiendo `siegfried` en consola o mediante la acción de las notificaciones KDE. Despliega un prompt continuo estilizado (`[Siegfried] > `) hasta que el usuario ingrese `salir`, `exit` o `Ctrl+D`. Cold-start P95 `<50 ms`.

---

## 4. Motor de Inferencia Híbrido y Ciclo de Vida Local

### A. Política de Conexión y Fallback con Deadline Estricto
1. **Timeouts Individuales y Deadline Absoluto:** Queda prohibido el uso de `socket.setdefaulttimeout()` global para evitar efectos secundarios en sockets concurrentes o IPC. Cada petición HTTP configura su timeout individual (3.0 s por intento) y el ciclo de fallback calcula un deadline estricto acumulado de 10.0 segundos medido mediante `time.monotonic()`.
2. **Buffer de Gracia de 10 Segundos:** Si hay error de red/socket, se reintenta respetando el deadline de `time.monotonic()`. Al consumirse la ventana de 10 segundos acumulados sin respuesta, se activa el modo offline.
3. **Lazy Loading de `llama-server`:** El daemon levanta el binario local `llama-server` **únicamente ante el estado offline confirmado**. Los parámetros de lanzamiento ajustan capas GPU (`--n-gpu-layers`), threads y contexto para cumplir el presupuesto de VRAM de `≤3.0 GiB`.
4. **Liberación de VRAM (Idle Timeout de 15 min):** Si transcurren 15 minutos sin peticiones offline, el daemon envía `SIGTERM` (y `SIGKILL` como salvaguarda) al PID exacto del proceso hijo para liberar el 100% de la VRAM.

### B. Enrutador de Dos Vías (Fast-Path vs. Deep-Path)
* **Vía Rápida Local (Fast-Path P95 <10 ms):** Reconoce intenciones directas por regex en lenguaje natural (ej. *"iniciar bloque de 50"*, *"terminé tarea"*, *"status"*). Ejecuta la acción en el daemon vía socket y responde de inmediato sin invocar al LLM.
* **Vía Cognitiva (LLM):** Enrutamiento a DeepSeek (o Qwen local si está offline) para ingesta de tareas complejas, evaluación de productividad, desahogo y el boot briefing matutino. Todo output del LLM es meramente interpretativo o propositivo; la mutación de estado reside en el núcleo determinista.

---

## 5. Persistencia, Concurrencia y Esquemas de Datos

```
~/.siegfried/
├── config/
│   ├── secrets.env            [Permisos chmod 600 - API Keys]
│   ├── core_profile.json      [CAPA FRÍA - Datos inmutables / Config Schema v1]
│   ├── active_agenda.json     [CAPA TIBIA - 1 tarea crítica y 2 secundarias]
│   └── active_agenda.lock     [Archivo de cerrojo fcntl para reemplazo atómico]
└── data/
    └── siegfried_vault.jsonl  [CAPA CALIENTE - Bitácora Append-Only Event Schema v1]
```

### A. Estrategia de Bloqueo Concurrente
* **Archivos JSON Reemplazables (`active_agenda.json`, etc.):** Para evitar la inconsistencia entre el reemplazo atómico de inodos (`os.replace`) y el bloqueo POSIX (`fcntl.flock`), los JSON mutables utilizan un archivo cerrojo independiente (ej. `active_agenda.lock`).
  * **Flujo seguro:** `abrir(lock) → flock(lock, LOCK_EX) → escribir .tmp → f.flush() → os.fsync() → os.replace(.tmp, final) → unlock(lock)`.
* **JSONL Append-Only (`siegfried_vault.jsonl`):** Mantiene bloqueo exclusivo directo en modo append (`a`), asegurando escritura continua segura con `flush()` y `os.fsync()` antes de liberar.

### B. Event Schema v1 (JSONL Puro Versionado)
Se elimina cualquier encabezado de metadatos no-JSON en `siegfried_vault.jsonl`. **Cada línea del archivo es un objeto JSON estricto y parseable** que implementa `Event Schema v1`:
```json
{
  "v": 1,
  "ts": 1730000000.123,
  "type": "pomodoro_completed",
  "data": {
    "duration_min": 50,
    "course": "Arquitectura de Software"
  }
}
```
Esto garantiza compatibilidad total con herramientas UNIX estándar (`jq`, `grep`, `wc -l`) y migraciones automáticas basadas en el campo `"v"`.

### C. Pre-agregador Histórico
Lectura en reversa con `f.seek()` por bloques desde el final del archivo. Filtra por rangos temporales e inyecta métricas numéricas precalculadas en el contexto del LLM (SLO P95 `<10 ms` para 20k eventos).

---

## 6. Centinela Biológico, Hábitos y Alertas Sensoriales

### A. Vigilancia de Descanso y Ritmo Circadiano
* **Ventana de Descanso Estimada (`ventana_descanso_estimada`):** Se reconoce explícitamente que suspender o apagar el equipo no demuestra con certeza biométrica el inicio del sueño. Por tanto, el cálculo:
  $$\text{ventana\_descanso\_estimada} = (\text{Hora Actual} - \text{Hora Apagado/Suspensión}) - 25\text{ min}$$
  se modela formalmente como una estimación heurística útil, no como descanso fáctico inmutable. Sólo se aplica a ausencias corroboradas de al menos 90 minutos. F5.2 utiliza tipos internos `ESTIMATED`, `INSUFFICIENT_DATA`, `NOT_APPLICABLE` e `INVALID_INTERVAL`; no persiste nuevas estimaciones porque el catálogo congelado no define un evento compatible para suspensión/ausencia. `sleep_initiated` no se reutiliza para un bloqueo o una suspensión; no se redefine `wake_detected` como reanudación. La brecha de persistencia queda documentada sin modificar Event Schema v1.
* **Comando Manual en Lenguaje Natural:** Registro explícito de retiro sin apagar el equipo (*"Siegfried, me voy a dormir ya"*), emitiendo el evento `sleep_initiated` y silenciando alarmas.

### B. Protocolo Pomodoro y Límite Postural de 60 Minutos
* **Bloques:** Modalidad Profunda (50 min trabajo / 10 min pausa) y Modalidad Ágil (25 min / 5 min).
* **Prórroga Consciente:** Permite hasta 10 minutos de extensión en estado de flow (`posponer 10`).
* **Barrera Dura de los 60 Minutos:** Al cumplirse 60 minutos continuos sentado, **las prórrogas quedan bloqueadas por el daemon**.
* **Alarma Sensorial y Gestión Quirúrgica de Procesos:**
  * Al llegar al límite de 60 minutos, el daemon dispara una notificación en KDE y reproduce asíncronamente el sonido de alerta (`pw-cat -p ~/.siegfried/assets/sounds/leaver.ogg` con fallback a `paplay`).
  * **Cero comandos globales `pkill`:** `notifier.py` almacena y custodia el `Popen` / PID exacto del subproceso reproductor iniciado por Siegfried.
  * **Silenciado Rápido:** Presionar `Espacio` o `Enter` en el REPL, o el botón de acción en KDE, finaliza únicamente ese proceso hijo con `terminate()` (y `kill()` si no responde en 500 ms) y emite el evento de inicio de pausa activa.

---

## 7. Integración con el Escritorio (Kubuntu / KDE Plasma)

* **Arquitectura Event-Driven de Ventanas (`kwin_focus_watcher.js`):** En lugar de polling periódico ciego que consume ciclos y pierde transiciones breves, un script KWin ligero basado en eventos se suscribe a los cambios de foco de ventanas en Wayland y notifica al daemon mediante DBus/IPC.
* **Privacidad y Registro Sanitizado:** Para proteger la privacidad del usuario, el sistema registra por defecto únicamente `aplicacion`, `categoria` y `duracion`. No se persisten títulos completos de ventanas, pestañas web privadas ni datos sensibles.
* **Sesión KDE/logind (F5.2):** Señales `PrepareForSleep`, `PrepareForShutdown`, `SessionRemoved`, `PropertiesChanged` de la sesión validada (`Active`, `LockedHint`, `State`) y `org.freedesktop.ScreenSaver.ActiveChanged`. Suscripción por eventos, sin polling. Los callbacks validan emisor/tipo, cierran rápidamente la admisión y encolan; un trabajador acotado coordina el único `FocusTracker`. Desbloqueo/reanudación espera nuevo foco válido, sin intervalos anteriores. El daemon comparte un único loop GLib entre adaptadores y no altera la máquina de estados Pomodoro/postura por estas señales.
* **Relojes de descanso:** `CLOCK_MONOTONIC` mide ejecución activa y excluye suspensión en Linux; `CLOCK_BOOTTIME` incluye suspensión y sólo se compara dentro del mismo arranque. Entre arranques se requieren endpoints de pared independientemente validados; sin esa evidencia se devuelve `INSUFFICIENT_DATA`. Desacuerdo de reloj superior a 5 s o cronología invertida produce `INVALID_INTERVAL`. Reiniciar el daemon no reconstruye ausencias a partir de ausencia de señales.
* **Boot Hook Matutino:** Script de sesión que despliega un briefing inicial matutino con botón interactivo **[Abrir Sesión]** para abrir el REPL de Siegfried en Konsole.

---

## 8. Calibración Dinámica del Tono

* **Modo Ejecutivo (1-2 oraciones):** Se activa automáticamente ante frases cortas (<7 palabras), modo imperativo directo o Pomodoros activos.
* **Modo Mayordomo Completo (Solemnidad y cuidado):** Se activa ante entradas largas (>25 palabras), lenguaje de cansancio/frustración, o durante el briefing matutino y balance nocturno.

---

## 9. Estructura Completa de Directorios del Proyecto

```
~/.siegfried/
├── bin/
│   ├── siegfried              # CLI interactivo REPL (Python stdlib sin PyPI)
│   └── siegfried-daemon       # Demonio de monitoreo en segundo plano
├── config/
│   ├── secrets.env            # Variables protegidas (DeepSeek API Key, chmod 600)
│   ├── core_profile.json      # Perfil permanente y restricciones de salud
│   ├── active_agenda.json     # Tareas activas (1 crítica, 2 secundarias)
│   └── active_agenda.lock     # Cerrojo para reemplazo atómico concurrente
├── data/
│   └── siegfried_vault.jsonl  # Bitácora histórica (JSONL puro, Event Schema v1)
├── assets/
│   └── sounds/
│       └── leaver.ogg         # Alerta sonora para el umbral de 60 min
├── systemd/
│   └── siegfried.service      # Servicio de usuario systemd (--user)
└── scripts/
    └── kwin_focus_watcher.js  # Script KWin event-driven para foco en Wayland
```

---

## 10. Plan Maestro de Implementación

El desglose completo del desarrollo en 6 fases estructuradas (Fases 0 a 5), cronograma de **51.5 h netas** con margen operativo de integración y estabilización del 15–25% (**60–65 horas totales**), análisis de riesgos y criterios de aceptación se encuentra registrado en [PLAN.md](file:///media/okami/Mio/Siegfried/PLAN.md).

* **Fase 0:** Contratos, Esquemas, Protocolos de Comunicación y Observabilidad (`Event Schema v1`, `IPC Protocol v1`, `Config Schema v1`, SLOs y benchmarks).
* **Fase 1:** Cimientos del Sistema de Archivos, Persistencia Atómica Concurrente con Lockfiles (`storage.py`, cerrojos de lockfile, JSONL puro versionado).
* **Fase 2:** Motor de Inferencia Híbrido, Presupuesto de Recursos y Fallback con Deadlines (`inference.py`, `llama_manager.py`, presupuesto VRAM, timeouts por socket).
* **Fase 3:** Demonio en Segundo Plano, Máquina de Estados Determinista y Gestión Quirúrgica de Procesos (`daemon.py`, `notifier.py` con custodia de PID, systemd).
* **Fase 4:** Interfaz REPL Interactiva y Pre-agregador Histórico Optimizado (`repl.py`, `aggregator.py`).
* **Fase 5:** Integración Event-Driven con Wayland, KWin Focus Sanitizado y Heurística de Descanso (`kwin_focus_watcher.js`, `boot_hook.py`, `ventana_descanso_estimada`).
