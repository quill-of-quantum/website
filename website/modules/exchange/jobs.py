from modules.exchange.client import fetch_history, fetch_latest_quote
from modules.exchange.service import build_analysis, build_indicator_history
from modules.exchange.seasonality import build_seasonality_report
from modules.exchange.pattern_model import build_pattern_report
from modules.exchange.storage import (
    analysis_is_dirty,
    load_plan,
    load_rates,
    load_indicators,
    load_seasonality,
    load_pattern_report,
    save_analysis,
    save_indicators,
    save_seasonality,
    save_pattern_report,
    mark_analysis_dirty,
    save_rates,
)


def refresh_quotes():
    payload = load_rates()
    if not payload:
        payload = fetch_history()
    payload = {**payload, **fetch_latest_quote()}
    changed = save_rates(payload)
    return {"changed": changed, "points": len(payload.get("dates", []))}


def refresh_history():
    history = fetch_history()
    current = load_rates() or {}
    for key in ("latest_date", "latest_eur", "latest_usd"):
        if current.get(key):
            history[key] = current[key]
    changed = save_rates(history)
    return {"changed": changed, "points": len(history.get("dates", []))}


def compute_analysis():
    if not analysis_is_dirty():
        return {"skipped": True, "reason": "inputs unchanged"}
    rates = load_rates()
    if not rates:
        raise RuntimeError("没有可用汇率数据")
    analysis = build_analysis(rates, load_plan(), load_seasonality(), load_pattern_report())
    save_analysis(analysis)
    return {
        "paths": analysis["simulation"]["paths"],
        "stable": analysis["simulation"]["stable"],
        "suggested_eur": analysis["decision"]["suggested_eur"],
    }


def compute_indicators():
    rates = load_rates()
    if not rates:
        raise RuntimeError("没有可用汇率数据")
    existing = load_indicators() or {}
    last_date = (rates.get("dates") or [None])[-1]
    if (existing.get("source_last_date") == last_date
            and existing.get("source_points") == len(rates.get("dates", []))
            and len(existing.get("dates", [])) >= 250):
        return {"skipped": True, "reason": "history unchanged"}
    payload = build_indicator_history(rates)
    payload["source_last_date"] = last_date
    payload["source_points"] = len(rates.get("dates", []))
    save_indicators(payload)
    return {"points": len(payload["dates"]), "last_date": last_date}


def compute_seasonality():
    rates = load_rates()
    if not rates:
        raise RuntimeError("没有可用汇率数据")
    existing = load_seasonality() or {}
    source = existing.get("source", {})
    dates = rates.get("dates") or []
    if source.get("last_date") == dates[-1] and source.get("points") == len(dates):
        return {"skipped": True, "reason": "history unchanged"}
    payload = build_seasonality_report(rates)
    save_seasonality(payload)
    mark_analysis_dirty()
    return {
        "effective": len(payload["effective_rules"]), "candidate": len(payload["candidate_rules"]),
        "invalid": len(payload["invalid_rules"]),
    }


def compute_pattern_model():
    rates = load_rates()
    if not rates:
        raise RuntimeError("没有可用汇率数据")
    existing = load_pattern_report() or {}
    source = existing.get("source", {})
    dates = rates.get("dates") or []
    if source.get("last_date") == dates[-1] and source.get("points") == len(dates):
        return {"skipped": True, "reason": "history unchanged"}
    payload = build_pattern_report(rates)
    save_pattern_report(payload)
    mark_analysis_dirty()
    return {"approved": payload["approved_for_decision"], "status": payload["status"],
            "validation": payload["walk_forward"]["metrics"]}
