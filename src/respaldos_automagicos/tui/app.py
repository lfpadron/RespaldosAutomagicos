"""Textual user interface for RespaldosAutomagicos."""

from collections.abc import Callable
from datetime import datetime

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Static

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.audit.service import AuditService
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.controllers import (
    AuditController,
    BackupGroupFormData,
    ConfigController,
    GroupController,
    GroupSelectionState,
    GroupValidationError,
    HistoryController,
    ManualBackupJobController,
    RestoreController,
    RestoreControllerError,
    TaskSchedulerActionResult,
    TaskSchedulerController,
    TaskSchedulerControllerError,
)
from respaldos_automagicos.task_scheduler.service import TaskSchedulerService
from respaldos_automagicos.utils.time import (
    DEFAULT_TIMEZONE,
    format_local_datetime,
)


class RespaldosAutomagicosTUI(App[None]):
    """Main Textual application."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #title {
        text-style: bold;
        text-align: center;
        width: 100%;
        margin: 1 0;
    }

    #main-menu {
        height: auto;
        padding: 0 1;
    }

    .panel {
        height: 1fr;
        padding: 1 2;
    }

    .hidden {
        display: none;
    }

    .toolbar {
        height: auto;
        margin-bottom: 1;
    }

    .form-row {
        height: auto;
        margin-bottom: 1;
    }

    DataTable {
        height: 1fr;
        width: 100%;
    }

    Input {
        width: 60;
    }

    #status {
        height: 1;
        padding: 0 2;
    }
    """

    BINDINGS = [("q", "quit", "Salir")]

    def __init__(
        self,
        settings: AppSettings | None = None,
        core_app: RespaldosAutomagicosApplication | None = None,
    ) -> None:
        """Create the TUI with optional injected settings."""
        self.core_app = core_app or create_app(settings or AppSettings())
        self.settings = self.core_app.settings
        self.core_app.initialize_storage()
        self.group_controller = GroupController(
            session_factory=self.core_app.session_factory,
            backup_service=self.core_app.backup_service,
            watcher_service=self.core_app.watcher_service,
        )
        self.history_controller = HistoryController(self.core_app.session_factory)
        self.audit_controller = AuditController(self.core_app.session_factory)
        self.restore_controller = RestoreController(
            session_factory=self.core_app.session_factory,
        )
        self.manual_backup_controller = ManualBackupJobController(
            session_factory=self.core_app.session_factory,
            backup_service=self.core_app.backup_service,
        )
        self.config_controller = ConfigController(
            settings=self.settings,
            session_factory=self.core_app.session_factory,
        )
        self.task_scheduler_controller = TaskSchedulerController(
            task_scheduler_service=TaskSchedulerService(),
            audit_service=AuditService(self.core_app.session_factory),
        )
        self.group_selection = GroupSelectionState()
        self._group_ids: list[int] = []
        self._restore_group_ids: list[int] = []
        self._restore_project_ids: list[int] = []
        self._restore_version_ids: list[int] = []
        self._editing_group_id: int | None = None
        self._pending_delete_group_ids: tuple[int, ...] | None = None
        self._history_desc = True
        super().__init__()

    def compose(self) -> ComposeResult:
        """Compose the application."""
        yield Header(show_clock=True)
        yield Static("RespaldosAutomágicos", id="title")
        with Horizontal(id="main-menu"):
            yield Button("1. Grupos de respaldo", id="menu-groups")
            yield Button("2. Restaurar", id="menu-restore")
            yield Button("3. Historial", id="menu-history")
            yield Button("4. Auditoría", id="menu-audit")
            yield Button("5. Configuración", id="menu-config")
            yield Button("6. Acerca de", id="menu-about")
            yield Button("Q Salir", id="menu-exit")
        yield Static("", id="status")
        yield from self._groups_panel()
        yield from self._restore_panel()
        yield from self._history_panel()
        yield from self._audit_panel()
        yield from self._config_panel()
        yield from self._about_panel()
        yield Footer()

    def on_mount(self) -> None:
        """Refresh visible data when the TUI starts."""
        self._show_panel("groups-panel")
        self.refresh_groups()
        self.refresh_restore_groups()
        self.refresh_history()
        self.refresh_audit()
        self.refresh_config()
        self.set_interval(1, self.refresh_groups)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button actions."""
        button_id = event.button.id or ""
        if button_id == "menu-exit":
            self.exit()
        elif button_id.startswith("menu-"):
            self._handle_menu(button_id)
        elif button_id == "group-new":
            self._open_group_form(None)
        elif button_id == "group-edit":
            self._open_group_form(self._selected_group_id())
        elif button_id == "group-delete":
            self._delete_selected_group()
        elif button_id == "group-toggle":
            self._toggle_selected_group()
        elif button_id == "group-duplicate":
            self._duplicate_selected_group()
        elif button_id == "group-scan":
            self._scan_selected_group()
        elif button_id == "group-backup":
            self._backup_selected_group()
        elif button_id == "group-select-all":
            self._select_all_groups()
        elif button_id == "group-clear-selection":
            self._clear_group_selection()
        elif button_id == "form-save":
            self._save_group_form()
        elif button_id == "form-cancel":
            self._show_group_list()
        elif button_id == "history-refresh":
            self.refresh_history()
        elif button_id == "history-sort":
            self._history_desc = not self._history_desc
            self.refresh_history()
        elif button_id == "restore-refresh":
            self.refresh_restore_groups()
        elif button_id == "restore-load-projects":
            self._load_restore_projects()
        elif button_id == "restore-load-versions":
            self._load_restore_versions()
        elif button_id == "restore-summary-action":
            self._show_restore_summary()
        elif button_id == "restore-run":
            self._restore_selected_version()
        elif button_id == "audit-refresh":
            self.refresh_audit()
        elif button_id == "config-refresh":
            self.refresh_config()
        elif button_id == "task-enable-boot":
            self._activate_task_on_boot()
        elif button_id == "task-run-now":
            self._activate_task_now()
        elif button_id == "task-pause-30":
            self._disable_task_for_minutes(30)
        elif button_id == "task-pause-60":
            self._disable_task_for_minutes(60)
        elif button_id == "task-pause-180":
            self._disable_task_for_minutes(180)
        elif button_id == "task-pause-hours":
            self._disable_task_for_custom_hours()
        elif button_id == "task-pause-boot":
            self._disable_task_until_boot()
        elif button_id == "task-disable":
            self._disable_task()

    def on_key(self, event: events.Key) -> None:
        """Handle group selection keyboard shortcuts."""
        groups_panel = self.query_one("#groups-panel")
        if not groups_panel.has_class("hidden") and event.key == "ctrl+a":
            self._select_all_groups()
            event.stop()
            return
        if not groups_panel.has_class("hidden") and event.key == "ctrl+l":
            self._clear_group_selection()
            event.stop()
            return
        if event.key == "space" and self.focused is self.query_one(
            "#groups-table",
            DataTable,
        ):
            self._toggle_highlighted_group_selection()
            event.stop()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle group selection with Enter on the groups table."""
        if event.data_table.id == "groups-table":
            self._toggle_group_selection_at_row(event.cursor_row)
            event.stop()

    def refresh_groups(self) -> None:
        """Refresh the backup groups table."""
        table = self.query_one("#groups-table", DataTable)
        previous_row = table.cursor_row
        previous_column = table.cursor_column
        previous_group_id = (
            self._group_ids[previous_row]
            if 0 <= previous_row < len(self._group_ids)
            else None
        )
        table.clear()
        groups = self.group_controller.list_groups()
        self._group_ids = [group.id for group in groups]
        self.group_selection.selected_ids.intersection_update(self._group_ids)
        progress_by_group = self.manual_backup_controller.snapshot()
        for group in groups:
            progress = progress_by_group.get(group.id)
            table.add_row(
                _selection_marker(self.group_selection.is_selected(group.id)),
                group.name,
                "Sí" if group.enabled else "No",
                group.timezone,
                group.root_directory,
                group.destination_directory,
                str(group.project_count),
                str(group.pending_count),
                _format_dt(group.last_backup_at, group.timezone),
                _format_dt(group.next_scan_at, group.timezone),
                progress.state.value if progress is not None else "-",
                f"{progress.progress_percent}%" if progress is not None else "-",
            )
        if not groups:
            table.add_row("-", "-", "-", "-", "-", "-", "0", "0", "-", "-", "-", "-")
        if groups:
            if previous_group_id in self._group_ids:
                next_row = self._group_ids.index(previous_group_id)
            elif previous_row >= 0:
                next_row = min(previous_row, len(groups) - 1)
            else:
                next_row = 0
            table.move_cursor(
                row=next_row,
                column=max(previous_column, 0),
                animate=False,
            )

    def refresh_restore_groups(self) -> None:
        """Refresh restore group options."""
        table = self.query_one("#restore-groups-table", DataTable)
        table.clear()
        groups = self.restore_controller.list_groups()
        self._restore_group_ids = [group.id for group in groups]
        for group in groups:
            table.add_row(str(group.id), group.name)
        if not groups:
            table.add_row("-", "-")
        self._clear_restore_projects()
        self._clear_restore_versions()
        self.query_one("#restore-confirm", Checkbox).value = False
        self.query_one("#restore-summary", Static).update("")

    def refresh_history(self) -> None:
        """Refresh the backup history table."""
        table = self.query_one("#history-table", DataTable)
        table.clear()
        group_filter = self.query_one("#history-group-filter", Input).value.strip()
        group_id = int(group_filter) if group_filter.isdigit() else None
        rows = self.history_controller.list_history(group_id=group_id)
        if not self._history_desc:
            rows = list(reversed(rows))
        for row in rows:
            table.add_row(
                _format_dt(row.backup_time, row.timezone),
                row.group_name,
                row.project_name,
                row.status,
                str(row.duration_ms or 0),
                str(row.backup_size_bytes or 0),
                (row.content_hash or "-")[:12],
                _availability_label(row.retained, row.deletion_reason),
            )

    def refresh_audit(self) -> None:
        """Refresh the audit table."""
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        group_filter = self.query_one("#audit-group-filter", Input).value.strip()
        action = self.query_one("#audit-action-filter", Input).value.strip() or None
        result = self.query_one("#audit-result-filter", Input).value.strip() or None
        group_id = int(group_filter) if group_filter.isdigit() else None
        for row in self.audit_controller.list_audit(
            group_id=group_id,
            action=action,
            result=result,
        ):
            table.add_row(
                _format_dt(row.timestamp, row.timezone),
                str(row.group_id or "-"),
                str(row.watched_directory_id or "-"),
                row.action,
                row.result,
                row.details or "-",
            )

    def refresh_config(self) -> None:
        """Refresh the read-only configuration table."""
        table = self.query_one("#config-table", DataTable)
        table.clear()
        summary = self.config_controller.summary()
        table.add_row("Ruta SQLite", summary.database_url)
        table.add_row("Versión", summary.version)
        table.add_row("Python", summary.python_version)
        table.add_row("uv", summary.uv_version)
        table.add_row("Número de grupos", str(summary.group_count))
        table.add_row("Número de proyectos", str(summary.project_count))
        table.add_row("Número de respaldos", str(summary.backup_count))

    def _groups_panel(self) -> ComposeResult:
        with Vertical(classes="panel", id="groups-panel"):
            yield Static("Grupos de respaldo")
            with Horizontal(classes="toolbar"):
                yield Button("Seleccionar todos", id="group-select-all")
                yield Button("Limpiar selección", id="group-clear-selection")
                yield Button("Crear", id="group-new")
                yield Button("Editar", id="group-edit")
                yield Button("Eliminar", id="group-delete")
                yield Button("Activar/Desactivar", id="group-toggle")
                yield Button("Duplicar grupo", id="group-duplicate")
                yield Button("Escanear proyectos", id="group-scan")
                yield Button("Respaldar ahora", id="group-backup")
                yield Button("Forzar respaldo", id="group-force-backup")
            groups_table: DataTable[str] = DataTable(id="groups-table")
            groups_table.cursor_type = "row"
            groups_table.add_columns(
                "Seleccionado",
                "Nombre",
                "Activo",
                "Zona horaria",
                "Raíz",
                "Destino",
                "Número de proyectos",
                "Pendientes",
                "Último respaldo",
                "Próximo escaneo",
                "Estado",
                "Progreso",
            )
            yield groups_table
            yield from self._group_form()

    def _group_form(self) -> ComposeResult:
        with Vertical(classes="hidden", id="group-form"):
            yield Static("Grupo de respaldo")
            yield Input(placeholder="Nombre", id="group-name")
            yield Input(placeholder="Directorio raíz", id="group-root")
            yield Input(placeholder="Directorio destino", id="group-destination")
            yield Input(
                placeholder="Zona horaria (ej. America/Mexico_City)",
                id="group-timezone",
            )
            yield Input(placeholder="Intervalo de escaneo", id="group-scan-interval")
            yield Input(
                placeholder="Tiempo de estabilización",
                id="group-stabilization",
            )
            yield Input(placeholder="Respaldos a conservar", id="group-keep")
            yield Input(placeholder="Días de conservación", id="group-days")
            yield Input(placeholder="Nivel de compresión", id="group-compression")
            yield Checkbox("Activo", id="group-enabled")
            with Horizontal(classes="toolbar"):
                yield Button("Guardar", id="form-save", variant="primary")
                yield Button("Cancelar", id="form-cancel")

    def _restore_panel(self) -> ComposeResult:
        with Vertical(classes="panel hidden", id="restore-panel"):
            yield Static("Restaurar")
            with Horizontal(classes="toolbar"):
                yield Button("Actualizar", id="restore-refresh")
                yield Button("Cargar proyectos", id="restore-load-projects")
                yield Button("Cargar versiones", id="restore-load-versions")
                yield Button("Resumen", id="restore-summary-action")
                yield Button("Restaurar", id="restore-run", variant="warning")
            yield Static("Grupo")
            groups_table: DataTable[str] = DataTable(id="restore-groups-table")
            groups_table.add_columns("ID", "Nombre")
            yield groups_table
            yield Static("Proyecto")
            projects_table: DataTable[str] = DataTable(id="restore-projects-table")
            projects_table.add_columns("ID", "Proyecto", "Estado")
            yield projects_table
            yield Static("Versión")
            versions_table: DataTable[str] = DataTable(id="restore-versions-table")
            versions_table.add_columns(
                "Fecha",
                "Rxxx",
                "Hash corto",
                "Tamaño",
                "Archivos",
                "Disponible",
            )
            yield versions_table
            yield Checkbox("Confirmar restauracion", id="restore-confirm")
            yield Static("", id="restore-summary")

    def _history_panel(self) -> ComposeResult:
        with Vertical(classes="panel hidden", id="history-panel"):
            yield Static("Historial")
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="ID de grupo", id="history-group-filter")
                yield Button("Actualizar", id="history-refresh")
                yield Button("Orden fecha", id="history-sort")
            history_table: DataTable[str] = DataTable(id="history-table")
            history_table.add_columns(
                "Fecha",
                "Grupo",
                "Proyecto",
                "Resultado",
                "Duración",
                "Tamaño",
                "Hash abreviado",
                "Disponible",
            )
            yield history_table

    def _audit_panel(self) -> ComposeResult:
        with Vertical(classes="panel hidden", id="audit-panel"):
            yield Static("Auditoría")
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="ID de grupo", id="audit-group-filter")
                yield Input(placeholder="Acción", id="audit-action-filter")
                yield Input(placeholder="Resultado", id="audit-result-filter")
                yield Button("Actualizar", id="audit-refresh")
            audit_table: DataTable[str] = DataTable(id="audit-table")
            audit_table.add_columns(
                "Fecha",
                "Grupo",
                "Directorio",
                "Acción",
                "Resultado",
                "Detalles",
            )
            yield audit_table

    def _config_panel(self) -> ComposeResult:
        with Vertical(classes="panel hidden", id="config-panel"):
            yield Static("Configuración")
            yield Button("Actualizar", id="config-refresh")
            config_table: DataTable[str] = DataTable(id="config-table")
            config_table.add_columns("Campo", "Valor")
            yield config_table
            yield Static("Task Scheduler")
            with Horizontal(classes="toolbar"):
                yield Button("Activar al iniciar sesión", id="task-enable-boot")
                yield Button("Activar ahora", id="task-run-now")
                yield Button("Desactivar 30 min", id="task-pause-30")
                yield Button("Desactivar 1 hora", id="task-pause-60")
                yield Button("Desactivar 3 horas", id="task-pause-180")
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Horas", id="task-pause-hours-input")
                yield Button("Desactivar N horas", id="task-pause-hours")
                yield Button("Hasta siguiente inicio de sesión", id="task-pause-boot")
                yield Button("Desactivar", id="task-disable", variant="warning")

    def _about_panel(self) -> ComposeResult:
        with Vertical(classes="panel hidden", id="about-panel"):
            yield Static("Acerca de")
            yield Static("RespaldosAutomágicos")
            yield Static(f"Versión {self.settings.app_version}")
            yield Static("Proyecto inicializado correctamente.")

    def _handle_menu(self, button_id: str) -> None:
        panel_by_button = {
            "menu-groups": "groups-panel",
            "menu-restore": "restore-panel",
            "menu-history": "history-panel",
            "menu-audit": "audit-panel",
            "menu-config": "config-panel",
            "menu-about": "about-panel",
        }
        panel_id = panel_by_button.get(button_id)
        if panel_id is not None:
            self._show_panel(panel_id)
        if button_id == "menu-restore":
            self.refresh_restore_groups()
        elif button_id == "menu-history":
            self.refresh_history()
        elif button_id == "menu-audit":
            self.refresh_audit()
        elif button_id == "menu-config":
            self.refresh_config()

    def _show_panel(self, panel_id: str) -> None:
        for current_panel_id in (
            "groups-panel",
            "restore-panel",
            "history-panel",
            "audit-panel",
            "config-panel",
            "about-panel",
        ):
            panel = self.query_one(f"#{current_panel_id}")
            if current_panel_id == panel_id:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")
        if panel_id == "groups-panel":
            self._show_group_list()

    def _show_group_list(self) -> None:
        self.query_one("#groups-table").remove_class("hidden")
        self.query_one("#group-form").add_class("hidden")
        self._editing_group_id = None

    def _open_group_form(self, group_id: int | None) -> None:
        if group_id is None:
            self._editing_group_id = None
            self._set_form_data(
                BackupGroupFormData(
                    name="",
                    root_directory="",
                    destination_directory="",
                    timezone=DEFAULT_TIMEZONE,
                    scan_interval_minutes=15,
                    stabilization_minutes=5,
                    backups_to_keep=10,
                    days_to_keep=30,
                    compression_level=6,
                    enabled=True,
                )
            )
        else:
            try:
                self._set_form_data(self.group_controller.get_form_data(group_id))
                self._editing_group_id = group_id
            except GroupValidationError as exc:
                self._set_status(exc.errors[0])
                return
        self.query_one("#groups-table").add_class("hidden")
        self.query_one("#group-form").remove_class("hidden")

    def _save_group_form(self) -> None:
        try:
            data = self._read_form_data()
            if self._editing_group_id is None:
                self.group_controller.create_group(data)
                self._set_status("Grupo creado.")
            else:
                self.group_controller.update_group(self._editing_group_id, data)
                self._set_status("Grupo actualizado.")
            self.refresh_groups()
            self._show_group_list()
        except GroupValidationError as exc:
            self._set_status(" ".join(exc.errors))
        except ValueError as exc:
            self._set_status(str(exc))

    def _delete_selected_group(self) -> None:
        group_ids = self._selected_action_group_ids()
        if not group_ids:
            self._set_status("Selecciona al menos un grupo.")
            return
        pending_group_ids = tuple(group_ids)
        if self._pending_delete_group_ids != pending_group_ids:
            self._pending_delete_group_ids = pending_group_ids
            self._set_status(
                f"Confirma eliminar {len(group_ids)} grupo(s) presionando Eliminar otra vez."
            )
            return
        try:
            self.group_controller.delete_groups(group_ids)
            self.group_selection.clear()
            self._pending_delete_group_ids = None
            self.refresh_groups()
            self._set_status(f"Grupos eliminados: {len(group_ids)}.")
        except GroupValidationError as exc:
            self._set_status(exc.errors[0])

    def _toggle_selected_group(self) -> None:
        group_ids = self._selected_action_group_ids()
        if not group_ids:
            self._set_status("Selecciona al menos un grupo.")
            return
        try:
            self.group_controller.toggle_groups(group_ids)
            self.refresh_groups()
            self._set_status(f"Grupos actualizados: {len(group_ids)}.")
        except GroupValidationError as exc:
            self._set_status(exc.errors[0])

    def _duplicate_selected_group(self) -> None:
        group_id = self._selected_group_id()
        if group_id is None:
            self._set_status("Selecciona un grupo.")
            return
        try:
            self.group_controller.duplicate_group(group_id)
            self.refresh_groups()
            self._set_status("Grupo duplicado.")
        except GroupValidationError as exc:
            self._set_status(exc.errors[0])

    def _scan_selected_group(self) -> None:
        group_ids = self._selected_action_group_ids()
        if not group_ids:
            self._set_status("Selecciona al menos un grupo.")
            return
        try:
            results = self.group_controller.scan_groups(group_ids)
            self.refresh_groups()
            self._set_status(
                "Escaneo completado: "
                f"{sum(result.created for result in results)} nuevos, "
                f"{sum(result.reactivated for result in results)} reactivados, "
                f"{sum(result.deactivated for result in results)} inactivos."
            )
        except GroupValidationError as exc:
            self._set_status(exc.errors[0])

    def _backup_selected_group(self) -> None:
        group_ids = self._selected_action_group_ids()
        if not group_ids:
            self._set_status("Selecciona al menos un grupo.")
            return
        requested_group_ids = tuple(group_ids)
        self.run_worker(
            lambda: self.manual_backup_controller.run(requested_group_ids),
            name="manual-backup",
            group="manual-backup",
            thread=True,
        )
        self.refresh_groups()
        self._set_status(f"Respaldo manual iniciado para {len(group_ids)} grupo(s).")

    def _load_restore_projects(self) -> None:
        group_id = self._selected_restore_group_id()
        if group_id is None:
            self._set_status("Selecciona un grupo para restaurar.")
            return
        try:
            projects = self.restore_controller.list_projects(group_id)
        except RestoreControllerError as exc:
            self._set_status(str(exc))
            return

        table = self.query_one("#restore-projects-table", DataTable)
        table.clear()
        self._restore_project_ids = [project.id for project in projects]
        for project in projects:
            table.add_row(str(project.id), project.relative_path, project.status)
        if not projects:
            table.add_row("-", "-", "-")
        self._clear_restore_versions()
        self.query_one("#restore-confirm", Checkbox).value = False
        self.query_one("#restore-summary", Static).update("")

    def _load_restore_versions(self) -> None:
        group_id = self._selected_restore_group_id()
        project_id = self._selected_restore_project_id()
        if group_id is None or project_id is None:
            self._set_status("Selecciona grupo y proyecto.")
            return
        versions = self.restore_controller.list_versions(
            group_id=group_id,
            watched_directory_id=project_id,
        )
        table = self.query_one("#restore-versions-table", DataTable)
        table.clear()
        self._restore_version_ids = [version.id for version in versions]
        for version in versions:
            table.add_row(
                _format_dt(version.backup_time, version.timezone),
                version.rolling_label,
                version.short_hash,
                _format_bytes(version.backup_size_bytes),
                str(version.file_count or 0),
                version.availability_label,
            )
        if not versions:
            table.add_row("-", "-", "-", "-", "0", "-")
        self.query_one("#restore-confirm", Checkbox).value = False
        self.query_one("#restore-summary", Static).update("")

    def _show_restore_summary(self) -> None:
        version_id = self._selected_restore_version_id()
        if version_id is None:
            self._set_status("Selecciona una version.")
            return
        try:
            summary = self.restore_controller.summary(version_id)
        except RestoreControllerError as exc:
            self._set_status(str(exc))
            return
        self.query_one("#restore-summary", Static).update(
            "\n".join(
                [
                    f"Grupo: {summary.group_name}",
                    f"Proyecto: {summary.project_name}",
                    f"Respaldo: {summary.backup_name}",
                    f"Fecha: {_format_dt(summary.backup_time, summary.timezone)}",
                    f"Hash: {summary.content_hash or '-'}",
                    f"Tamaño: {_format_bytes(summary.backup_size_bytes)}",
                    f"Archivos: {summary.file_count or 0}",
                    f"Disponible: {summary.availability_label}",
                ]
            )
        )

    def _restore_selected_version(self) -> None:
        version_id = self._selected_restore_version_id()
        if version_id is None:
            self._set_status("Selecciona una version.")
            return
        if not self.query_one("#restore-confirm", Checkbox).value:
            self._set_status("Confirma la restauracion.")
            return
        try:
            result = self.restore_controller.restore(version_id)
        except RestoreControllerError as exc:
            self._set_status(str(exc))
            return
        if result.restored_path is None:
            self._set_status(result.message)
        else:
            self._set_status(f"Restauracion completada: {result.restored_path}.")
        self.query_one("#restore-confirm", Checkbox).value = False
        self._load_restore_versions()
        self.refresh_history()
        self.refresh_audit()

    def _activate_task_on_boot(self) -> None:
        self._run_task_scheduler_action(self.task_scheduler_controller.activate_on_boot)

    def _activate_task_now(self) -> None:
        self._run_task_scheduler_action(self.task_scheduler_controller.activate_now)

    def _disable_task_for_minutes(self, minutes: int) -> None:
        self._run_task_scheduler_action(
            lambda: self.task_scheduler_controller.disable_for_minutes(minutes)
        )

    def _disable_task_for_custom_hours(self) -> None:
        value = self.query_one("#task-pause-hours-input", Input).value.strip()
        if not value:
            self._set_status("Captura cuántas horas desactivar.")
            return
        try:
            hours = int(value)
        except ValueError:
            self._set_status("Las horas deben ser un número entero.")
            return
        self._run_task_scheduler_action(
            lambda: self.task_scheduler_controller.disable_for_hours(hours)
        )

    def _disable_task_until_boot(self) -> None:
        self._run_task_scheduler_action(
            self.task_scheduler_controller.disable_until_next_boot
        )

    def _disable_task(self) -> None:
        self._run_task_scheduler_action(self.task_scheduler_controller.disable)

    def _run_task_scheduler_action(
        self,
        action: Callable[[], TaskSchedulerActionResult],
    ) -> None:
        try:
            result = action()
        except TaskSchedulerControllerError as exc:
            self._set_status(f"Task Scheduler: {exc}")
            self.refresh_audit()
            return
        self._set_status(result.message)
        self.refresh_audit()

    def _clear_restore_projects(self) -> None:
        table = self.query_one("#restore-projects-table", DataTable)
        table.clear()
        self._restore_project_ids = []
        table.add_row("-", "-", "-")

    def _clear_restore_versions(self) -> None:
        table = self.query_one("#restore-versions-table", DataTable)
        table.clear()
        self._restore_version_ids = []
        table.add_row("-", "-", "-", "-", "0", "-")

    def _select_all_groups(self) -> None:
        self.group_selection.select_all(self._group_ids)
        self._pending_delete_group_ids = None
        self.refresh_groups()
        self._set_status(f"Grupos seleccionados: {len(self._group_ids)}.")

    def _clear_group_selection(self) -> None:
        self.group_selection.clear()
        self._pending_delete_group_ids = None
        self.refresh_groups()
        self._set_status("Selección limpia.")

    def _toggle_highlighted_group_selection(self) -> None:
        row = self.query_one("#groups-table", DataTable).cursor_row
        self._toggle_group_selection_at_row(row)

    def _toggle_group_selection_at_row(self, row: int) -> None:
        if row < 0 or row >= len(self._group_ids):
            return
        self.group_selection.toggle(self._group_ids[row])
        self._pending_delete_group_ids = None
        self.refresh_groups()

    def _selected_action_group_ids(self) -> list[int]:
        return self.group_selection.selected_or_fallback(self._selected_group_id())

    def _selected_group_id(self) -> int | None:
        if not self._group_ids:
            return None
        table = self.query_one("#groups-table", DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(self._group_ids):
            return self._group_ids[0]
        return self._group_ids[row]

    def _selected_restore_group_id(self) -> int | None:
        return self._selected_id_from_table(
            self._restore_group_ids, "restore-groups-table"
        )

    def _selected_restore_project_id(self) -> int | None:
        return self._selected_id_from_table(
            self._restore_project_ids,
            "restore-projects-table",
        )

    def _selected_restore_version_id(self) -> int | None:
        return self._selected_id_from_table(
            self._restore_version_ids,
            "restore-versions-table",
        )

    def _selected_id_from_table(self, ids: list[int], table_id: str) -> int | None:
        if not ids:
            return None
        table = self.query_one(f"#{table_id}", DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(ids):
            return ids[0]
        return ids[row]

    def _set_form_data(self, data: BackupGroupFormData) -> None:
        self.query_one("#group-name", Input).value = data.name
        self.query_one("#group-root", Input).value = data.root_directory
        self.query_one("#group-destination", Input).value = data.destination_directory
        self.query_one("#group-timezone", Input).value = data.timezone
        self.query_one("#group-scan-interval", Input).value = str(
            data.scan_interval_minutes
        )
        self.query_one("#group-stabilization", Input).value = str(
            data.stabilization_minutes
        )
        self.query_one("#group-keep", Input).value = str(data.backups_to_keep)
        self.query_one("#group-days", Input).value = str(data.days_to_keep)
        self.query_one("#group-compression", Input).value = str(data.compression_level)
        self.query_one("#group-enabled", Checkbox).value = data.enabled

    def _read_form_data(self) -> BackupGroupFormData:
        return BackupGroupFormData(
            name=self.query_one("#group-name", Input).value,
            root_directory=self.query_one("#group-root", Input).value,
            destination_directory=self.query_one("#group-destination", Input).value,
            timezone=self.query_one("#group-timezone", Input).value,
            scan_interval_minutes=self._read_int("group-scan-interval"),
            stabilization_minutes=self._read_int("group-stabilization"),
            backups_to_keep=self._read_int("group-keep"),
            days_to_keep=self._read_int("group-days"),
            compression_level=self._read_int("group-compression"),
            enabled=self.query_one("#group-enabled", Checkbox).value,
        )

    def _read_int(self, field_id: str) -> int:
        value = self.query_one(f"#{field_id}", Input).value.strip()
        if not value:
            raise ValueError("Todos los campos numéricos son obligatorios.")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("Los campos numéricos deben contener enteros.") from exc

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)


def _format_dt(value: datetime | None, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    return format_local_datetime(value, timezone_name)


def _selection_marker(selected: bool) -> str:
    return "X" if selected else ""


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "0 B"
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _availability_label(retained: bool, deletion_reason: str | None) -> str:
    if retained:
        return "Sí"
    if deletion_reason == "RETENTION_BY_COUNT":
        return "No - cantidad"
    if deletion_reason == "RETENTION_BY_AGE":
        return "No - antigüedad"
    return "No"


def main() -> None:
    """Run the Textual TUI."""
    RespaldosAutomagicosTUI().run()
