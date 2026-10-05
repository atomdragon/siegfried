# Siegfried — Asistente Personal & Mayordomo Estratégico
> *Documento Maestro de Especificación de Arquitectura, Filosofía y Diseño del Sistema (Versión Consolidada)*

---

## 1. Visión, Filosofía y Rol Operativo

* **Identidad:** Siegfried.
* **Arquetipo:** Mayordomo digital de alta lealtad, sobrio, formal y estratégico (inspirado en personalidades como Jarvis y Alfred Pennyworth).
* **Trato formal:** Se dirige al usuario invariablemente como *"Señor"* o *"Joven"*.
* **Propósito primordial:** Maximizar el rendimiento cognitivo y avance de proyectos del usuario mediante disciplina estructurada, **blindando de forma innegociable su salud física y psicológica**.
* **Postura emocional:**
  * **Cuidado firme sobre imposición ciega:** No penaliza con hostilidad ni genera culpa destructiva; previene activamente el agotamiento crónico (*burnout*). Si el usuario decide trasnochar o sobrecargarse, interviene con elegancia y advertencias sanitarias claras, permaneciendo disponible a su lado sin bloquear el sistema.
  * **Antídoto contra la baja autoestima:** Registra con frialdad objetiva las horas invertidas y los logros alcanzados. Cuando el usuario se desestima o sufre bloqueo, Siegfried refuta sus dudas con métricas reales e históricas de su progreso.
* **Filosofía de ingeniería:** **Estilo Doom** — Latencia de respuesta mínima (<25 ms en operaciones locales), arranque instantáneo, modularidad estricta y uso quirúrgico de los recursos de hardware.

---

## 2. Entorno Operativo, Stack Técnico y Hardware

