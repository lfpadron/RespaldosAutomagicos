# RespaldosAutomagicos

RespaldosAutomagicos es la base de una aplicacion profesional para Windows que administrara respaldos ZIP automaticos por grupos configurables desde una TUI.

La iteracion actual incluye arquitectura, configuracion, base de datos, migraciones, TUI, CRUD de grupos, repositorios, controllers, bus interno de eventos, vigilancia con watchdog, planificacion de respaldos pendientes, hashing real de contenido, creacion de ZIP, auditoria, retencion por cantidad/antiguedad y restauracion segura. Todavia no implementa Task Scheduler.

## Requisitos

- Python 3.13
- uv

## Instalacion

```powershell
uv sync
```

## Ejecutar la TUI

```powershell
uv run respaldos-automagicos
```

Tambien puede ejecutarse como modulo:

```powershell
uv run python -m respaldos_automagicos
```

En Windows tambien puedes usar:

```powershell
.\abrir_tui.bat
```

## Pantallas TUI

Menu principal:

```text
RespaldosAutomagicos

1. Grupos de respaldo
2. Restaurar
3. Historial
4. Auditoria
5. Configuracion
6. Acerca de
Q Salir
```

Grupos de respaldo:

```text
Seleccionar todos | Limpiar seleccion
Crear | Editar | Eliminar | Activar/Desactivar | Duplicar grupo
Escanear proyectos | Respaldar ahora

Seleccionado | Nombre | Activo | Raiz | Destino | Numero de proyectos
Pendientes | Ultimo respaldo | Proximo escaneo | Estado | Progreso
```

Historial:

```text
Filtro por grupo | Actualizar | Orden fecha

Fecha | Grupo | Proyecto | Resultado | Duracion | Tamano | Hash abreviado | Disponible
```

Restaurar:

```text
Actualizar | Cargar proyectos | Cargar versiones | Resumen | Restaurar

Grupo:
ID | Nombre

Proyecto:
ID | Proyecto | Estado

Version:
Fecha | Rxxx | Hash corto | Tamano | Archivos | Disponible

Confirmar restauracion
```

Auditoria:

```text
Filtro por grupo | Accion | Resultado | Actualizar

Fecha | Grupo | Directorio | Accion | Resultado | Detalles
```

## Base de datos y migraciones

Crear o actualizar la base de datos SQLite usando Alembic:

```powershell
uv run alembic upgrade head
```

Crear una nueva migracion futura:

```powershell
uv run alembic revision --autogenerate -m "descripcion"
```

## Pruebas y calidad

```powershell
uv run pytest
uv run ruff check .
uv run black --check .
uv run mypy
```

Formatear codigo:

```powershell
uv run black .
uv run ruff check . --fix
```

## Configuracion

Copia `.env.example` a `.env` y ajusta las rutas si lo necesitas.

```powershell
Copy-Item .env.example .env
```

Variables principales:

- `RESPALDOS_APP_NAME`
- `RESPALDOS_APP_VERSION`
- `RESPALDOS_DATABASE_URL`
- `RESPALDOS_DATA_DIR`
- `RESPALDOS_LOGS_DIR`
- `RESPALDOS_LOG_LEVEL`
- `RESPALDOS_SCHEDULER_TICK_SECONDS`

## Arquitectura

El nucleo de la aplicacion vive fuera de `tui/`. La TUI importa el nucleo, pero el dominio no depende de Textual.

- `config.py`: settings tipados con Pydantic Settings.
- `database.py`: base declarativa, engine y fabricas de sesiones SQLAlchemy.
- `app.py`: composicion inicial del nucleo de la aplicacion.
- `models/`: entidades SQLAlchemy del dominio.
- `repositories/`: acceso concreto a datos por entidad.
- `controllers/`: flujos de aplicacion reutilizables por interfaces.
- `services/`: contratos base, bus interno de eventos y servicio de directorios vigilados.
- `scheduler/`: cola de pendientes en memoria y planificador con estabilizacion.
- `watcher/`: integracion watchdog y resolucion del proyecto afectado.
- `retention/`: politicas de retencion por cantidad y antiguedad.
- `restore/`: servicio de restauracion segura con validacion de ZIP y manifest.
- `hashing/`: punto de extension para hashes de contenido futuros.
- `zipper/`: punto de extension para generacion ZIP futura.
- `audit/`: servicio y repositorio para auditoria de eventos.
- `tui/`: interfaz Textual principal.
- `utils/`: utilidades compartidas sin dependencia de UI.

La capa visible sigue este flujo:

```text
Model
  ^
Repositories
  ^
Services
  ^
Controllers
  ^
Textual UI
```

## Crear un grupo

