"""Module entrypoint for RespaldosAutomagicos."""

import argparse
import signal
from threading import Event

from respaldos_automagicos.app import create_app
from respaldos_automagicos.audit.service import AuditService
from respaldos_automagicos.controllers.task_scheduler import TaskSchedulerController
from respaldos_automagicos.service_runner import run_background_service
from respaldos_automagicos.task_scheduler.service import TaskSchedulerService
from respaldos_automagicos.tui.app import main


def cli() -> None:
    """Run the requested command or start the TUI by default."""
    parser = argparse.ArgumentParser(prog="respaldos_automagicos")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run-service")
    resume_parser = subparsers.add_parser("task-resume")
    resume_parser.add_argument("--run-now", action="store_true")
    args = parser.parse_args()

    if args.command == "run-service":
        stop_event = Event()
        signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
        run_background_service(stop_event)
    elif args.command == "task-resume":
        app = create_app()
        app.initialize_storage()
        controller = TaskSchedulerController(
            task_scheduler_service=TaskSchedulerService(),
            audit_service=AuditService(app.session_factory),
        )
        controller.resume_from_task(run_now=args.run_now)
    else:
        main()


if __name__ == "__main__":
    cli()
