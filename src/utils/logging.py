import logging
import queue
import sys
import threading
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

from src.settings import settings


def get_queue_logger(
    file_path: Path = settings.log_path,
    app_name: str = "reverse_proxy",
    file_limit: int = 1024 * 1024 * 100,  # 100MB
    rollover_limit: int = 10,
    queue_size: int = 10000,
) -> tuple[logging.Logger, QueueListener]:
    """
    Sets up a queue-based logger for asynchronous logging.

    Args:
        file_path (Path): Directory path for log files
        app_name (str): Name of the application/logger
        file_limit (int): Maximum size of each log file before rotation
        rollover_limit (int): Number of backup files to keep

    Returns:
        tuple[logging.Logger, QueueListener]: Configured logger and its queue listener
    """
    file_path.mkdir(parents=True, exist_ok=True)

    logger: logging.Logger = logging.getLogger(app_name)
    level: str = ("debug" if settings.debug else "info").upper()
    logger.setLevel(level)

    log_format: str = "%(name)s - %(asctime)s - %(funcName)s - %(levelname)s - %(message)s"
    formatter: logging.Formatter = logging.Formatter(log_format)

    # Create handlers
    handlers: list[logging.Handler] = []

    if settings.log_to_file:
        # File handler
        main_handler = RotatingFileHandler(
            filename=f"{str(file_path)}/{app_name}.log",
            maxBytes=file_limit,
            backupCount=rollover_limit,
            encoding="utf-8",
        )
        main_handler.setFormatter(formatter)
        handlers.append(main_handler)

        # Error log file handler
        error_handler = RotatingFileHandler(
            filename=f"{str(file_path)}/{app_name}_err.log",
            maxBytes=file_limit,
            backupCount=rollover_limit,
            encoding="utf-8",
        )
        error_handler.setFormatter(formatter)
        error_handler.setLevel(logging.ERROR)
        handlers.append(error_handler)

    # Stream log handler - will stream logs to stdout
    if settings.stream_stdout:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    # Setup queue
    log_queue: queue.Queue = queue.Queue(queue_size)
    queue_handler = QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    # Setup listener
    listener = QueueListener(
        log_queue,
        *handlers,
        respect_handler_level=True,  # Ensures handlers only receive records they're configured for
    )
    listener.start()  # Spawns new thread for logging operations

    print(f"Initialized queue logger on {level} level")
    print(f"Active threads: {[t.name for t in threading.enumerate()]}")

    return logger, listener


def get_app_logger(app_name: str = "app") -> logging.Logger:
    """
    Returns a logger for the application. Used to fetch single queue logger for the application.
    """
    return logging.getLogger(app_name)
