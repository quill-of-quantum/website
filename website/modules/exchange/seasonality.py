import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime


MONTH_NAMES = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"]
WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五"]


def _normal_cdf(value):
    return .5 * (1 + math.erf(value / math.sqrt(2)))


def _event_stats(events, label, rule_type, metadata):
    events = sorted(events, key=lambda item: item[0])
    split = max(1, int(len(events) * .7))
    train, test = events[:split], events[split:]

    def summarize(part, direction=None):
        returns = [item[1] for item in part]
        if not returns:
            return {"samples": 0, "mean_pct": 0, "median_pct": 0, "hit_rate_pct": 0, "p_value": 1}
        mean = statistics.fmean(returns)
        direction = direction or (-1 if mean < 0 else 1)
        hit_rate = sum((value * direction) > 0 for value in returns) / len(returns)
        deviation = statistics.stdev(returns) if len(returns) > 1 else 0
        standard_error = deviation / math.sqrt(len(returns)) if deviation else 0
        z_score = abs(mean / standard_error) if standard_error else 0
        p_value = 2 * (1 - _normal_cdf(z_score)) if z_score else 1
        return {
            "samples": len(returns), "mean_pct": round(mean * 100, 3),
            "median_pct": round(statistics.median(returns) * 100, 3),
            "hit_rate_pct": round(hit_rate * 100, 1), "p_value": p_value,
        }

    train_summary = summarize(train)
    direction = -1 if train_summary["mean_pct"] < 0 else 1
    test_summary = summarize(test, direction)
    all_summary = summarize(events, direction)
    same_direction = not test or test_summary["mean_pct"] * direction > 0
    return {
        "id": f"{rule_type}:{':'.join(str(metadata[key]) for key in sorted(metadata))}",
        "label": label, "type": rule_type, "metadata": metadata,
        "direction": "下降" if direction < 0 else "上涨",
        "train": train_summary, "test": test_summary, "all": all_summary,
        "same_direction": same_direction, "q_value": 1.0, "status": "候选",
    }


def _apply_fdr(rules):
    ordered = sorted(enumerate(rules), key=lambda item: item[1]["train"]["p_value"])
    total = len(ordered)
    adjusted = [1.0] * total
    running = 1.0
    for reverse_index in range(total - 1, -1, -1):
        original_index, rule = ordered[reverse_index]
        rank = reverse_index + 1
        running = min(running, rule["train"]["p_value"] * total / rank)
        adjusted[original_index] = running
    for index, rule in enumerate(rules):
        rule["q_value"] = round(adjusted[index], 4)
        enough_test = rule["test"]["samples"] >= 10
        meaningful = abs(rule["test"]["mean_pct"]) >= .05
        validated = rule["same_direction"] and rule["test"]["hit_rate_pct"] >= 55 and meaningful
        if adjusted[index] <= .1 and enough_test and validated:
            rule["status"] = "有效"
        elif not rule["same_direction"] or (enough_test and rule["test"]["hit_rate_pct"] < 48):
            rule["status"] = "失效"
        else:
            rule["status"] = "候选"


