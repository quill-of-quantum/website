import json
import os
import re
import shutil
import socket
import subprocess
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from modules.auth.user_store import is_admin_user, user_exists


bp = Blueprint("nas", __name__)

ADMIN_PREFIX = "/1"
NAS_STATE_PATH = "/home/bbdwz/projects/website/data/nas/state.json"
LEGACY_NAS_STATE_PATH = "/home/bbdwz/projects/website/nas_state.json"
NAS_MANAGED_CONF_PATH = "/etc/samba/smb.conf.d/website-nas.conf"
NAS_SMB_CONF_PATH = "/etc/samba/smb.conf"


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("logged_in") and not user_exists(session.get("user")):
            session.clear()
        if not session.get("logged_in") or not is_admin_user(session.get("user")):
            if request.path.startswith(ADMIN_PREFIX + "/api"):
                return jsonify({"require_login": True}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


def _json_error(message, code=400, detail=None):
    payload = {"ok": False, "error": message}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), code


def _json_ok(data=None):
    payload = {"ok": True}
    if data:
        payload.update(data)
    return jsonify(payload)


def _default_nas_state():
    return {
        "version": 2,
        "simple_share": {
            "enabled": False,
            "device": "",
            "mount_point": "",
            "share_name": "usbshare",
            "share_path": "",
        },
    }


def _load_nas_state():
    data = _default_nas_state()
    for path in (NAS_STATE_PATH, LEGACY_NAS_STATE_PATH):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if "simple_share" in loaded:
                        data["simple_share"].update(loaded.get("simple_share") or {})
                    elif "shares" in loaded:
                        share = next(iter((loaded.get("shares") or {}).values()), {}) or {}
                        data["simple_share"].update({
                            "enabled": bool(loaded.get("shares")),
                            "device": (loaded.get("mount") or {}).get("device", ""),
                            "mount_point": share.get("path", ""),
                            "share_name": "usbshare",
                            "share_path": share.get("path", ""),
                        })
                    break
        except Exception:
            pass
    return data


def _save_nas_state(state):
    os.makedirs(os.path.dirname(NAS_STATE_PATH), exist_ok=True)
    tmp_path = NAS_STATE_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True, indent=2)
        os.replace(tmp_path, NAS_STATE_PATH)
        return True, None
    except Exception as e:
        return False, str(e)


def _run_cmd(args, input_text=None):
    try:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _run_root_cmd(args, input_text=None):
    if os.geteuid() == 0:
        return _run_cmd(args, input_text=input_text)
    sudo_path = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not sudo_path:
        return False, "", "sudo-not-found"
    return _run_cmd([sudo_path, "-n"] + args, input_text=input_text)


def _read_root_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return True, f.read(), None
    except Exception:
        ok, out, err = _run_root_cmd(["cat", path])
        if ok:
            return True, out, None
        return False, "", err


def _write_root_file(path, content):
    if os.geteuid() == 0:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)
    sudo_path = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not sudo_path:
        return False, "sudo-not-found"
    try:
        result = subprocess.run(
            [sudo_path, "-n", "tee", path],
            input=content,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or "").strip()
        return True, None
    except Exception as e:
        return False, str(e)


