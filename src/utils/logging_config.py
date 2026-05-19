import datetime
import logging
import sys
from pathlib import Path


_FMT = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def setup_logging(level: int = logging.INFO, log_dir: str | None = None) -> None:
    """Configure root logger.

    Parameters
    ----------
    level : logging level (default INFO)
    log_dir : if given, also write to a daily log file at
              <log_dir>/YYYY-MM-DD.log (appended across restarts).
    """
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(_FMT)
        root.addHandler(stdout_handler)

    if log_dir is not None:
        today = datetime.date.today().strftime("%Y-%m-%d")
        daily_dir = Path(log_dir) / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        
        log_path = daily_dir / f"{today}.log"
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(_FMT)
        # Avoid duplicate file handlers if setup_logging is called more than once
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path.resolve())
                   for h in root.handlers):
            root.addHandler(file_handler)
            logging.getLogger(__name__).info("Logging to %s", log_path)