1. Abre la TUI.
2. Entra a `Grupos de respaldo`.
3. Usa `Crear`.
4. Captura nombre, directorio raiz, destino, intervalos, retencion basica y compresion.
5. Guarda.
6. Usa `Escanear proyectos` para registrar subdirectorios inmediatos.

Validaciones principales:

- nombre obligatorio y unico;
- raiz y destino existentes;
- intervalo entre 5 y 1440 minutos;
- estabilizacion menor que intervalo;
- respaldos y dias de conservacion mayores o iguales a 1.

## Seleccion multiple de grupos

La pantalla `Grupos de respaldo` permite seleccionar uno o varios grupos desde la primera columna:

```text
  no seleccionado
X seleccionado
```

Controles:

- `Space`: selecciona o deselecciona el grupo resaltado cuando la tabla tiene foco;
- `Enter`: selecciona o deselecciona la fila resaltada;
- `Seleccionar todos` o `Ctrl+A`: selecciona todos los grupos visibles;
- `Limpiar seleccion` o `Ctrl+L`: limpia la seleccion.

Las acciones `Eliminar`, `Activar/Desactivar`, `Escanear proyectos` y `Respaldar ahora` operan sobre todos los grupos seleccionados. Si no hay seleccion, se usa como fallback el grupo resaltado. Para `Eliminar`, la TUI pide confirmacion y muestra cuantos grupos seran afectados.

## Respaldo manual

1. Selecciona un grupo.
2. Usa `Escanear proyectos` si aun no hay proyectos detectados.
3. Usa `Respaldar ahora`.
4. Revisa `Historial` y `Auditoria`.

Cuando hay varios grupos seleccionados, `Respaldar ahora` ejecuta un respaldo manual masivo en segundo plano. La TUI no se bloquea y muestra progreso por grupo:

- `En espera`: el grupo fue aceptado por el job y espera turno;
- `Escaneando`: se estan detectando proyectos del grupo;
- `Respaldando`: se estan generando respaldos;
- `Finalizado`: el grupo termino;
- `Error`: el grupo fallo, sin detener los demas grupos.

Al terminar cada proyecto, el respaldo se verifica antes de marcarlo como exitoso: el ZIP debe existir, tener tamaño mayor a cero, abrirse correctamente, pasar `testzip()` y contener un `manifest.json` coherente con el proyecto, hash y conteo de archivos. Si esta verificacion falla, se registra `ERROR_ZIP`.

La columna `Progreso` se calcula con datos reales:

```text
proyectos procesados / proyectos totales
```

Ejemplo:

```text
18 / 45 = 40%
```

Si un grupo no esta ejecutando respaldo, `Estado` y `Progreso` muestran `-`.

La auditoria del respaldo manual masivo registra `MANUAL_BACKUP_STARTED`, `MANUAL_BACKUP_GROUP_STARTED`, `MANUAL_BACKUP_GROUP_FINISHED`, `MANUAL_BACKUP_GROUP_ERROR` y `MANUAL_BACKUP_FINISHED`.

## Flujo de vigilancia

El watcher crea un observer por cada grupo activo y publica `FileChangedEvent` en el bus interno. El servicio de directorios vigilados recibe el evento, identifica o crea el proyecto afectado, lo marca como pendiente y lo agrega a la cola en memoria sin duplicados.

El scheduler revisa la cola por intervalos de grupo. Si el ultimo cambio aun no cumple `stabilization_minutes`, deja el proyecto pendiente. Cuando ya esta estable, registra `Proyecto listo para respaldo` y delega al servicio de respaldo.

El servicio de respaldo:

- resuelve el subdirectorio real desde `root_directory` y `relative_path`;
- carga `automagic_ignore` si existe;
- calcula hash SHA-256 usando rutas relativas normalizadas y bytes de archivos incluidos;
- omite el ZIP si no hay cambios efectivos;
- crea el ZIP si el hash cambio;
- registra `BackupHistory` y `AuditLog`;
- actualiza `WatchedDirectory`.
- aplica retencion despues de `BACKUP_OK`.

## Retencion

La retencion se aplica por grupo y por proyecto, nunca de forma global.

Regla principal:

```text
backups_to_keep tiene precedencia sobre days_to_keep
```

Esto significa:

- siempre se conservan los N respaldos mas recientes;
- si hay respaldos fuera de ese conjunto protegido, pueden eliminarse;
- un respaldo fuera del conjunto protegido se marca por antiguedad si supera `days_to_keep`;
- si no supera `days_to_keep`, se elimina por cantidad;
- nunca se borra por antiguedad si eso deja menos de `backups_to_keep`.

Ejemplo por cantidad:

```text
backups_to_keep = 10
days_to_keep = 30
hay 11 respaldos

Resultado: se elimina el mas antiguo para conservar los 10 mas recientes.
```

Ejemplo de precedencia:

