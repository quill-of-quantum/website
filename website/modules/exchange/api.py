from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from modules.computation.state import list_job_states
from .storage import (
    add_execution,
    list_executions,
    load_latest_analysis,
    load_indicators,
    load_seasonality,
    load_pattern_report,
    load_plan,
    load_rates,
    save_plan,
)


bp = Blueprint("exchange", __name__)


@bp.get("/exchange")
def exchange_page():
    return render_template("exchange.html")


@bp.get("/api/exchange_rate")
def exchange_rate():
    payload = load_rates()
    if not payload:
        return jsonify({"error": "暂无汇率数据，请检查computation.service"}), 503
    return jsonify(payload)


@bp.get("/api/exchange/analysis")
def analysis():
    payload = load_latest_analysis()
    if not payload:
        return jsonify({"success": False, "error": "后台尚未生成分析结果"}), 503
    return jsonify(payload)


@bp.get("/api/exchange/history")
def exchange_history():
    range_name = request.args.get("range", "3m")
    days = {"3m": 92, "6m": 183, "1y": 365}.get(range_name)
    if not days:
        return jsonify({"success": False, "error": "range仅支持3m、6m或1y"}), 400
    payload = load_indicators()
    if not payload or not payload.get("dates"):
        return jsonify({"success": False, "error": "后台尚未生成历史指标"}), 503
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    indexes = [index for index, value in enumerate(payload["dates"]) if value >= cutoff]
    result = {"range": range_name, "definition": payload.get("definition"), "updated_at": payload.get("updated_at")}
    for key in ("dates", "eur_cny", "attractiveness", "higher_probability", "lower_probability"):
        values = payload.get(key, [])
        result[key] = [values[index] for index in indexes if index < len(values)]
    return jsonify(result)


@bp.get("/api/exchange/seasonality")
def exchange_seasonality():
    payload = load_seasonality()
    if not payload:
        return jsonify({"success": False, "error": "后台尚未生成周期规律报告"}), 503
    return jsonify(payload)


@bp.get("/api/exchange/pattern_model")
def exchange_pattern_model():
    payload = load_pattern_report()
    if not payload:
        return jsonify({"success": False, "error": "后台尚未生成隐含形态模型"}), 503
    return jsonify(payload)


@bp.route("/api/exchange/plan", methods=["GET", "POST"])
def plan():
    if request.method == "GET":
        return jsonify(load_plan())
    incoming = request.get_json(silent=True) or {}
    try:
        current = load_plan()
        target = float(incoming.get("target_eur"))
        purchased = float(incoming.get("purchased_eur", current["purchased_eur"]))
        cny_yield = float(incoming.get("cny_yield", 0.5))
        deadline = datetime.strptime(str(incoming.get("deadline")), "%Y-%m-%d").date().isoformat()
        start_date = datetime.strptime(str(incoming.get("start_date") or current["start_date"]), "%Y-%m-%d").date().isoformat()
        if target <= 0 or purchased < 0 or purchased > target:
            raise ValueError("目标和已兑换金额不合法")
        if deadline < start_date:
            raise ValueError("完成期限不能早于计划开始日期")
        payload = {
            "target_eur": target, "purchased_eur": purchased, "start_date": start_date,
            "deadline": deadline, "cny_yield": cny_yield,
        }
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    saved = save_plan(payload)
    return jsonify({"success": True, "message": "计划已保存，后台将在一分钟内重新计算", "data": saved})


@bp.route("/api/exchange/executions", methods=["GET", "POST"])
def executions():
    if request.method == "GET":
        return jsonify({"success": True, "data": list_executions()})
    incoming = request.get_json(silent=True) or {}
    try:
        eur_amount = float(incoming.get("eur_amount"))
        rate = float(incoming.get("rate"))
        if eur_amount <= 0 or rate <= 0:
            raise ValueError("兑换金额和成交汇率必须大于0")
        execution = add_execution(eur_amount, rate, incoming.get("executed_at"), str(incoming.get("note", "")))
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "message": "实际兑换已记录，后台将在一分钟内重新计算", "data": execution})


@bp.get("/api/exchange/computation_status")
def computation_status():
    jobs = [row for row in list_job_states() if row["name"].startswith("exchange.")]
    return jsonify({"success": True, "data": jobs})
