#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tracker_scheduler.py
作用：
    定期执行 tracker 任务。
    - 每分钟扫描任务列表
    - 对超过 interval_minutes 的任务运行抓取
    - 抓取失败不覆盖旧状态
"""

import time
import sqlite3
import asyncio
from datetime import datetime
from tracker_browser import fetch_tracking, parse_tracking_result, compare_with_last

DB_PATH = "/home/bbdwz/projects/website/tracker.db"

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def run_task(task):
    """执行单个追踪任务"""
    conn = db_conn()
    conn.execute("UPDATE tracker_tasks SET running=1 WHERE id=?", (task["id"],))
    conn.commit()
    conn.close()

    print(f"🚀 开始任务 {task['tracking_number']} ...")

    try:
        html = await fetch_tracking(task["tracking_number"])
        parsed = parse_tracking_result(html)

        # 🧩 抓取失败时，不覆盖旧状态
        if not parsed:
            status = "抓取失败（保持旧状态）"
        else:
            status = compare_with_last(parsed)

        conn = db_conn()
        if "抓取失败" in status:
            # 仅更新时间，不更新状态
            conn.execute(
                "UPDATE tracker_tasks SET running=0, last_run=datetime('now') WHERE id=?",
                (task["id"],)
            )
        else:
            conn.execute(
                "UPDATE tracker_tasks SET running=0, last_run=datetime('now'), last_status=? WHERE id=?",
                (status, task["id"])
            )
        conn.commit()
        conn.close()

        print(f"[{task['tracking_number']}] {status}")

    except Exception as e:
        print(f"❌ 执行任务 {task['tracking_number']} 出错：{e}")
        conn = db_conn()
        conn.execute("UPDATE tracker_tasks SET running=0 WHERE id=?", (task["id"],))
        conn.commit()
        conn.close()


def main():
    """主循环"""
    while True:
        conn = db_conn()
        tasks = conn.execute("SELECT * FROM tracker_tasks WHERE enabled=1").fetchall()
        conn.close()

        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] 🔄 检查任务列表...")

        for t in tasks:
            run_now = False
            if not t["last_run"]:
                run_now = True
            else:
                last = datetime.fromisoformat(t["last_run"])
                diff = (datetime.now() - last).total_seconds() / 60
                run_now = diff >= t["interval_minutes"]

            if run_now:
                print(f"⏰ 触发任务 {t['tracking_number']} (间隔 {t['interval_minutes']} 分钟)")
                asyncio.run(run_task(t))

        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    main()
