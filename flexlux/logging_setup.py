import os
import platform
import logging
from logging.handlers import RotatingFileHandler


def setup_logging():
    system = platform.system()
    if system == "Windows":
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FlexLux")
    elif system == "Darwin":
        log_dir = os.path.expanduser("~/Library/Logs")
    else:
        log_dir = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "FlexLux")
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("FlexLux")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(os.path.join(log_dir, "FlexLux.log"), maxBytes=1_000_000, backupCount=1)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
