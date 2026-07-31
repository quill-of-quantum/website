import math
import random
import statistics
from datetime import date, datetime


CONFIDENCE_Z = 2.576
MIN_PATHS = 5000
BATCH_PATHS = 5000
TARGET_PATHS = 20000
MAX_PATHS = 50000
PROBABILITY_ERROR_PCT = 1.0
DECISION_STABILITY_EUR = 50.0


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _returns(values):
    return [math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i - 1] > 0]


def _percentile_rank(values, current):
    return 100.0 * sum(value <= current for value in values) / len(values) if values else 50.0


def _market_state(values):
    current = values[-1]
    window = values[-min(2520, len(values)):]
    ou_window = window[-min(504, len(window)):]
    mean = statistics.fmean(ou_window)
    xs, ys = ou_window[:-1], ou_window[1:]
    x_mean, y_mean = statistics.fmean(xs), statistics.fmean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    beta = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator if denominator else 1.0
    alpha = y_mean - beta * x_mean
    equilibrium = alpha / (1 - beta) if 0 < beta < 0.999 else mean
    half_life = -math.log(2) / math.log(beta) if 0 < beta < 1 else None

    recent_returns = _returns(window[-min(252, len(window)):])
    weights = [0.94 ** (len(recent_returns) - 1 - i) for i in range(len(recent_returns))]
    weighted_variance = sum(w * r * r for w, r in zip(weights, recent_returns)) / sum(weights) if weights else 0
    annual_vol = math.sqrt(weighted_variance * 252) * 100
    ma10 = statistics.fmean(values[-10:])
    ma30 = statistics.fmean(values[-30:])
    trend = (ma10 / ma30 - 1) * 100
    regime = "人民币偏强" if trend < -0.35 else "欧元偏强" if trend > 0.35 else "震荡"

    return {
        "current": round(current, 4),
        "percentile": round(_percentile_rank(window, current), 1),
        "equilibrium": round(equilibrium, 4),
        "distance_to_equilibrium_pct": round((current / equilibrium - 1) * 100, 2),
        "half_life_days": round(half_life, 1) if half_life and half_life < 1000 else None,
        "annualized_volatility_pct": round(annual_vol, 2),
        "regime": regime,
        "trend_pct": round(trend, 2),
    }


def _quantile(series, q):
    ordered = sorted(series)
    return round(ordered[int((len(ordered) - 1) * q)], 4)


def _probability_error(successes, paths):
    probability = successes / paths if paths else 0.5
    return CONFIDENCE_Z * math.sqrt(max(probability * (1 - probability), 1e-12) / paths) * 100


