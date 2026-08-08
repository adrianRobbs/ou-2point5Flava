import json
import logging
import sys
import time
import uuid


class JsonLineFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "run_id": self._run_id,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> str:
    run_id = uuid.uuid4().hex[:12]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter(run_id))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    return run_id
