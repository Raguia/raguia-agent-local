from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(app_data_dir: Path, *, level: str = "INFO", structured: bool = True) -> Path:
    app_data_dir.mkdir(parents=True, exist_ok=True)
    log_path = app_data_dir / "agent.log"
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    if structured:
        file_handler.setFormatter(JsonFormatter())
    else:
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console)
    return log_path


def export_support_bundle(app_data_dir: Path, output_zip: Path, doctor_summary: str) -> Path:
    app_data_dir.mkdir(parents=True, exist_ok=True)
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_zip, "w", compression=ZIP_DEFLATED) as zf:
        for p in sorted(app_data_dir.glob("agent.log*")):
            if p.is_file():
                zf.write(p, arcname=f"logs/{p.name}")
        report = app_data_dir / "doctor_latest.txt"
        if report.is_file():
            zf.write(report, arcname="doctor_latest.txt")
        zf.writestr("doctor_now.txt", doctor_summary)
    return output_zip

