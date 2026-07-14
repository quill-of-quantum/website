import os
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

try:
    from smbus2 import SMBus
except ImportError:
    try:
        from smbus import SMBus  # type: ignore
    except ImportError:
        SMBus = None  # type: ignore

bp = Blueprint("sensor", __name__)

SENSOR_DIR = "/home/bbdwz/projects/website/data/sensor"
LOG_PATH = os.path.join(SENSOR_DIR, "sgp30.log")

I2C_BUS = 1
I2C_ADDR = 0x58
LOG_INTERVAL_SEC = 10

# SGP30 commands
CMD_INIT_AIR_QUALITY = 0x2003
CMD_MEASURE_AIR_QUALITY = 0x2008
CMD_MEASURE_RAW = 0x2050

_latest_lock = threading.Lock()
_latest = {
    "ok": False,
    "ts": None,
    "eco2": None,
    "tvoc": None,
    "raw_h2": None,
    "raw_ethanol": None,
    "error": "not started",
}

_logger_thread = None
_stop_event = threading.Event()


def _crc8(data):
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _write_cmd(bus, addr, cmd):
    bus.write_i2c_block_data(addr, (cmd >> 8) & 0xFF, [cmd & 0xFF])


def _read_words(bus, addr, n_words):
    raw = bus.read_i2c_block_data(addr, 0x00, n_words * 3)
    words = []
    for i in range(n_words):
        b1, b2, crc = raw[i * 3 : i * 3 + 3]
        if _crc8([b1, b2]) != crc:
            raise ValueError("CRC mismatch while reading from SGP30")
        words.append((b1 << 8) | b2)
    return words


def _ensure_log_header():
    os.makedirs(SENSOR_DIR, exist_ok=True)
    if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) == 0:
        with open(LOG_PATH, "a") as f:
            f.write("timestamp,eco2_ppm,tvoc_ppb,raw_h2,raw_ethanol\n")


def _log_line(ts, eco2, tvoc, raw_h2, raw_ethanol):
    _ensure_log_header()
    line = f"{ts},{eco2},{tvoc},{raw_h2},{raw_ethanol}\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)


def _update_latest(data):
    with _latest_lock:
        _latest.update(data)


def _sensor_loop():
    if SMBus is None:
        _update_latest({"ok": False, "error": "smbus2/smbus not installed", "ts": None})
        return

    while not _stop_event.is_set():
        try:
            with SMBus(I2C_BUS) as bus:
                _write_cmd(bus, I2C_ADDR, CMD_INIT_AIR_QUALITY)
                time.sleep(0.01)

                while not _stop_event.is_set():
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    _write_cmd(bus, I2C_ADDR, CMD_MEASURE_AIR_QUALITY)
                    time.sleep(0.012)
                    eco2, tvoc = _read_words(bus, I2C_ADDR, 2)

                    _write_cmd(bus, I2C_ADDR, CMD_MEASURE_RAW)
                    time.sleep(0.025)
                    raw_h2, raw_ethanol = _read_words(bus, I2C_ADDR, 2)

                    _log_line(ts, eco2, tvoc, raw_h2, raw_ethanol)
                    _update_latest({
                        "ok": True,
                        "ts": ts,
                        "eco2": eco2,
                        "tvoc": tvoc,
                        "raw_h2": raw_h2,
                        "raw_ethanol": raw_ethanol,
                        "error": None,
                    })

                    for _ in range(int(LOG_INTERVAL_SEC * 10)):
                        if _stop_event.is_set():
                            break
                        time.sleep(0.1)
        except Exception as exc:
            _update_latest({
                "ok": False,
                "error": str(exc),
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            time.sleep(5)


def start_sensor_logger():
    global _logger_thread
    if _logger_thread and _logger_thread.is_alive():
        return

    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return

    _stop_event.clear()
    _logger_thread = threading.Thread(target=_sensor_loop, daemon=True)
    _logger_thread.start()


@bp.route("/api/sensor/latest")
def sensor_latest():
    with _latest_lock:
        return jsonify(dict(_latest))


@bp.route("/api/sensor/log")
def sensor_log():
    limit = 300
    try:
        limit = int(request.args.get("limit", limit))
    except Exception:
        limit = 300

    if limit <= 0:
        limit = 300

    if not os.path.exists(LOG_PATH):
        return jsonify({"ok": False, "error": "log not found", "rows": []})

    rows = []
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
        data_lines = [ln.strip() for ln in lines[1:] if ln.strip()]
        for ln in data_lines[-limit:]:
            parts = ln.split(",")
            if len(parts) != 5:
                continue
            ts, eco2, tvoc, raw_h2, raw_ethanol = parts
            rows.append({
                "ts": ts,
                "eco2": int(eco2),
                "tvoc": int(tvoc),
                "raw_h2": int(raw_h2),
                "raw_ethanol": int(raw_ethanol),
            })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "rows": []})

    return jsonify({"ok": True, "rows": rows})