```text
backups_to_keep = 6
days_to_keep = 365
hay 4 respaldos con mas de 365 dias

Resultado: se conservan los 4 porque estan dentro del conjunto protegido.
```

El ZIP se borra fisicamente, pero `BackupHistory` no se borra. En su lugar se actualizan:

- `retained = false`
- `deleted_at`
- `deletion_reason`

La columna `Disponible` del historial muestra:

- `Si`: el ZIP sigue disponible;
- `No - cantidad`: eliminado por exceder la cantidad conservada;
- `No - antiguedad`: eliminado por antiguedad fuera del conjunto protegido.

## Restauracion

La restauracion se ejecuta desde la TUI en el menu `Restaurar`:

```text
Grupo -> Proyecto -> Version -> Resumen -> Confirmar restauracion -> Resultado
```

Solo se muestran versiones de `BackupHistory` con:

- `status = BACKUP_OK`
- `retained = true`

Si el archivo ZIP fisico ya no existe, la version aparece como `NO DISPONIBLE` y no se permite restaurarla.

Antes de restaurar se valida:

- el ZIP existe;
- el ZIP puede abrirse completo;
- `manifest.json` existe dentro del directorio raiz del proyecto;
- `manifest.json` es JSON valido;
- el manifest contiene `program`, `version`, `group`, `relative_path`, `content_hash`, `backup_time`, `rolling_counter` y `file_count`;
- el grupo del manifest coincide con el grupo seleccionado;
- el `relative_path` del manifest coincide con el proyecto seleccionado;
- todas las rutas del ZIP pertenecen al directorio raiz esperado.

La extraccion se realiza directamente en `root_directory`, de modo que el resultado sea:

```text
root_directory/ProyectoA/
```

y no:

```text
root_directory/ProyectoA/ProyectoA/
```

Si `ProyectoA` ya existe, nunca se sobrescribe. Primero se renombra a:

```text
ProyectoA.cucho_YYYYMMDD_HHMMSS
```

Ejemplo:

```text
ProyectoA.cucho_20260624_160203
```

Luego se extrae la version seleccionada como un nuevo `ProyectoA/`. La carpeta renombrada queda intacta para revision manual.

Ejemplo de restauracion:

```text
Grupo: Scripts Python
Proyecto: ProyectoA
Version: ProyectoA_20260624_145632_R007.zip
Resultado:
root_directory/ProyectoA/
```

La auditoria registra `RESTORE_STARTED`, `RESTORE_OK`, `RESTORE_ABORTED` o `RESTORE_ERROR` con grupo, proyecto, respaldo, motivo y duracion. Cada restauracion exitosa actualiza `restore_count` y `last_restored_at` en `backup_history`.

## automagic_ignore

Cada proyecto puede incluir un archivo llamado `automagic_ignore` en su raiz. Sintaxis soportada:

- lineas vacias y lineas con `#` inicial se ignoran;
- patrones glob como `*.pyc`;
- directorios terminados en `/`, como `.venv/`;
- negacion con `!`, como `!important.log`.

Ejemplo:

```text
__pycache__/
*.pyc
.venv/
node_modules/
*.log
!important.log
```

Las reglas se aplican a rutas relativas dentro del proyecto respaldado.

## Respaldos ZIP

Nombre de archivo:

```text
<subdirectorio>_<YYYYMMDD_HHMMSS>_R000.zip
```

Ejemplo:

```text
ProyectoA_20260624_145632_R000.zip
```

Destino:

```text
destination_directory/<nombre_del_grupo>/<subdirectorio>/
```

Estructura interna:

```text
ProyectoA/
    archivo.py
    src/main.py
    manifest.json
```

Ejemplo de `manifest.json`:

```json
{
  "program": "RespaldosAutomagicos",
  "version": "1.0",
  "group": {
    "id": 1,
    "name": "Scripts Python"
  },
  "group_id": 1,
  "group_name": "Scripts Python",
  "root_directory": "C:/Scripts",
  "relative_path": "ProyectoA",
  "backup_name": "ProyectoA_20260624_145632_R000.zip",
  "backup_time": "2026-06-24T14:56:32+00:00",
  "rolling_counter": 0,
  "content_hash": "...",
  "file_count": 12,
  "compression_level": 6
}
```

## Tablas iniciales

- `backup_groups`: grupos de respaldo con rutas, intervalos, retencion y compresion.
- `watched_directories`: subdirectorios vigilados por grupo y su estado operativo.
- `backup_history`: historial de respaldos generados o intentados, disponibilidad, retencion y metadatos de restauracion.
- `audit_logs`: eventos de auditoria asociados a grupos y subdirectorios.

## Siguientes pasos

1. Agregar configuracion global editable.
2. Preparar integracion con Task Scheduler.
3. Pulir metricas, empaquetado e instalador.
# RespaldosAutomagicos
