from typing import Union
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler


def setup_logging(
        log_dir: Union[str, Path],
        log_file: str = "app.log",
        max_log_file_size: int = 5 * 1024 * 1024,
        backup_log_count: int = 5
        ):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / log_file

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_log_file_size,
        backupCount=backup_log_count,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
