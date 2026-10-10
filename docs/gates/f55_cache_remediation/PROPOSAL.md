# F5.5 — Propuesta pendiente de autorización

Estado: PREPARED_NOT_APPLIED. Producción, snapshots y XDG reales no modificados.
Servicio active/enabled, PID 2704, NRestarts=0. Desktop efectivo Hidden=false.

## Causa y alcance

El desktop efectivo ejecuta Python sin -B. El hook importa módulos locales antes
de entrar a main. Los workers lanzan un intérprete nuevo con -S, sin -B, con un
env explícito que sólo contiene PATH/LANG; -S no desactiva bytecode y -B del padre
no se hereda. El launcher independiente de Konsole/REPL tampoco contiene -B.
PYTHONDONTWRITEBYTECODE/PYTHONPATH están ausentes en el entorno del manager actual;
el daemon propio sí tiene PYTHONDONTWRITEBYTECODE=1. El hook ya no corre, por lo
que su entorno histórico y el PID escritor exacto no pueden recuperarse.

La reproducción temporal demuestra las tres rutas y que el worker puede recrear
caches incluso cuando el intérprete padre usa -B. Los 33 pyc actuales tienen magic
CPython, header/source timestamp/tamaño correctos y body idéntico a recompilar cada
fuente auditada con su path efectivo. Ningún bytecode se ejecutó para atribuirlo.

57 fuentes originales y marcador coinciden; 33 pyc 0600, UID/GID del snapshot,
nlink=1; seis directorios __pycache__ 0775. Cero archivos extra desconocidos,
symlinks, hardlinks o propietarios ajenos. Padres originales y raíz 0700 preservados.
Los modos internos 0775 incumplen el manifiesto pero los padres privados impiden
acceso desde otras cuentas. Se acredita desviación de inventario/permisos, sin
evidencia de corrupción de fuente ni exposición de datos del runtime.

[cache-inventory.json](cache-inventory.json) enumera exactamente las 33 rutas y seis
directorios, sus hashes y dev/inodo/UID/GID/modo/nlink/size/mtime/ctime, además de la
prueba de compilación y originales/candidatos permitidos. No incluye datos personales.
[proposal.json](proposal.json) fija el hash/metadatos del desktop y hashes de candidatos.

## Prevención propuesta

[prevent-bytecode.patch](prevent-bytecode.patch) añade solamente -B a:

1. tools/install_boot_briefing.py: generación de Exec del desktop.
2. src/siegfried/integrations/boot_briefing.py: argv de workers, preservando -S.
3. src/siegfried/integrations/briefing_kde.py: argv de Python del launcher REPL.

Despliegue Boot propuesto, sólo después de autorizar:

- ~/.local/share/siegfried-boot/src/siegfried/integrations/boot_briefing.py
- ~/.local/share/siegfried-boot/src/siegfried/integrations/briefing_kde.py
- ~/.config/autostart/org.siegfried.BootBriefing.desktop

El desktop tiene hash original 13a581d8ac2094a37b86f63322e9f1a1b3ee1e6cab4eaf8c8b5cbf65c438360d,
UID/GID 1000/1000, modo 0600, nlink=1, contenido idéntico a original.desktop.
proposed.desktop conserva todos sus campos y añade "-B" después de Python.
No se modifica hook/composición/inferencia, bin/siegfried, snapshot daemon,
servicio, habilitación, marcador de sesión o runtime. El -B del launcher sólo
previene bytecode; NO aísla historial y NO autoriza clicar Abrir sesión.

## Verificaciones realizadas

Doce comprobaciones en HOME/snapshots temporales PASS (fixture-results.json):
recreación por ruta original, -B de padre insuficiente para worker original,
parent/worker corregidos sin caches, instalador y desktop KDE temporales válidos,
launcher argv protegido comprobado interceptando Popen (sin Konsole/REPL), borrado
exacto 33/6 con backup y preservación de fuentes/archivo ajeno, rechazo de caché
cambiada/symlink/hardlink/archivo desconocido/directorio reemplazado/hook activo.
No notificaciones nuevas, REPL, login o regresión completa. No fuente productiva
modificada; el patch existe sólo como propuesta y copias de fixture.

## Secuencia propuesta para aplicar con autorización específica

