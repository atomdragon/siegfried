# GATE REPORT: FASE 2 — MOTOR HÍBRIDO E INFERENCIA LOCAL DETERMINISTA (CUDA)

- **Identificador de Gate:** `GATE_F2`
- **Fecha y Hora de Corte:** Viernes, 9 de octubre de 2026, ~23:48 (Perú, UTC−05:00)
- **Rama / Checkout:** `/media/okami/Mio/Siegfried` en rama `main`
- **Host Operativo:** Kubuntu Linux (KDE Plasma sobre Wayland, Kernel x86_64), usuario `okami` (UID 1000)
- **Hardware de Ejecución:** Intel Core i9-14900HX (24C/32T), 64 GB DDR5 RAM, NVIDIA GeForce RTX 4070 Laptop GPU (8188 MiB VRAM total, Driver 595.91.07, `libcuda.so.1` activa)
- **Estado de Evaluación del Gate:** **PASS (Certificación Operativa Exitosa en Hardware Real)**

---

## 1. Alcance Contractual y Principios Innegociables

La Fase 2 implementa y certifica el motor de inferencia híbrido de Siegfried (Vía Cognitiva determinista), gobernado bajo las siguientes reglas cardinales:

1. **Jerarquía Operativa:** El LLM interpreta y recomienda; el núcleo determinista calcula, valida y ejecuta. El LLM carece de permisos de mutación directa sobre el sistema operativo, timers o almacenamiento.
2. **Presupuesto de VRAM:** Presupuesto de seguridad de $\le 3072\text{ MiB}$ ($\le 3.0\text{ GiB}$) en la GPU NVIDIA RTX 4070 Laptop para la configuración base del asistente, garantizando $> 4.7\text{ GiB}$ libres para el entorno de escritorio Wayland y desarrollo.
3. **Límite Temporal (Deadline Monotónico):** Presupuesto temporal duro de $10.0\text{ s}$ acumulado para el ciclo de inferencia y streaming.
4. **Ciclo de Vida Efímero (Eviction por Inactividad):** Desalojo automático de `llama-server` tras $900\text{ s}$ continuos en estado `IDLE`, gestionado por `ResourceBudget`.
5. **Alcance Finito de Fases:** El diseño del sistema comprende estrictamente las fases F0–F5. No existe ni se proyecta una Fase 6.

---

## 2. Defectos de Software Remediados en `src/`

| Defecto | Archivo(s) Afectado(s) | Causa Raíz | Solución Aplicada |
| :--- | :--- | :--- | :--- |
| **Firma Inválida Cloud** | `src/siegfried/daemon/app.py` | Argumento no soportado `paths=` en constructor de `CloudInferenceClient`. | Removido el parámetro, alineando la llamada con el contrato F2. |
| **Conexión de Eviction en Daemon** | `src/siegfried/daemon/app.py`, `src/siegfried/inference/llama_manager.py` | El bucle principal del daemon no ejecutaba la comprobación de inactividad del gestor. | Conectada la llamada periódica `check_idle()` en estado `IDLE` (el timeout de $900\text{ s}$ reside y se evalúa dentro de `ResourceBudget`). |
| **Bloqueo Indefinido / Falta de Deadline** | `src/siegfried/inference/deadline.py`, `src/siegfried/inference/local.py` | Ausencia de cómputo monotónico compartido entre arranque HTTP y lectura de chunks. | Implementado `deadline.py`; timeout restante propagado a conexión y lecturas. |

### Validación de Regresión Aislada (Landlock)
- **Suite Ejecutada:** `tools/run_isolated_tests.py` bajo arnés Landlock sandbox.
- **Resultado:** **706 / 706 pruebas en PASS (100%)**, incorporando 17 pruebas específicas de configuración y regresión en `tests/unit/test_inference_f2_regressions.py`.

---

## 3. Infraestructura de Inferencia Local Desplegada

Ante la ausencia de `nvcc` en el host, se utilizó un release precompilado oficial con dependencias dinámicas aisladas:

1. **Binario de Inferencia:** `llama.cpp` release `b11540` (`llama-b11540-bin-ubuntu-cuda-12.8-x64.tar.gz`, SHA-256: `114c5017d19e230cd5df697f09c2d7af87c1ddfe1f0b3f1fa484ceab882d84db`).
2. **Runtime Dinámico CUDA:** Paquete oficial `cudart-llama-b11540-bin-ubuntu-cuda-12.8-x64.tar.gz` (SHA-256: `8f44b435a6739de562b3b709cf2de09cba909adc1f32e3def526aa788b4ef62f`) en `~/.siegfried/opt/llama.cpp/b11540-cuda12.8/runtime/`.
3. **Wrapper Local:** `~/.siegfried/bin/llama-server` (permisos `0700`) resolviendo dependencias dinámicas localmente sin modificar `LD_LIBRARY_PATH` a nivel global.
4. **Modelo Base Aprovisionado:** `Qwen 2.5 7B Instruct` (`Q4_K_M`) en dos fragmentos (`chmod 0600`):
   - `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` (SHA-256: `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db`).
   - `qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf` (SHA-256: `539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a`).