| Parámetro | Especificación / Restricción |
| :--- | :--- |
| **Sistema Operativo** | Kubuntu Linux (KDE Plasma sobre Wayland) |
| **Stack de Desarrollo** | **Python 3 usando exclusivamente la Librería Estándar** (`socket`, `json`, `urllib`, `re`, `subprocess`, `pathlib`, `fcntl`, `time`). Cero dependencias externas para garantizar arranque en <25 ms. *(Roadmap: Portabilidad a C# Native AOT en Fase 2 si se requiere binario único autocontenido)*. |
| **Hardware** | Intel Core i9-14900HX (24 núcleos), 64 GB RAM DDR5, NVIDIA RTX 4070 Laptop (8 GB VRAM). |
| **Presupuesto de VRAM** | **Tope estricto de 2.5 GB a 3.0 GB.** Preservar siempre más de 5.0 GB de VRAM libres para IDEs, compilación y renderizado de escritorio. |
| **Presupuesto Cloud** | $6.00 USD en la API de DeepSeek como cerebro analítico primario. |
| **Gestión de Secretos** | Credenciales y API Keys en `~/.siegfried/config/secrets.env` con permisos UNIX `chmod 600`. |

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
│ • Lectura en Stream      │             │ • Listener KWin DBus    │
└──────────────────────────┘             └─────────────────────────┘
             │                                        │
             ▼                                        ▼
   [Motor de Inferencia]                   [siegfried_vault.jsonl]
   (DeepSeek / llama.cpp)                    (Bloqueo atómico fcntl)
```

1. **`siegfried-daemon` (Servicio de Fondo):**
   * Gestionado por `systemd --user` (`siegfried.service`).
   * Consumo en reposo: <15 MB RAM, 0% CPU.
   * Monitorea el tiempo continuo de asiento (umbral duro de 60 min), gestiona temporizadores de Pomodoro y escucha cambios de ventana activa en KDE Plasma vía DBus KWin.
   * Comunicación vía Socket Unix local (`/run/user/$UID/siegfried.sock`).
2. **`siegfried` (CLI de Usuario):**
   * **Modo Exclusivo REPL (Sesión Interactiva):** Se ejecuta escribiendo `siegfried` en consola o mediante el botón de acción de las notificaciones KDE. Despliega un prompt continuo estilizado (`[Siegfried] > `) hasta que el usuario ingrese `salir`, `exit` o `Ctrl+D`.

---

## 4. Motor de Inferencia Híbrido y Ciclo de Vida Local

### A. Política de Conexión y Fallback Offline
1. **Intento Primario:** Consulta a la API de DeepSeek vía llamada directa HTTPS (timeout: 3.0 s).
2. **Buffer de Gracia de 10 Segundos:** Si la conexión falla por error de red/socket, el sistema espera 3 segundos y reintenta. Si al cumplirse **10 segundos acumulados** la red sigue inaccesible, se declara estado Offline genuino.
3. **Lazy Loading de `llama-server`:** El daemon levanta el binario local `llama-server` (Qwen 2.5 7B Q4_K_M con 16 capas offloaded a la RTX 4070) **únicamente cuando se confirma el estado offline**.
4. **Liberación de VRAM (Idle Timeout de 15 min):** Si transcurren 15 minutos sin peticiones offline, el daemon finaliza el proceso `llama-server`, devolviendo el 100% de la VRAM al sistema.

### B. Enrutador de Dos Vías (Fast-Path vs. Deep-Path)
* **Vía Rápida Local (<2 ms):** Reconoce intenciones directas por regex en lenguaje natural (ej. *"iniciar bloque de 50"*, *"terminé tarea"*, *"status"*). Ejecuta la acción en el daemon vía socket y responde sin consultar al LLM.
* **Vía Cognitiva (LLM):** Enrutamiento a DeepSeek (o Qwen local si está offline) para ingesta de tareas complejas, evaluación de productividad, desahogo y el boot briefing matutino.

---

## 5. Persistencia, Concurrencia y Memoria Escalonada

```
~/.siegfried/
├── config/
│   ├── secrets.env            [Permisos chmod 600 - API Keys]
│   ├── core_profile.json      [CAPA FRÍA - Datos inmutables (~150 tokens)]
│   └── active_agenda.json     [CAPA TIBIA - 1 tarea crítica y 2 secundarias]
├── data/
│   └── siegfried_vault.jsonl  [CAPA CALIENTE - Bitácora Append-Only]
```

* **Concurrencia Segura:** Todas las operaciones de lectura/escritura sobre `siegfried_vault.jsonl` implementan **bloqueo a nivel de kernel mediante `fcntl.flock`**. Evita condiciones de carrera entre el daemon y la sesión REPL.
* **Pre-agregador Histórico:** Para consultas de periodos (*"¿cuántas horas estudié esta semana?"*), un escáner en Python lee el JSONL en reversa, precalcula los totales numéricos en <2 ms e inyecta un bloque sintético condensado (<60 tokens) al LLM.

---

## 6. Centinela Biológico, Hábitos y Alertas Sensoriales

### A. Vigilancia del Sueño y Descanso Manual
* **Detección Automática:** Registro de marcas de tiempo de apagado (`shutdown`), suspensión (`suspend`) y encendido matutino, descontando una latencia de conciliación estimada de 25 minutos.
* **Comando Manual en Lenguaje Natural:** El usuario puede registrar su retiro sin apagar el equipo (*"Siegfried, me voy a dormir ya"*). El sistema guarda el evento `sleep_initiated` y silencia alertas hasta la mañana.

### B. Protocolo Pomodoro y Límite Postural de 60 Minutos
* **Bloques:** Modalidad Profunda (50 min trabajo / 10 min pausa) y Modalidad Ágil (25 min / 5 min).
* **Prórroga Consciente:** Permite hasta 10 minutos de extensión en estado de flow (`posponer 10`).
* **Barrera Dura de los 60 Minutos:** Al cumplirse 60 minutos continuos sentado, **las prórrogas quedan bloqueadas**.
* **Alarma Sensorial Inmediata:** 
  * Al llegar al límite de 60 minutos, el daemon dispara una notificación en KDE y reproduce de forma asíncrona un audio distintivo (`pw-cat -p ~/.siegfried/assets/sounds/leaver.ogg &` o `paplay`).
  * **Silenciado Rápido:** Presionar la tecla `Espacio` o `Enter` dentro de la sesión REPL, o pulsar el botón de acción en la notificación de KDE, detiene el reproductor (`pkill pw-cat`) y marca la pausa activa como iniciada.

---

## 7. Integración con el Escritorio (Kubuntu / KDE Plasma)

* **Monitoreo de Foco en Wayland:** El daemon consulta de forma pasiva el identificador de ventana enfocado a través del bus de sesión DBus de KWin (`org.kde.KWin`). Cero uso de `xdotool`.
* **Boot Hook Matutino:** Script en el inicio de sesión que genera una notificación flotante en KDE vía `notify-send` / `kdialog`. Incluye el botón interactivo **[Abrir Sesión]**, el cual lanza la terminal en modo REPL interactivo con Siegfried.

---

## 8. Calibración Dinámica del Tono

* **Modo Ejecutivo (1-2 oraciones):** Se activa automáticamente ante frases cortas (<7 palabras), modo imperativo directo o Pomodoros activos.
* **Modo Mayordomo Completo (Solemnidad y cuidado):** Se activa ante entradas largas (>25 palabras), lenguaje de cansancio/frustración, o durante el briefing matutino y balance nocturno.

---

## 9. Estructura Completa de Directorios del Proyecto

```
~/.siegfried/
├── bin/
│   ├── siegfried              # CLI interactivo REPL (Python 3 stdlib)
│   └── siegfried-daemon       # Demonio de monitoreo en segundo plano
├── config/
│   ├── secrets.env            # Variables protegidas (DeepSeek API Key, chmod 600)
│   ├── core_profile.json      # Perfil permanente y restricciones de salud
│   └── active_agenda.json     # Tareas activas (1 crítica, 2 secundarias)
├── data/
│   └── siegfried_vault.jsonl  # Bitácora histórica (bloqueo atómico con fcntl)
├── assets/
│   └── sounds/
│       └── leaver.ogg         # Alerta sonora para el umbral de 60 min
├── systemd/
│   └── siegfried.service      # Servicio de usuario systemd (--user)
└── scripts/
    └── kwin_focus_watcher.js  # Script KWin para monitoreo pasivo en Wayland
```

---

## 10. Plan Maestro de Implementación

El desglose completo del desarrollo en 5 fases, estimaciones temporales (51.5 horas totales), análisis de riesgos por fase y criterios de aceptación se encuentra registrado en el documento independiente [PLAN.md](file:///media/okami/Mio/Siegfried/PLAN.md).

* **Fase 1:** Cimientos del Sistema de Archivos, Esquemas y Persistencia Atómica Concurrente (`storage.py`, `fcntl`, permisos `600`).
* **Fase 2:** Motor de Inferencia Híbrido, Enrutador Fast-Path y Fallback Local (`inference.py`, `llama_manager.py`, `router.py`).
* **Fase 3:** Demonio en Segundo Plano (`siegfried-daemon`), Timers y Alertas Sensoriales (`daemon.py`, `notifier.py`, `systemd`).
* **Fase 4:** Interfaz de Usuario REPL Interactiva y Pre-agregador Histórico (`repl.py`, `aggregator.py`).
* **Fase 5:** Integración con el Sistema Operativo, Wayland DBus y Boot Briefing (`kwin_watcher.py`, `boot_hook.py`).

