#!/usr/bin/env python3
"""Simple SGP30 test script.

Usage:
  python3 test.py --bus 1 --addr 0x58
"""

import argparse
import sys
import time

try:
    from smbus2 import SMBus
except ImportError:
    try:
        from smbus import SMBus  # type: ignore
    except ImportError as exc:
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        hint = "Try: sudo apt-get install -y python3-smbus"
        if in_venv:
            hint = (
                "You appear to be in a venv; apt-installed python3-smbus is not visible.\n"
                "Either run with /usr/bin/python3, or install smbus2 into this venv: pip install smbus2"
            )
        raise SystemExit(f"smbus2 or smbus not installed. {hint}") from exc

SGP30_ADDR_DEFAULT = 0x58

# SGP30 commands
CMD_INIT_AIR_QUALITY = 0x2003
CMD_MEASURE_AIR_QUALITY = 0x2008
CMD_MEASURE_RAW = 0x2050
CMD_GET_FEATURE_SET = 0x202F
CMD_MEASURE_TEST = 0x2032


def _crc8(data):
    # SGP CRC-8: polynomial 0x31, init 0xFF
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
    # Each word: 2 bytes + 1 CRC
    raw = bus.read_i2c_block_data(addr, 0x00, n_words * 3)
    words = []
    for i in range(n_words):
        b1, b2, crc = raw[i * 3 : i * 3 + 3]
        if _crc8([b1, b2]) != crc:
            raise ValueError("CRC mismatch while reading from SGP30")
        words.append((b1 << 8) | b2)
    return words


def main():
    parser = argparse.ArgumentParser(description="SGP30 test program")
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument("--addr", type=lambda x: int(x, 0), default=SGP30_ADDR_DEFAULT, help="I2C address (default: 0x58)")
    parser.add_argument("--samples", type=int, default=10, help="Number of samples to read")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval seconds between samples")
    parser.add_argument("--raw", action="store_true", help="Also read raw H2/EtOH signals")
    args = parser.parse_args()

    with SMBus(args.bus) as bus:
        # Feature set
        _write_cmd(bus, args.addr, CMD_GET_FEATURE_SET)
        time.sleep(0.01)
        feature_set = _read_words(bus, args.addr, 1)[0]
        print(f"Feature set: 0x{feature_set:04X}")

        # Optional self-test
        _write_cmd(bus, args.addr, CMD_MEASURE_TEST)
        time.sleep(0.22)
        test_res = _read_words(bus, args.addr, 1)[0]
        print(f"Self-test result: 0x{test_res:04X} (0xD400 means OK)")

        # Init air quality
        _write_cmd(bus, args.addr, CMD_INIT_AIR_QUALITY)
        time.sleep(0.01)

        print("Reading eCO2/TVOC...")
        for i in range(args.samples):
            _write_cmd(bus, args.addr, CMD_MEASURE_AIR_QUALITY)
            time.sleep(0.012)
            eco2, tvoc = _read_words(bus, args.addr, 2)
            if args.raw:
                _write_cmd(bus, args.addr, CMD_MEASURE_RAW)
                time.sleep(0.025)
                raw_h2, raw_eth = _read_words(bus, args.addr, 2)
                print(
                    f"{i+1:02d}: eCO2={eco2} ppm, TVOC={tvoc} ppb, "
                    f"rawH2={raw_h2}, rawEtOH={raw_eth}"
                )
            else:
                print(f"{i+1:02d}: eCO2={eco2} ppm, TVOC={tvoc} ppb")
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