def _base_decision(plan, state, lower_probability, days_left):
    remaining = max(0.0, float(plan["target_eur"]) - float(plan["purchased_eur"]))
    periods = max(1, math.ceil(max(days_left, 1) / 30))
    baseline = remaining / periods

    historical_score = 100 - state["percentile"]
    equilibrium_score = _clamp(50 - state["distance_to_equilibrium_pct"] * 12)
    carry_score = _clamp(50 - float(plan.get("cny_yield", 0)) * 5)
    attractiveness = _clamp(.55 * historical_score + .35 * equilibrium_score + .10 * carry_score)

    try:
        start = datetime.strptime(plan.get("start_date", date.today().isoformat()), "%Y-%m-%d").date()
        deadline = datetime.strptime(plan["deadline"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        start, deadline = date.today(), date.today()
    total_days = max(1, (deadline - start).days)
    elapsed_ratio = _clamp((date.today() - start).days / total_days, 0, 1)
    completion_ratio = float(plan["purchased_eur"]) / float(plan["target_eur"]) if plan["target_eur"] else 1
    progress_gap = _clamp(elapsed_ratio - completion_ratio, 0, 1)
    urgency = _clamp(100 * (.55 * elapsed_ratio + .45 * progress_gap))
    if days_left <= 30 and remaining:
        urgency = max(urgency, 90)

    # Price position remains descriptive until it proves predictive out of sample.
    # The executable baseline is adjusted only by schedule pressure and validated seasonal rules.
    multiplier = _clamp(1 + 1.4 * urgency / 100, .25, 3.0)
    suggested = min(remaining, round(baseline * multiplier / 50) * 50)
    if days_left <= 30:
        suggested = remaining
    return suggested, baseline, multiplier, attractiveness, urgency


def _adaptive_simulation(values, state, plan, days_left):
    current = values[-1]
    recent_returns = _returns(values[-min(252, len(values)):])
    daily_vol = statistics.pstdev(recent_returns) if len(recent_returns) > 1 else 0.005
    equilibrium = state["equilibrium"]
    beta = 0.985
    steps = max(1, min(252, round(max(days_left, 1) * 252 / 365)))
    step30 = min(steps, max(1, round(30 * 252 / 365)))
    step90 = min(steps, max(1, round(90 * 252 / 365)))
    seed = f"{current:.6f}-{days_left}-{len(values)}-{plan.get('revision', 0)}"
    rng = random.Random(seed)

    minima, averages, endings = [], [], []
    lower1 = lower3 = lower30 = lower90 = higher90 = 0
    decision_history = []
    stable = False
    probability_error = 100.0

    for path_index in range(MAX_PATHS):
        price = current
        total = 0.0
        minimum = current
        minimum30 = current
        minimum90 = current
        price90 = current
        for step in range(1, steps + 1):
            price = equilibrium + beta * (price - equilibrium) + price * daily_vol * rng.gauss(0, 1)
            price = max(price, 0.01)
            total += price
            minimum = min(minimum, price)
            if step <= step30:
                minimum30 = min(minimum30, price)
            if step <= step90:
                minimum90 = min(minimum90, price)
                price90 = price
        minima.append(minimum)
        averages.append(total / steps)
        endings.append(price)
        lower1 += minimum < current * .99
        lower3 += minimum < current * .97
        lower30 += minimum30 < current
        lower90 += minimum90 < current
        higher90 += price90 > current * 1.03

        paths = path_index + 1
        if paths % BATCH_PATHS:
            continue
        lower_probability = lower1 / paths * 100
        probability_error = _probability_error(lower1, paths)
        suggestion = _base_decision(plan, state, lower_probability, days_left)[0]
        decision_history.append(suggestion)
        decision_stable = len(decision_history) >= 3 and max(decision_history[-3:]) - min(decision_history[-3:]) <= DECISION_STABILITY_EUR
        stable = paths >= TARGET_PATHS and probability_error <= PROBABILITY_ERROR_PCT and decision_stable
        if stable:
            break

    paths = len(minima)
    return {
        "paths": paths,
        "horizon_days": days_left,
        "stable": stable,
        "stability_label": "高" if stable else "偏低",
        "confidence_level_pct": 99,
        "probability_error_pct": round(probability_error, 2),
        "decision_stability_eur": DECISION_STABILITY_EUR,
        "lower_than_today_probability": round(lower1 / paths * 100, 1),
        "lower_3pct_probability": round(lower3 / paths * 100, 1),
        "lower_30d_probability": round(lower30 / paths * 100, 1),
        "lower_90d_probability": round(lower90 / paths * 100, 1),
        "higher_3pct_90d_probability": round(higher90 / paths * 100, 1),
        "minimum": {"p10": _quantile(minima, .1), "p50": _quantile(minima, .5), "p90": _quantile(minima, .9)},
        "average": {"p10": _quantile(averages, .1), "p50": _quantile(averages, .5), "p90": _quantile(averages, .9)},
        "ending": {"p10": _quantile(endings, .1), "p50": _quantile(endings, .5), "p90": _quantile(endings, .9)},
    }


def _level(score, kind):
    if kind == "attractiveness":
        return "很划算" if score >= 81 else "比较划算" if score >= 61 else "中性" if score >= 41 else "偏贵" if score >= 21 else "很不划算"
    return "很高" if score >= 81 else "较高" if score >= 61 else "适中" if score >= 41 else "较低" if score >= 21 else "很低"


def _intensity(multiplier):
    return "建议等待" if multiplier < .5 else "偏少" if multiplier < .8 else "常规" if multiplier <= 1.2 else "偏多" if multiplier <= 1.8 else "明显加快"


def build_indicator_history(rate_payload):
    dates = rate_payload.get("dates") or []
    values = [float(value) for value in rate_payload.get("eur_cny", [])]
    if len(values) < 60 or len(dates) != len(values):
        raise ValueError("历史指标至少需要60个完整汇率数据点")
    output = {"dates": [], "eur_cny": [], "attractiveness": [], "higher_probability": [], "lower_probability": []}
    horizon = 63  # roughly 90 calendar days in trading days
    beta = .985
    for index in range(59, len(values)):
        history = values[:index + 1]
        current = history[-1]
        trailing = history[-min(252, len(history)):]
        recent_returns = _returns(history[-60:])
        daily_vol = statistics.pstdev(recent_returns) if len(recent_returns) > 1 else .005
        equilibrium = statistics.fmean(history[-60:])
        expected = equilibrium + beta ** horizon * (current - equilibrium)
        variance_factor = (1 - beta ** (2 * horizon)) / (1 - beta ** 2)
        deviation = max(current * daily_vol * math.sqrt(variance_factor), .0001)

        def cdf(value):
            return .5 * (1 + math.erf((value - expected) / (deviation * math.sqrt(2))))

        lower_probability = _clamp(cdf(current * .99) * 100)
        higher_probability = _clamp((1 - cdf(current * 1.01)) * 100)
        percentile = _percentile_rank(trailing, current)
        distance = (current / equilibrium - 1) * 100
        historical_score = 100 - percentile
        equilibrium_score = _clamp(50 - distance * 12)
        future_score = 100 - lower_probability
        attractiveness = _clamp(.50 * historical_score + .30 * equilibrium_score + .20 * future_score)
        output["dates"].append(dates[index])
        output["eur_cny"].append(round(current, 4))
        output["attractiveness"].append(round(attractiveness, 1))
        output["higher_probability"].append(round(higher_probability, 1))
        output["lower_probability"].append(round(lower_probability, 1))
    output["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    output["definition"] = "未来90天相对当前上涨或下跌超过1%的模型概率"
    return output


def build_analysis(rate_payload, plan, seasonality=None, pattern_report=None):
    values = [float(value) for value in rate_payload.get("eur_cny", [])]
    if len(values) < 30:
        raise ValueError("至少需要30个 EUR/CNY 数据点")
    latest = rate_payload.get("latest_eur")
    if latest and rate_payload.get("latest_date") != (rate_payload.get("dates") or [None])[-1]:
        values.append(float(latest))

    state = _market_state(values)
    try:
        deadline = datetime.strptime(plan["deadline"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        deadline = date.today()
    days_left = max(0, (deadline - date.today()).days)
    remaining = max(0.0, float(plan["target_eur"]) - float(plan["purchased_eur"]))
    suggested, baseline, multiplier, attractiveness, urgency = _base_decision(
        plan, state, 0, days_left
    )
    active_seasonal_rules = (seasonality or {}).get("current", {}).get("active_rules", [])
    seasonal_signal = 0.0
    for rule in active_seasonal_rules:
        strength = max(0, (rule["test"]["hit_rate_pct"] - 50) / 50)
        seasonal_signal += strength if rule["direction"] == "上涨" else -strength
    seasonal_adjustment = max(-.30, min(.30, seasonal_signal))
    pattern_adjustment = 0.0
    pattern_approved = bool((pattern_report or {}).get("approved_for_decision"))
    pattern_forecast = (pattern_report or {}).get("forecast", {}).get("20", {})
    pattern_validation = (pattern_report or {}).get("walk_forward", {}).get("metrics", {}).get("20", {})
    if pattern_approved and pattern_validation.get("approved") and pattern_forecast:
        directional_edge = (pattern_forecast.get("up_probability_pct", 50) - pattern_forecast.get("down_probability_pct", 50)) / 100
        pattern_adjustment = max(-.20, min(.20, directional_edge * .5))
    total_model_adjustment = max(-.30, min(.30, seasonal_adjustment + pattern_adjustment))
    if days_left > 30 and remaining:
        suggested = min(remaining, round(suggested * (1 + total_model_adjustment) / 50) * 50)
        multiplier *= 1 + total_model_adjustment
    interval_margin = max(50, round(baseline * .08 / 50) * 50)
    lower_suggestion = max(0, suggested - interval_margin)
    upper_suggestion = min(remaining, suggested + interval_margin)

    reasons_for = [f"按剩余需求和期限，常规月度进度为{baseline:.0f}欧元"]
    reasons_against = []
    if urgency >= 60:
        reasons_for.append("剩余时间或计划进度带来较高执行压力")
    else:
        reasons_against.append("当前执行压力不高，不额外加快进度")
    for rule in active_seasonal_rules:
        message = f"有效周期：{rule['label']}，样本外命中率{rule['test']['hit_rate_pct']:.1f}%"
        (reasons_for if rule["direction"] == "上涨" else reasons_against).append(message)
    if not active_seasonal_rules:
        reasons_against.append("当前没有通过样本外验证且正在生效的周期规律，不做周期调整")
    if pattern_approved:
        destination = reasons_for if pattern_adjustment > 0 else reasons_against
        destination.append(
            f"隐含形态模型已通过验证：未来20日上涨{pattern_forecast.get('up_probability_pct', 0):.1f}%、"
            f"下跌{pattern_forecast.get('down_probability_pct', 0):.1f}%"
        )
    else:
        reasons_against.append("隐含形态模型尚未超过简单基准，不参与金额调整")

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": rate_payload.get("latest_date") or rate_payload.get("dates", [None])[-1],
        "quote_signature": "|".join(str(value or "") for value in (
            rate_payload.get("latest_date"), rate_payload.get("latest_eur"), rate_payload.get("latest_usd"),
            (rate_payload.get("dates") or [None])[-1], (rate_payload.get("eur_cny") or [None])[-1],
            (rate_payload.get("usd_cny") or [None])[-1],
        )),
        "plan": {**plan, "remaining_eur": remaining, "days_left": days_left},
        "market": state,
        "scores": {
            "attractiveness": round(attractiveness, 1),
            "attractiveness_label": _level(attractiveness, "attractiveness"),
            "urgency": round(urgency, 1),
            "urgency_label": _level(urgency, "urgency"),
        },
        "simulation": {"paths": 0, "stable": False, "status": "均值回归模拟已停用，不参与周期版决策"},
        "decision": {
            "suggested_eur": suggested,
            "suggested_range_eur": {"min": lower_suggestion, "max": upper_suggestion},
            "estimated_cny": round(suggested * state["current"], 2),
            "remaining_after_eur": round(remaining - suggested, 2),
            "baseline_monthly_eur": round(baseline, 2),
            "multiplier": round(multiplier, 2),
            "intensity_label": _intensity(multiplier),
            "seasonal_adjustment_pct": round(seasonal_adjustment * 100, 1),
            "pattern_adjustment_pct": round(pattern_adjustment * 100, 1),
            "total_model_adjustment_pct": round(total_model_adjustment * 100, 1),
            "pattern_model_approved": pattern_approved,
            "active_seasonal_rules": active_seasonal_rules,
            "reasons_for": reasons_for,
            "reasons_against": reasons_against,
        },
        "method": {
            "valuation": "AR(1)/OU近似",
            "volatility": "EWMA波动率（GARCH待接入）",
            "regime": "均线状态（HMM待接入）",
            "simulation": "均值回归Monte Carlo已停用",
            "optimizer": "期限进度基线 + 已验证周期调整",
            "history_days": len(values),
        },
    }
