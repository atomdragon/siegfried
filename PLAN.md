# Plan Maestro de Implementación — Siegfried MVP (Fases 1 a 5)

---

## Fase 1: Cimientos del Sistema de Archivos, Esquemas y Persistencia Atómica Concurrente

### 1.1 Objetivo y Alcance Técnico
Establecer la infraestructura de almacenamiento local en `~/.siegfried/`, garantizando concurrencia segura sin corrupción de datos entre múltiples procesos (demonio y CLI) mediante primitivas del kernel de Linux (`fcntl`), y aislando credenciales con permisos restrictivos.

### 1.2 Tareas de Ingeniería
1. **Scaffolding de Directorios y Control de Permisos:**
   * Crear el árbol: `~/.siegfried/{bin,config,data,assets/sounds,systemd,scripts}`.
   * Inicializar `secrets.env` con máscara `0o077` antes de la creación (`os.umask(0o077)`) para forzar permisos strictly `600` (`-rw-------`).
2. **Definición de Esquemas JSON Base:**
   * `core_profile.json`: Estructura para registrar datos biológicos, límite postural (60 min), cursos activos, materias críticas y stack (.NET/C#).
   * `active_agenda.json`: Estructura del embudo con llaves fijas: `critical_task` (objeto o `null`), `secondary_tasks` (array de hasta 2 objetos), y `backlog` (array de tareas diferidas).
   * `siegfried_vault.jsonl`: Archivo vacío con encabezado de verificación de integridad.
3. **Módulo `storage.py` (Librería Estándar de Python):**
   * **Escritura Atómica en JSONL (`append_vault`):** Apertura en modo append (`a`), obtención de bloqueo exclusivo `fcntl.flock(fd, fcntl.LOCK_EX)` dentro de un context manager, serialización JSON + salto de línea, vaciado forzado de buffers a disco (`f.flush()`, `os.fsync(fd)`) y liberación del cerrojo (`fcntl.LOCK_UN`).
   * **Reemplazo Atómico para JSON (`atomic_write_json`):** Escribir primero en un archivo temporal en la misma partición (`active_agenda.json.tmp`) y aplicar `os.replace()` atómico a nivel de inodo POSIX para evitar lecturas de archivos a medio escribir si el proceso se interrumpe.
   * **Lectura Segura con Bloqueo Compartido (`read_vault_tail` / `read_json`):** Implementar `fcntl.flock(fd, fcntl.LOCK_SH)` para garantizar que ninguna lectura ocurra mientras otro proceso está en medio de un `os.fsync`.

### 1.3 Matriz de Riesgos y Mitigación
* **Deadlock en `fcntl.flock`:** Usar bandera no bloqueante combinada `fcntl.LOCK_EX | fcntl.LOCK_NB` dentro de un bucle de reintento con backoff exponencial (máximo 5 reintentos de 10 ms). Abortar con excepción controlada en lugar de colgar el proceso.
* **Líneas truncadas en `vault.jsonl`:** `os.fsync()` obligatorio antes de liberar el candado. Si una línea falla en `json.loads()`, ignorarla silenciosamente y escribir una copia de rescate en `vault.corrupt.log`.
* **Fuga de permisos en `secrets.env`:** Validación en tiempo de arranque en `storage.py`. Si los permisos no son `600`, lanzar error fatal y detener el inicio.

### 1.4 Estimación Temporal
* Scaffolding y permisos: 1.5 h.
* Módulo `storage.py` con `fcntl` y reemplazos atómicos: 3.5 h.
* Pruebas de concurrencia (1,000 escrituras paralelas): 2.0 h.
* **Subtotal Fase 1: 7.0 horas.**

### 1.5 Criterio de Verificación
20 procesos escribiendo 50 eventos en paralelo sobre `siegfried_vault.jsonl`. El comando `wc -l ~/.siegfried/data/siegfried_vault.jsonl` debe reportar exactamente 1,000 líneas válidas verificables con `jq .`.

---

## Fase 2: Motor de Inferencia Híbrido, Enrutador Fast-Path y Fallback Local

### 2.1 Objetivo y Alcance Técnico
Desarrollar el subsistema de inferencia sin dependencias externas (`urllib.request`), implementando la política de tolerancia de red de 10 segundos, el gestor de ciclo de vida perezoso (*lazy loading*) de `llama-server` para preservar VRAM, el enrutador regex sub-milisegundo y el calibrador dinámico de tono.

### 2.2 Tareas de Ingeniería
1. **Cliente de Inferencia Cloud (`inference.py`):**
   * Payload HTTP POST compatible con OpenAI/DeepSeek vía `urllib.request` y `json` con timeout estricto de socket de 3.0 s.
   * **Buffer de Gracia de 10s:** Si hay error de red/DNS, iniciar reintento cada 3 segundos. Si a los 10 segundos acumulados no hay conexión, activar bandera `SYSTEM_OFFLINE = True`.
2. **Supervisor de Proceso Local (`llama_manager.py`):**
   * Control de `llama-server` vía `subprocess.Popen` (`--model qwen2.5-7b-instruct-q4_k_m.gguf --n-gpu-layers 16 --threads 8 --port 8080`).
   * **Idle Timeout (15 min):** Hilo en segundo plano que mide inactividad offline. A los 900 segundos envía `SIGTERM`/`SIGKILL` y libera los 2.7 GB de VRAM de la RTX 4070.
3. **Enrutador Rápido / Fast-Path (`router.py`):**
   * Expresiones regulares compiladas (`re.compile`) para intenciones operativas directas (pomodoro, prórroga, descanso manual, silenciado). Si coincide, responde en <1 ms sin invocar al LLM.
4. **Calibrador Dinámico de Tono:**
   * Entradas <7 palabras, verbos directos o foco activo inyectan directiva `[MODO: EJECUTIVO]` (máximo 2 oraciones, cero preámbulos).
   * Entradas >25 palabras o palabras emocionales (*"cansado"*, *"bloqueado"*) inyectan `[MODO: MAYORDOMO_COMPLETO]` (protector, reconocimiento y advertencias de salud).

### 2.3 Matriz de Riesgos y Mitigación
* **Proceso Zombie de `llama-server` reteniendo VRAM:** Manejador de salida con `atexit.register()`, captura de `SIGTERM` y guardado de PID en `/run/user/$UID/siegfried_llama.pid`.
* **Falsos positivos en regex:** Delimitadores estrictos (`^...$`) y longitud máxima de 12 palabras. Si la frase es más larga, derivar a la Vía Cognitiva (LLM).
* **Socket colgado en HTTP:** Fijar timeout de socket global con `socket.setdefaulttimeout(3.5)`.

### 2.4 Estimación Temporal
* Cliente HTTP y reintento de 10s: 2.5 h.
* Gestor lazy load / idle timeout de `llama-server`: 4.0 h.
* Regex y calibrador de tono: 2.5 h.
* Pruebas de descarga de VRAM con `nvidia-smi`: 2.0 h.
* **Subtotal Fase 2: 11.0 horas.**

### 2.5 Criterio de Verificación
1. Entrada *"iniciar bloque de 50"* responde en <10 ms.
2. Al cortar Wi-Fi, la llamada espera 10s, levanta `llama-server` (~2.7 GB VRAM) y libera la VRAM tras 15 minutos de inactividad.

---

## Fase 3: Demonio en Segundo Plano (`siegfried-daemon`), Timers y Alertas Sensoriales

### 3.1 Objetivo y Alcance Técnico
Construir el servicio autónomo administrado por `systemd --user` que orqueste la máquina de estados de salud, administre el tiempo de asiento continuo (límite innegociable de 60 minutos), escuche conexiones IPC vía socket Unix y controle las alarmas visuales y auditivas.

### 3.2 Tareas de Ingeniería
1. **Servidor de Socket Unix (`daemon.py`):**
   * Socket en `/run/user/$UID/siegfried.sock`.
   * Bucle no bloqueante con `selectors` para atender la CLI y los cronómetros en el mismo hilo.
2. **Máquina de Estados y Centinela de Postura:**
   * Estados: `IDLE`, `POMODORO_RUNNING`, `POSTPONE_RUNNING`, `BREAK_RUNNING`.
   * Contador postural continuo: Aviso al minuto 50. Permite prórroga hasta el minuto 60. Al cumplirse 60 min, **barrera dura**: rechaza prórrogas y pasa a `CRITICAL_BREAK_REQUIRED`.
3. **Notificador y Alerta Sensorial (`notifier.py`):**
   * Notificación en KDE Plasma vía `notify-send` o `kdialog`.
   * Reproducción sonora asíncrona: `pw-cat -p ~/.siegfried/assets/sounds/leaver.ogg &` (fallback a `paplay`). Almacenar PID para cancelación inmediata ante confirmación de pausa.
4. **Servicio Systemd de Usuario:**
   * Unidad `~/.config/systemd/user/siegfried.service` (`Restart=always`, `RestartSec=2s`).

### 3.3 Matriz de Riesgos y Mitigación
* **Address already in use en Socket Unix:** Probar conexión con `connect()`; si no responde, desvincular con `os.unlink()` antes de `bind()`.
* **Drift en timers por suspensión de la laptop:** Prohibido usar `sleep()` para cronometrar. Comparar siempre marcas de tiempo absolutas contra `time.monotonic()`.
* **Fallo de reproducción de audio:** Archivo de respaldo `.wav`. Fallback en cascada: `pw-cat` -> `paplay` -> `aplay` -> `printf '\a'`.

### 3.4 Estimación Temporal
* Servidor Socket Unix con `selectors`: 3.0 h.
* Máquina de estados y centinela de 60 min: 4.0 h.
* Integración audio/notificación: 2.5 h.
* Configuración y pruebas con `systemd --user`: 2.5 h.
* **Subtotal Fase 3: 12.0 horas.**

### 3.5 Criterio de Verificación
Simular 60 minutos sentado: (1) Se reproduce el sonido sin congelar el demonio; (2) Notificación en KDE; (3) Rechazo automático de nuevas prórrogas; (4) Comando `ACK_BREAK` mata el reproductor de sonido inmediatamente.

---

## Fase 4: La Interfaz de Usuario REPL Interactiva y Pre-agregador Histórico

### 4.1 Objetivo y Alcance Técnico
Construir el ejecutable interactivo `siegfried` en modo REPL fluido, gestionando señales de terminal, ofreciendo atajos inmediatos (Espacio/Enter para cortar alarmas) e integrando el pre-agregador analítico de métricas en <2 ms.

### 4.2 Tareas de Ingeniería
1. **Consola REPL Interactiva (`repl.py`):**
   * Configuración de `readline` para historial con flechas y guardado en `~/.siegfried/data/.history`.
   * Prompt `[Siegfried] > ` y captura limpia de `Ctrl+C` y `Ctrl+D`.
2. **Silenciado Rápido de Emergencia:**
   * Teclas `Enter` o `Espacio` en la terminal durante una alarma envían `ACK_BREAK` al demonio, silenciando el audio y registrando la pausa activa.
3. **Módulo Pre-agregador Histórico (`aggregator.py`):**
   * Lectura en reversa con `f.seek()` desde el final del archivo en bloques para no saturar memoria.
   * Filtro por rangos temporales (*"esta semana"*, *"hoy"*), totalizando minutos netos, pausas de pie cumplidas y desglose de horas por curso.
   * Inyección del bloque sintético `<metricas_historicas>` en el prompt del LLM.

### 4.3 Matriz de Riesgos y Mitigación
* **Degradación con archivos JSONL grandes (>100 MB):** Romper la lectura en reversa con `break` en cuanto se encuentre un timestamp fuera del rango solicitado.
* **Corrupción visual por códigos ANSI:** Delimitar secuencias ANSI con `\001` y `\002` para el cálculo exacto del ancho de línea en `readline`.
* **Daemon inactivo al abrir el CLI:** Timeout de conexión al socket de 0.2 s. Si no responde, emitir aviso elegante y continuar en modo directo sin bloquear la consola.

### 4.4 Estimación Temporal
* Bucle REPL y señales de terminal: 3.0 h.
* Interceptación para corte sonoro: 1.5 h.
* Algoritmo de lectura reversa en `aggregator.py`: 3.5 h.
* Pruebas de estrés histórico: 1.5 h.
* **Subtotal Fase 4: 9.5 horas.**

### 4.5 Criterio de Verificación
Con 20,000 líneas en `vault.jsonl`, consultar: *"¿Cuánto tiempo estudié esta semana?"*. El pre-agregador debe procesar los datos en <3 ms y DeepSeek debe responder con los totales numéricos calculados, sin alucinaciones.

---

## Fase 5: Integración con el Sistema Operativo, Wayland DBus y Boot Briefing

### 5.1 Objetivo y Alcance Técnico
Completar la integración de Siegfried en Kubuntu: monitoreo pasivo de ventanas enfocado a través del bus DBus de KWin bajo Wayland, cálculo de ventana real de sueño al encender el equipo y notificación interactiva matutina en KDE Plasma.

### 5.2 Tareas de Ingeniería
1. **Monitor de Ventanas en Wayland (`kwin_watcher.py`):**
   * Consulta al bus de sesión DBus: `qdbus org.kde.KWin /KWin org.kde.KWin.activeWindow` (o script KWin local).
   * Lectura periódica (cada 60 s) por el demonio para detectar ventanas de ocio durante bloques de estudio.
2. **Cálculo de Descanso y Latencia de Sueño:**
   * Script `boot_hook.py` al iniciar sesión en KDE.
   * Lee la hora del último `shutdown`/`suspend` y calcula:
     $$\text{Descanso Real} = (\text{Hora Actual} - \text{Hora Apagado}) - 25\text{ minutos (Latencia Conciliación)}$$
3. **Consulta Climática Resiliente:**
   * Endpoint de texto plano (`wttr.in/Lima?format=%c+%t+%C`) con timeout de 0.8 s. Si falla, continúa sin retrasar el saludo.
4. **Notificación Interactiva Matutina:**
   * `kdialog` / `notify-send` con acción interactiva. Si se confirma, lanza: `konsole -e siegfried`.

### 5.3 Matriz de Riesgos y Mitigación
* **Cambios en API de KWin DBus:** Si la consulta DBus falla, desactivar el rastreo de ventanas silenciosamente sin afectar timers ni pomodoros.
* **Arranque sin Wi-Fi:** Ping de 0.5 s a `1.1.1.1`. Si no hay red, emitir saludo determinista precalculado en <100 ms sin llamar a la API.
* **Doble notificación por abrir/cerrar la tapa rápido:** Umbral de desconexión mínimo de 90 minutos para considerar el evento como boot matutino.

### 5.4 Estimación Temporal
* Integración KWin DBus para Wayland: 3.5 h.
* Cálculo de latencia de sueño: 2.5 h.
* Boot briefing y consulta de clima: 2.0 h.
* Lanzador `.desktop` en KDE Autostart: 1.5 h.
* Pruebas de ciclo de vida (suspensión/reinicio): 2.5 h.
* **Subtotal Fase 5: 12.0 horas.**

### 5.5 Criterio de Verificación
Reiniciar la máquina. En <2 s tras ingresar al escritorio debe aparecer la notificación de Siegfried con el cálculo de descanso y pendientes. Un clic en "Abrir Sesión" debe desplegar Konsole con el REPL activo.

---

## Cronograma Total Consolidado
* **Fase 1 (Almacenamiento y Concurrencia):** 7.0 h
* **Fase 2 (Inferencia y Fast-Path):** 11.0 h
* **Fase 3 (Demonio y Alarmas):** 12.0 h
* **Fase 4 (REPL y Pre-agregador):** 9.5 h
* **Fase 5 (Wayland y Boot Matutino):** 12.0 h
* **Total Estimado de Desarrollo:** **51.5 horas de desarrollo neto.**
