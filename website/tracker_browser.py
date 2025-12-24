#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tracker_browser.py
功能：
    1. 使用 Playwright 自动访问 tracking.nextsls.com。
    2. 自动输入追踪号并查询。
    3. 抓取结果 HTML 并解析出最新物流信息。
    4. 比对与上次记录是否更新。
    5. 输出运行结果并保存最新状态。
"""

import asyncio
import os
import json
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ===== 配置区 =====
TRACKING_NUMBER = "LO018721"   # ← 修改为你要跟踪的单号
RESULT_HTML = "/home/bbdwz/projects/website/tracker_result.html"
LAST_JSON = "/home/bbdwz/projects/website/tracker_last.json"


# ===== Playwright 抓取网页 =====
async def fetch_tracking(tracking_number: str):
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()

            # 打开查询网站
            await page.goto("http://tracking.nextsls.com/trace?app=670dd81873f04205f41ccb9a", timeout=60000)

            # 等待输入框出现
            await page.wait_for_selector("input.ant-input", timeout=20000)

            # 输入追踪号
            await page.fill("input.ant-input", tracking_number)

            # 点击"查询"按钮
            await page.click("button.ant-btn-primary")

            # ✅ 关键修改：等待物流路线加载完
            try:
                # 有数据的情况
                await page.wait_for_selector(".ant-timeline-item", timeout=60000)
            except:
                # 没有路线也要继续保存页面
                await page.wait_for_timeout(10000)

            # 保存 HTML
            html = await page.content()
            with open(RESULT_HTML, "w", encoding="utf-8") as f:
                f.write(html)

            await page.close()
            await browser.close()
            return html
    except Exception as e:
        print(f"⚠️ 浏览器异常：{e}")
        if browser:
            try:
                await browser.close()
            except:
                pass
        raise


# ===== HTML 解析逻辑 =====
def parse_tracking_result(html: str):
    soup = BeautifulSoup(html, "html.parser")

    number_tag = soup.select_one(".tracking_number___19XrB")
    if not number_tag:
        return None

    data = {}
    data["tracking_number"] = number_tag.get_text(strip=True)

    # 基本信息
    rows = soup.select(".item_header___34LsY .comment___2Gy6N")
    labels = ["状态", "国家", "邮编", "系统单号", "客户单号", "转单号"]
    for i, label in enumerate(labels):
        if i < len(rows):
            data[label] = rows[i].get_text(strip=True)

    # ✅ 关键修复：正确解析物流路由
    data["routes"] = []
    timeline_items = soup.select(".ant-timeline-item")
    
    for li in timeline_items:
        event_el = li.select_one(".route_event___cF-bT")
        time_el = li.select_one(".route_time___2OCqz")
        
        if event_el and time_el:
            event_text = event_el.get_text(strip=True)
            time_text = time_el.get_text(strip=True)
            
            # ✅ 只有在两者都非空时才添加
            if event_text and time_text:
                data["routes"].append({
                    "time": time_text,
                    "event": event_text
                })

    # ✅ 验证：如果没有路由信息，返回 None（表示无效抓取）
    if not data["routes"]:
        print(f"⚠️ 追踪号 {data.get('tracking_number')} 未找到物流路由信息")
        return None

    return data


# ===== 与上次结果比对 =====
def compare_with_last(new_data: dict, json_path: str = None):
    """
    比对新旧物流数据：
      - 有更新：写入新数据，返回"✅ 有更新"
      - 无更新：保留旧数据，返回"💤 无更新（最新状态：xxx）"
      - 新数据为空：仍返回旧数据内容
    
    Args:
        new_data: 新的物流数据字典
        json_path: 存储 JSON 的路径（如不提供则使用全局 LAST_JSON）
    """
    if json_path is None:
        json_path = LAST_JSON
    
    if not os.path.exists(json_path):
        # 第一次抓取
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        return "首次抓取 ✅"

    # 读取旧数据
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except Exception:
        old_data = {}

    old_routes = old_data.get("routes", [])
    new_routes = new_data.get("routes", [])

    # 1️⃣ 新数据为空，直接返回旧数据
    if not new_routes:
        current = old_routes[0]["event"] if old_routes else "无"
        return f"💤 无更新（最新状态：{current}）"

    # 2️⃣ 新旧不同 → 写入新数据
    if new_routes != old_routes:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        old_last = old_routes[0]["event"] if old_routes else "无"
        new_last = new_routes[0]["event"]
        return f"✅ 有更新：{old_last} → {new_last}"

    # 3️⃣ 新旧相同 → 保留旧数据不写入
    current = new_routes[0]["event"]
    return f"💤 无更新（最新状态：{current}）"


# ===== 主入口 =====
async def main():
    try:
        html = await fetch_tracking(TRACKING_NUMBER)
        parsed = parse_tracking_result(html)
        if not parsed:
            print("❌ 抓取失败：未识别到有效内容")
            return
        print(compare_with_last(parsed))
    except Exception as e:
        print(f"❌ 抓取过程出错：{e}")


if __name__ == "__main__":
    asyncio.run(main())