def _get_service_status(service_name):
    systemctl_path = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl_path:
        return "no-systemctl"
    try:
        result = subprocess.run(
            [systemctl_path, "show", "-p", "ActiveState", "-p", "SubState", service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").lower()
            if "not found" in stderr or "not-found" in stderr:
                return "not_found"
            if "system has not been booted" in stderr:
                return "no-systemd"
            return "unknown"
        active = None
        sub = None
        for line in result.stdout.splitlines():
            if line.startswith("ActiveState="):
                active = line.split("=", 1)[1].strip()
            elif line.startswith("SubState="):
                sub = line.split("=", 1)[1].strip()
        if active:
            if sub and sub != active:
                return f"{active} ({sub})"
            return active
        return "unknown"
    except Exception:
        return "unknown"


def _get_samba_status():
    return {
        "smbd": _get_service_status("smbd.service"),
        "nmbd": _get_service_status("nmbd.service"),
    }


def _get_local_ip():
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        try:
            if sock:
                sock.close()
        except Exception:
            pass
    ok, out, _ = _run_cmd(["hostname", "-I"])
    if ok:
        for ip in out.split():
            if "." in ip and not ip.startswith("127."):
                return ip
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None


def _get_lsblk():
    ok, out, err = _run_cmd(["lsblk", "-J", "-o", "NAME,KNAME,TYPE,FSTYPE,SIZE,LABEL,UUID,MOUNTPOINT,TRAN,MODEL,VENDOR"])
    if not ok:
        return None, err
    try:
        return json.loads(out), None
    except Exception as e:
        return None, str(e)


def _flatten_lsblk_devices(devices, parent=None):
    rows = []
    for device in devices or []:
        item = dict(device)
        item["parent"] = parent
        rows.append(item)
        rows.extend(_flatten_lsblk_devices(item.get("children") or [], item))
    return rows


def _is_usb_storage_candidate(item):
    if item.get("type") not in ("part", "disk"):
        return False
    if item.get("type") == "disk" and item.get("children"):
        return False
    if item.get("tran") == "usb":
        return True
    parent = item.get("parent") or {}
    return parent.get("tran") == "usb"


def _safe_mount_name(item):
    for key in ("label", "uuid", "kname", "name"):
        value = item.get(key)
        if value:
            text = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value)).strip("-")
            if text:
                return text[:64]
    return "usb-drive"


def _get_usb_share_devices():
    data, err = _get_lsblk()
    if not data:
        return [], err
    rows = _flatten_lsblk_devices(data.get("blockdevices", []))
    devices = []
    for item in rows:
        if not _is_usb_storage_candidate(item):
            continue
        kname = item.get("kname") or item.get("name")
        if not kname:
            continue
        parent = item.get("parent") or {}
        display_bits = [
            item.get("label") or kname,
            item.get("size") or "",
            item.get("fstype") or "未识别文件系统",
        ]
        model = item.get("model") or parent.get("model") or ""
        if model:
            display_bits.append(model.strip())
        devices.append({
            "device": f"/dev/{kname}",
            "name": item.get("name") or kname,
            "kname": kname,
            "type": item.get("type"),
            "fstype": item.get("fstype") or "",
            "size": item.get("size") or "",
            "label": item.get("label") or "",
            "uuid": item.get("uuid") or "",
            "mountpoint": item.get("mountpoint") or "",
            "model": model,
            "display": " · ".join(bit for bit in display_bits if bit),
            "share_ready": bool(item.get("fstype")),
        })
    devices.sort(key=lambda x: (x["label"] or x["name"]))
    return devices, None


def _sanitize_smb_value(value):
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def _render_simple_nas_conf(share_name, share_path):
    share_name = _sanitize_smb_value(share_name or "usbshare") or "usbshare"
    share_path = _sanitize_smb_value(share_path)
    return "\n".join([
        "# Managed by website NAS panel.",
        "# Simple LAN guest share. Do not edit manually.",
        "map to guest = Bad User",
        "",
        f"[{share_name}]",
        f"  path = {share_path}",
        "  browseable = yes",
        "  read only = no",
        "  guest ok = yes",
        "  public = yes",
        "  create mask = 0666",
        "  directory mask = 0777",
        "",
    ])


def _ensure_samba_include():
    include_line = f"include = {NAS_MANAGED_CONF_PATH}"
    ok, content, err = _read_root_file(NAS_SMB_CONF_PATH)
    if not ok:
        return False, f"read-smbconf-failed: {err}"
    if include_line.lower() in content.lower():
        return True, "already"
    lines = content.splitlines()
    inserted = False
    for idx, line in enumerate(lines):
        if line.strip().lower() == "[global]":
            lines.insert(idx + 1, include_line)
            inserted = True
            break
    if not inserted:
        lines.append("")
        lines.append("[global]")
        lines.append(include_line)
    new_content = "\n".join(lines).rstrip() + "\n"
    ok, err = _write_root_file(NAS_SMB_CONF_PATH, new_content)
    if not ok:
        return False, f"write-smbconf-failed: {err}"
    return True, "added"


