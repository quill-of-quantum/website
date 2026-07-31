import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests


def fetch_latest_quote():
    response = requests.get("https://open.er-api.com/v6/latest/CNY", timeout=8)
    response.raise_for_status()
    current = response.json()
    if current.get("result") != "success":
        raise ValueError("实时汇率接口未返回成功状态")
    return {
        "latest_date": current.get("time_last_update_utc", "").split(" ")[0],
        "latest_eur": 1 / current["rates"]["EUR"],
        "latest_usd": 1 / current["rates"]["USD"],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_history():
    response = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml", timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    namespace = {"def": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    points = []
    for cube in root.findall(".//def:Cube/def:Cube", namespace):
        rates = {item.get("currency"): float(item.get("rate")) for item in cube.findall("def:Cube", namespace)}
        if "CNY" in rates and "USD" in rates:
            points.append((cube.get("time"), rates["CNY"], rates["CNY"] / rates["USD"]))
    points.sort()
    if not points:
        raise ValueError("ECB未返回汇率数据")

    def stats(values, dates):
        low = min(range(len(values)), key=values.__getitem__)
        high = max(range(len(values)), key=values.__getitem__)
        return {
            "min": round(values[low], 4), "min_date": dates[low], "min_idx": low,
            "max": round(values[high], 4), "max_date": dates[high], "max_idx": high,
            "pct_change": round((values[high] / values[low] - 1) * 100, 2),
        }

    dates = [point[0] for point in points]
    eur = [point[1] for point in points]
    usd = [point[2] for point in points]
    return {
        "dates": dates, "eur_cny": eur, "usd_cny": usd,
        "stats": {"eur": stats(eur, dates), "usd": stats(usd, dates)},
        "latest_date": dates[-1], "latest_eur": eur[-1], "latest_usd": usd[-1],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_rates():
    payload = fetch_history()
    try:
        payload.update(fetch_latest_quote())
    except Exception as exc:
        print(f"汇率最新值获取失败，将使用ECB日频值: {exc}", flush=True)
    return payload