def build_seasonality_report(rate_payload):
    raw_dates = rate_payload.get("dates") or []
    values = [float(value) for value in rate_payload.get("eur_cny", [])]
    if len(raw_dates) != len(values) or len(values) < 500:
        raise ValueError("周期分析至少需要500个完整日频数据点")
    dates = [datetime.strptime(value, "%Y-%m-%d").date() for value in raw_dates]

    month_groups = defaultdict(list)
    for index, current_date in enumerate(dates):
        month_groups[(current_date.year, current_date.month)].append(index)
    month_position = {}
    for indexes in month_groups.values():
        for position, index in enumerate(indexes, 1):
            month_position[index] = (position, len(indexes))

    rules = []
    for month in range(1, 13):
        events = []
        for (year, event_month), indexes in month_groups.items():
            if event_month == month and len(indexes) > 1:
                events.append((dates[indexes[0]], values[indexes[-1]] / values[indexes[0]] - 1))
        rules.append(_event_stats(events, f"{MONTH_NAMES[month-1]}整月", "month", {"month": month, "horizon": "month"}))

    buckets = [(1, 3), (4, 7), (8, 12), (13, 17), (18, 31)]
    for start, end in buckets:
        for horizon in (3, 5, 10):
            events = []
            # One observation per month avoids inflating significance with heavily overlapping windows.
            for indexes in month_groups.values():
                if len(indexes) < start:
                    continue
                index = indexes[start - 1]
                if index + horizon < len(values):
                    events.append((dates[index], values[index + horizon] / values[index] - 1))
            label = f"每月第{start}–{end}个交易日起，未来{horizon}个交易日"
            rules.append(_event_stats(events, label, "month_day", {"start": start, "end": end, "horizon": horizon}))

    for weekday in range(5):
        for horizon in (3, 5):
            events = [(dates[index], values[index + horizon] / values[index] - 1)
                      for index in range(len(values) - horizon) if dates[index].weekday() == weekday]
            rules.append(_event_stats(events, f"{WEEKDAY_NAMES[weekday]}起未来{horizon}个交易日", "weekday",
                                      {"weekday": weekday, "horizon": horizon}))

    _apply_fdr(rules)
    rules.sort(key=lambda rule: ({"有效": 0, "候选": 1, "失效": 2}[rule["status"]], rule["q_value"], -rule["test"]["hit_rate_pct"]))

    yearly_lows = Counter()
    yearly_highs = Counter()
    year_groups = defaultdict(list)
    for index, current_date in enumerate(dates):
        year_groups[current_date.year].append(index)
    for indexes in year_groups.values():
        if len(indexes) < 100:
            continue
        yearly_lows[dates[min(indexes, key=lambda idx: values[idx])].month] += 1
        yearly_highs[dates[max(indexes, key=lambda idx: values[idx])].month] += 1

    latest_index = len(values) - 1
    latest_position, _ = month_position[latest_index]
    today = dates[-1]
    active_rules = []
    for rule in rules:
        if rule["status"] != "有效":
            continue
        metadata = rule["metadata"]
        applies = ((rule["type"] == "month" and metadata["month"] == today.month)
                   or (rule["type"] == "month_day" and metadata["start"] <= latest_position <= metadata["end"])
                   or (rule["type"] == "weekday" and metadata["weekday"] == today.weekday()))
        if applies:
            active_rules.append(rule)

    effective = [rule for rule in rules if rule["status"] == "有效"]
    candidates = [rule for rule in rules if rule["status"] == "候选"]
    invalid = [rule for rule in rules if rule["status"] == "失效"]
    historical_signals = []
    for index in range(max(0, len(values) - 370), len(values)):
        current_date = dates[index]
        position, _ = month_position[index]
        matching = []
        for rule in effective:
            metadata = rule["metadata"]
            applies = ((rule["type"] == "month" and metadata["month"] == current_date.month)
                       or (rule["type"] == "month_day" and metadata["start"] <= position <= metadata["end"])
                       or (rule["type"] == "weekday" and metadata["weekday"] == current_date.weekday()))
            if applies:
                matching.append(rule)
        if matching:
            net = sum((1 if rule["direction"] == "上涨" else -1) * (rule["test"]["hit_rate_pct"] - 50) for rule in matching)
            if abs(net) >= 5:
                historical_signals.append({
                    "date": raw_dates[index], "rate": round(values[index], 4),
                    "direction": "高价风险" if net > 0 else "低价窗口",
                    "strength": round(min(100, abs(net) * 4), 1),
                    "rules": [rule["label"] for rule in matching],
                })
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"first_date": raw_dates[0], "last_date": raw_dates[-1], "points": len(values), "years": len(year_groups)},
        "method": {
            "discovery": "前70%样本确定方向", "validation": "后30%样本外验证",
            "multiple_testing": "Benjamini-Hochberg FDR", "minimum_test_samples": 10,
            "effective_rule": "q≤0.10、样本外命中率≥55%、平均幅度≥0.05%且方向一致",
        },
        "current": {"date": raw_dates[-1], "month": today.month, "trading_day_of_month": latest_position,
                    "weekday": WEEKDAY_NAMES[today.weekday()] if today.weekday() < 5 else "周末", "active_rules": active_rules},
        "effective_rules": effective, "candidate_rules": candidates, "invalid_rules": invalid,
        "historical_signals": historical_signals,
        "yearly_extremes": {
            "years": sum(yearly_lows.values()),
            "low_months": [{"month": month, "label": MONTH_NAMES[month - 1], "count": yearly_lows[month]} for month in range(1, 13)],
            "high_months": [{"month": month, "label": MONTH_NAMES[month - 1], "count": yearly_highs[month]} for month in range(1, 13)],
        },
    }