1. Revalidar hashes del patch/inventario/helper y archivos reales. Repetir check
   read-only. No proceder con ningún elemento desconocido, hook activo o cambio.
2. Aplicar las tres líneas del patch al repositorio preservando cambios anteriores.
   Al modificar producción, ejecutar regresión completa mediante el harness aislado
   oficial antes de desplegar. No usar suite directamente sobre HOME real.
3. Guardar originales de los dos módulos Boot y desktop en el directorio privado
   ~/.local/share/siegfried-gate-f55 (0700), archivos 0600, sin sobrescribir backups.
   Verificar otra vez origen SHA/UID/GID/modo/nlink/identidad y contenido exacto.
   Publicar candidatos mediante temporales propios 0600 + reemplazo atómico,
   conservando owner/modo; nunca adoptar/reemplazar un archivo ajeno o cambiado.
4. Actualizar manifiesto Boot para los dos hashes deliberadamente cambiados,
   conservando inventario y evidencia originales. Desktop debe seguir Hidden=false.
5. Verificar ausencia de hook, ejecutar helper de limpieza sólo con autorización:

```bash
/usr/bin/python3 -B tools/check_boot_cache_f55.py \
  --manifest docs/gates/f55_cache_remediation/cache-inventory.json \
  --apply \
  --backup /home/okami/.local/share/siegfried-gate-f55/cache-backup-f55.json
```

El helper permite únicamente hashes originales o candidatos de los dos módulos;
verifica el árbol completo, caches exactamente iguales (incluyendo identidad,
timestamps y hash), root propio, ningún enlace/tipo desconocido/mount ajeno,
ningún hook activo. Crea backup exclusivo 0600 de los 33 bytecodes dentro de un padre
0700 fuera de snapshot/runtime, revalida todo, vuelve a verificar antes de cada
unlink y rmdir. Usa dirfd/O_NOFOLLOW; rmdir falla ante cualquier entrada nueva.
No usa rm -r, glob destructivo ni desinstalador. Un fallo para la operación; no
se anuncia atomicidad global ni una exclusión absoluta de procesos externos que
arranquen entre consultas. Mantener esta sesión, sin lanzar hooks ni otro login
mientras se aplica. Bajo amenaza de escritor malicioso del mismo UID estas consultas
no constituyen un bloqueo transaccional.

6. Inventario final: 57 fuentes candidatas más marcador, 14 dirs contando root,
   sin caches, permisos originales. Desktop desktop-file-validate PASS/Hidden=false.
   Servicio mismo PID/active/enabled/NRestarts, PING/STATUS y runtime/private-data
   comparado a la referencia persistente existente. Sólo dry-run/probe temporal para
   probar ausencia de recreación, sin notificación real, REPL ni logout/reinicio.

## Recuperación propuesta (no ejecutada)

Un fallo antes de deploy: conservar originales y parar. Durante deploy, restaurar
sólo esos dos módulos y desktop, sólo si el archivo de destino aún coincide con el
candidato escrito por esta operación, usando backup SHA verificado. No tocar
servicios/Autostart enabled/runtime ni archivos ajenos. Si un destino cambió, parar
para revisión en lugar de sobrescribirlo.

La limpieza conserva backup completo de bytecodes antes del primer unlink. Si falla
parcialmente, registrar exactamente qué cachés se retiraron y cuáles quedan; no
continuar con borrados generales ni reetiquetar un nuevo inventario como original.
Caches son derivadas: no se necesita restaurarlas para funcionar. Cualquier decisión
de reconstruirlas queda para autorización posterior, usando sólo las rutas de backup
con propietarios/modos privados. Una copia restaurada no recupera inodo/ctime original.
Ningún rollback puede recuperar retroactivamente evidencia del login ya realizado.

## Estado del Gate

PASS humano login/briefing y PASS técnico daemon permanecen. Historial actual
preservado contra referencia auténtica anterior y referencia persistente reciente;
incidente histórico UNRESOLVED. PRE_LOGIN_BASELINE_UNAVAILABLE para runtime
completo durante el primer login, RSS histórico excedido y SLO login→visible <2 s
sin certificar. Gate BLOCKED; esta propuesta no declara preservación retroactiva.
