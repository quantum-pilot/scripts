# /// script
# dependencies = [
#   "cronsim",
#   "libusb",
#   "libusb-package",
#   "PyYAML",
#   "pyusb",
# ]
# ///

import os
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

import yaml
import libusb_package, usb.util
from cronsim import CronSim


POLL_MINS = int(os.getenv("POLL_MINS", "5"))
CANDIDATE_FILES = ("schedule.yaml", "cron.yaml")

# Sample `schedule.yaml` at $HOME
"""
- name: HIIT
  cron: "30 9 * * *"
  last: null
  once: false

- name: Walking
  cron: "0 7 * * 1,3,5"
  last: null
  once: false

- name: Running
  cron: "0 7 * * 0,2,4,6"
  last: null
  once: false

"""

# Init printer
backend = libusb_package.get_libusb1_backend()
dev = usb.core.find(idVendor=0x0471, idProduct=0x0055, backend=backend)
assert dev is not None, "Printer not found"
dev.set_configuration()
cfg = dev.get_active_configuration()
intf = cfg[(0, 0)]
ep_out = usb.util.find_descriptor(
    intf,
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
                        == usb.util.ENDPOINT_OUT)
assert ep_out is not None, "OUT endpoint not found"


ESC = b'\x1b'
GS  = b'\x1d'
TOP_PAD  = 24
BOT_PAD  = 24


def create_msg(msg: str):
    today = datetime.now().strftime('%Y-%m-%d')
    msg = (
        ESC + b'@'                          # reset
    + ESC + b'a' + b'\x01'                  # centre horizontally
    + GS  + b'!' + b'\x10'                  # double width & single height
    + (today + '\n' + msg).encode('utf-8')  # message
    + ESC + b'J' + bytes([BOT_PAD])         # bottom padding
    + GS  + b'V' + b'\x42' + b'\x00'        # partial cut, no feed
    )
    ep_out.write(msg)


def parse_iso(val):
    if not val:
        return None
    if isinstance(val, str) and val.strip() != "":
        try:
            val = datetime.fromisoformat(val)
        except Exception:
            return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    return None


def prev_fire(cron_expr: str, base: datetime):
    try:
        return next(CronSim(cron_expr, base, reverse=True))
    except StopIteration:
        return None
    except Exception:
        return None


@contextmanager
def load_jobs():
    path_env = os.getenv("CRON_YAML_PATH")
    if path_env:
        path = Path(path_env).expanduser()
        if not path.exists():
            yield None
            return
    else:
        home = Path.home()
        path = None
        for fname in CANDIDATE_FILES:
            p = home / fname
            if p.exists():
                path = p
                break
        if path is None:
            yield None
            return

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = data if isinstance(data, list) else []
    except Exception:
        jobs = []

    yield jobs

    dumped = yaml.safe_dump(
        jobs, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    path.write_text(dumped, encoding="utf-8")


def main():
    with load_jobs() as jobs:
        if not jobs:
            return

        now = datetime.now().astimezone()

        for job in jobs:
            name = (job.get("name") or "").strip()
            cron_expr = (job.get("cron") or "").strip()
            if not name or not cron_expr:
                continue

            once = bool(job.get("once", False))
            last_dt = parse_iso(job.get("last"))

            if once and last_dt is not None:
                continue

            prev = prev_fire(cron_expr, now)
            if prev is None:
                continue

            if last_dt is None or last_dt < prev:
                create_msg(name)
                job["last"] = prev.isoformat()


if __name__ == "__main__":
    main()