def _share_urls(share_name):
    ip = _get_local_ip() or "树莓派IP"
    return {
        "windows": f"\\\\{ip}\\{share_name}",
        "mac": f"smb://{ip}/{share_name}",
        "linux": f"smb://{ip}/{share_name}",
    }


@bp.route(ADMIN_PREFIX + "/nas")
@login_required
def admin_nas():
    return render_template("nas.html", user=session.get("user"))


@bp.route(ADMIN_PREFIX + "/api/nas/simple/status")
@login_required
def nas_simple_status():
    state = _load_nas_state()
    devices, devices_err = _get_usb_share_devices()
    simple = state.get("simple_share") or {}
    share_name = simple.get("share_name") or "usbshare"
    return _json_ok({
        "state": simple,
        "devices": devices,
        "devices_err": devices_err,
        "samba": {
            "service": _get_samba_status(),
            "share_url": _share_urls(share_name)["windows"],
            "share_urls": _share_urls(share_name),
            "managed_conf_path": NAS_MANAGED_CONF_PATH,
        },
    })


@bp.route(ADMIN_PREFIX + "/api/nas/simple/enable", methods=["POST"])
@login_required
def nas_simple_enable():
    data = request.get_json(silent=True) or {}
    selected_device = (data.get("device") or "").strip()
    devices, devices_err = _get_usb_share_devices()
    if devices_err:
        return _json_error("device-scan-failed", detail=devices_err)
    device = next((item for item in devices if item["device"] == selected_device), None)
    if not device:
        return _json_error("device-not-found")
    if not device.get("fstype"):
        return _json_error("filesystem-not-detected", detail="不会自动格式化硬盘，请先完成格式化。")

    mount_point = device.get("mountpoint")
    if not mount_point:
        mount_point = os.path.join("/mnt/website-nas", _safe_mount_name(device))
        ok, out, err = _run_root_cmd(["mkdir", "-p", mount_point])
        if not ok:
            return _json_error("mkdir-failed", detail=err or out)
        ok, out, err = _run_root_cmd(["mount", selected_device, mount_point])
        if not ok:
            return _json_error("mount-failed", detail=err or out)

    share_name = "usbshare"
    content = _render_simple_nas_conf(share_name, mount_point)
    ok, err = _write_root_file(NAS_MANAGED_CONF_PATH, content)
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    ok, msg = _ensure_samba_include()
    if not ok:
        return _json_error("include-failed", detail=msg)

    _run_root_cmd(["chmod", "0777", mount_point])

    for unit in ("smbd.service", "nmbd.service"):
        _run_root_cmd(["systemctl", "enable", unit])
        ok, out, err = _run_root_cmd(["systemctl", "restart", unit])
        if unit == "smbd.service" and not ok:
            return _json_error("samba-start-failed", detail=err or out)

    state = _load_nas_state()
    state["simple_share"] = {
        "enabled": True,
        "device": selected_device,
        "mount_point": mount_point,
        "share_name": share_name,
        "share_path": mount_point,
    }
    _save_nas_state(state)
    return _json_ok({
        "state": state["simple_share"],
        "share_url": _share_urls(share_name)["windows"],
        "share_urls": _share_urls(share_name),
        "samba": _get_samba_status(),
    })


@bp.route(ADMIN_PREFIX + "/api/nas/simple/disable", methods=["POST"])
@login_required
def nas_simple_disable():
    ok, err = _write_root_file(NAS_MANAGED_CONF_PATH, "# Managed by website NAS panel.\n# Sharing disabled.\n")
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    for unit in ("smbd.service", "nmbd.service"):
        _run_root_cmd(["systemctl", "disable", unit])
        _run_root_cmd(["systemctl", "stop", unit])
    state = _load_nas_state()
    simple = state.get("simple_share") or {}
    simple["enabled"] = False
    state["simple_share"] = simple
    _save_nas_state(state)
    return _json_ok({"state": state["simple_share"], "samba": _get_samba_status()})
