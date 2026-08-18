#!/usr/bin/env python3
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.housing.scraper import scrape, scrape_details
from modules.housing.db import (
    apply_catalog, detail_target_ids, migrate_legacy_state, notification_rooms,
    reset_tracking_data, save_room_detail,
)
from modules.housing.analysis import geocode_pending, parse_detail
from modules.housing.notifications import build_notification, notification_title
from modules.housing.result import generate_result_html
from modules.housing.store import (
    clear_service_pid, consume_run_request, load_config, load_state, save_state,
    write_service_pid,
)


PROFILE_DIR = Path(__file__).resolve().parents[2] / "data" / "housing" / "browser_profile"
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081")
STOP = False


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _notify(config, changes):
    if not config.get("email_enabled") or not changes:
        return
    selected = []
    for item in changes:
        if item["change"] in {"added", "relisted", "updated"} and item["rental_type"] in config["notify_added_types"]:
            selected.append(item)
        elif item["change"] == "delisted" and item["rental_type"] in config["notify_delisted_types"]:
            selected.append(item)
    if not selected:
        return
    room_records = notification_rooms([item["id"] for item in selected])
    plain_text, html_table, _ = build_notification(selected, room_records)
    for recipient in config["notification_emails"]:
        response = requests.post(
            f"{EMAIL_SERVICE_URL}/api/mail/send",
            json={
                "to": recipient,
                "subject": f"{notification_title(selected)}（{len(selected)} 条）",
                "text": plain_text,
                "html": html_table,
            },
            timeout=20,
        )
        response.raise_for_status()


def run_once(mode="incremental"):
    initialize = mode == "initialize"
    search_mode = "full" if initialize else mode
    config = load_config(include_password=True)
    state = load_state()
    state.update({
        "status": "running", "last_started_at": _now(), "last_finished_at": "",
        "last_error": "", "progress": {"step": "starting", "message": "准备开始检查"},
        "run_log": [], "run_mode": mode,
    })
    save_state(state)

    def progress(step, message, **details):
        entry = {"at": _now(), "step": step, "message": message, **details}
        state["progress"] = entry
        state.setdefault("run_log", []).append(entry)
        state["run_log"] = state["run_log"][-500:]
        save_state(state)

    try:
        if initialize:
            progress("initialize_reset", "正在清理房源、变化和运行历史；保留地理编码缓存")
            reset_tracking_data()
            progress("initialize_search", "历史数据已清理，开始建立全量基准")
        else:
            progress("starting", f"已读取设置，准备执行{'全量' if mode == 'full' else '增量'}搜索", run_mode=mode)
        current = scrape(config, PROFILE_DIR, progress=progress)
        targets = detail_target_ids(current, search_mode)
        progress("catalog_commit", f"目录发现 {len(current)} 条，正在先提交 SQLite", discovered=len(current))
        changes, counts = apply_catalog(current, search_mode, state["last_started_at"], baseline=initialize)
        state["last_counts"] = {"total": len(current), **counts}
        state["last_changes"] = changes[:100]
        save_state(state)
        progress("catalog_done", f"目录已提交：{len(current)} 条，新增 {counts['added']}，下架 {counts['delisted']}", changed=len(changes))
        generate_result_html()

        detail_stats = {"planned": len(targets), "saved": 0, "failed": 0}
        if targets:
            progress("detail_plan", f"本轮需要抓取并逐条保存 {len(targets)} 个详情", detail_total=len(targets))

            def save_detail_checkpoint(room_id, detail):
                parsed = parse_detail(detail)
                save_room_detail(room_id, parsed)
                detail_stats["saved"] += 1

            try:
                details = scrape_details(
                    config, PROFILE_DIR, current, targets, progress=progress,
                    item_callback=save_detail_checkpoint,
                )
                detail_stats["failed"] = len(targets) - len(details)
            except Exception as exc:
                detail_stats["failed"] = len(targets) - detail_stats["saved"]
                progress("detail_stage_error", f"详情阶段中断；已保存 {detail_stats['saved']} 条：{exc}")
        else:
            progress("detail_skip", "没有新增或变化详情需要抓取")
        state["detail_stats"] = detail_stats
        save_state(state)

        try:
            progress("notify_plan", "详情已保存，正在按通知条件生成房源变化表格")
            _notify(config, changes)
        except Exception as exc:
            progress("notify_error", f"邮件通知失败但抓取继续：{exc}")

        progress("geocode_plan", "开始独立定位数据库中尚无坐标的地址")
        location_stats = geocode_pending(progress=progress)
        state["location_stats"] = location_stats
        result_path = generate_result_html()
        state.update({
            "status": "ok", "last_finished_at": _now(), "last_error": "",
            "progress": {"at": _now(), "step": "done", "message": f"{'初始化' if initialize else mode} 完成：目录 {len(current)}，详情保存 {detail_stats['saved']}，已定位 {location_stats['located']}"},
            "last_counts": {"total": len(current), **counts},
            "last_changes": changes[:100], "result_path": str(result_path),
        })
    except Exception as exc:
        logging.exception("Housing check failed")
        error = f"{type(exc).__name__}: {exc}"
        state.setdefault("run_log", []).append({"at": _now(), "step": "error", "message": error})
        state.update({
            "status": "error", "last_finished_at": _now(), "last_error": error,
            "progress": {"at": _now(), "step": "error", "message": error},
        })
    save_state(state)


def main():
    global STOP
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("STOP", True))
    write_service_pid(os.getpid())
    migrate_legacy_state()
    generate_result_html()
    next_run = 0.0
    next_full_run = 0.0
    try:
        while not STOP:
            config = load_config(include_password=True)
            now = time.monotonic()
            requested_mode = consume_run_request()
            mode = requested_mode
            if mode is None and now >= next_full_run:
                mode = "full"
            elif mode is None and now >= next_run:
                mode = "incremental"
            if mode:
                run_once(mode)
                current_time = time.monotonic()
                next_run = current_time + config["incremental_interval_minutes"] * 60
                if mode in ("full", "initialize"):
                    next_full_run = current_time + config["full_interval_minutes"] * 60
            time.sleep(1)
    finally:
        clear_service_pid(os.getpid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
