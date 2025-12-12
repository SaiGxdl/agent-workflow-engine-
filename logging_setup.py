# logging_setup.py
import logging
from logging import StreamHandler, Formatter

def configure_logging(level: str = "INFO"):
    handler = StreamHandler()
    handler.setFormatter(Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # remove default handlers then add ours
    root.handlers = []
    root.addHandler(handler)