5. **Configuración Validada:** `--n-gpu-layers 16`, `--threads 8`, `--ctx-size 2048`, puerto loopback `8080`.

---

## 4. Matriz de Telemetría Real en Host

Certificación ejecutada contra el snapshot `~/.local/share/siegfried-daemon` bajo `siegfried.service` (`systemd --user`, PID 50352) en `/run/user/1000/siegfried.sock` (`0600`).

| Prueba Operativa | Condición / Entrada | Métrica de Referencia | Telemetría Real Observada | Veredicto |
| :--- | :--- | :--- | :--- | :--- |
| **VRAM en Frío (Baseline)** | Sistema en reposo | N/A | **42 MiB** en uso | **PASS** |
| **Smoke Test Offload** | Carga manual (16 capas) | $\le 3072\text{ MiB}$ objetivo | **2836 MiB** (pico observado) | **PASS** |
| **Aborto por Deadline** | Petición sin límite de tokens | Aborto cercano a $10.0\text{ s}$ | Aborto controlado recibido en IPC a los **10.03 s** (`InferenceDeadlineExceededError`) | **PASS** |
| **Latencia E2E Fría (Local)** | Inferencia ejecutiva (tope 64 tokens) | $\le 10.0\text{ s}$ total | **4.333 s** (respuesta estructurada devuelta vía IPC) | **PASS** |
| **Consumo VRAM Operativo** | Inferencia activa bajo daemon | $\le 3072\text{ MiB}$ objetivo | **2838 MiB** (pico medido para esta configuración) | **PASS** |
| **Eviction Idle Timeout** | Inactividad sostenida tras última consulta | $900\text{ s}$ configurados | Desalojo efectivo observado a los **900.617 s** | **PASS** |
| **Liberación Post-eviction** | Proceso terminado | Retorno a baseline | **42 MiB** VRAM, puerto 8080 cerrado, PID file removido | **PASS** |

---

## 5. Salvedades y Condiciones Operativas Retenidas

1. **Acotamiento de Tokens (`max_tokens = 64`):** La prueba E2E fría completada en $4.333\text{ s}$ utilizó una cota operativa de 64 tokens. La respuesta narrativa resultó truncada por diseño para garantizar el cumplimiento del deadline de $10\text{ s}$ frente a la tasa de decodificación en modo híbrido (16 capas GPU / 13 capas CPU).
2. **Desconexión Temporal del Muestreo NVML:** A los ~238 s de inactividad, una llamada de telemetría a `nvidia-smi` agotó su timeout interno de 5 s, interrumpiendo el registro de sondas externas. La observación se retomó a los $377.9\text{ s}$ comprobando que el proceso `llama-server` (PID 50361) permaneció activo e inalterado en VRAM, culminando en la expulsión limpia a los $900.617\text{ s}$.
3. **Pico de Memoria vs. Límite Universal:** El valor de $2838\text{ MiB}$ representa el pico máximo medido durante esta prueba y con este tamaño de contexto ($2048$ tokens). No constituye una garantía matemática universal para cargas de inferencia arbitrarias o contextos superiores.

---

## 6. Verificación de Higiene del Host

- [x] **Cero Archivos Bytecode (`.pyc`):** Toda validación sintáctica de despliegue se realizó vía AST en memoria con `/usr/bin/python3 -B`.
- [x] **Integridad de `.history`:** El archivo sensible `~/.siegfried/data/.history` no fue tocado, respetando la directiva `UNRESOLVED`.
- [x] **Sin Escalación de Privilegios:** Cero uso de `sudo` o alteraciones de paquetes de sistema / PyPI.
- [x] **Socket Unix:** Permisos verificados en `0600` en `/run/user/1000/siegfried.sock`.

---

## 7. Dictamen Final

La certificación operativa de la **Fase 2 (Motor Híbrido e Inferencia Local)** en hardware real queda formalmente aprobada bajo las condiciones empíricas documentadas.

Evidencia consolidada disponible en:
`~/.siegfried/logs/f2-certification-b11540.json`

**GATE_F2: CERRADO (PASS)**
