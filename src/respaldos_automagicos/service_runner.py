"""Background service runner used by Windows Task Scheduler."""

from threading import Event

from respaldos_automagicos.app import create_app
from respaldos_automagicos.logging_config import get_logger


def run_background_service(stop_event: Event | None = None) -> None:
    """Run watcher and scheduler services until stopped."""
    app = create_app()
    app.initialize_storage()
    logger = get_logger("service")
    resolved_stop_event = stop_event or Event()
    app.watcher_service.start()
    app.scheduler_service.start()
    logger.info("Servicio de respaldos iniciado")
    try:
        resolved_stop_event.wait()
    finally:
        app.scheduler_service.stop()
        app.watcher_service.stop()
        logger.info("Servicio de respaldos detenido")
