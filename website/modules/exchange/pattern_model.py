import math
import gc
from datetime import datetime

import numpy as np
import torch
from torch import nn


WINDOW = 60
HORIZONS = (20, 60)
NEIGHBORS = 50
LATENT_DIM = 16


class AutoEncoder(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(input_dim, 64), nn.GELU(), nn.Linear(64, LATENT_DIM))
        self.decoder = nn.Sequential(nn.Linear(LATENT_DIM, 64), nn.GELU(), nn.Linear(64, input_dim))

    def forward(self, values):
        latent = self.encoder(values)
        return self.decoder(latent)


def _prepare(rate_payload):
    eur_cny = np.asarray(rate_payload["eur_cny"], dtype=np.float64)
    usd_cny = np.asarray(rate_payload["usd_cny"], dtype=np.float64)
    eur_usd = eur_cny / usd_cny
    series = np.column_stack([eur_cny, eur_usd, usd_cny])
    log_returns = np.diff(np.log(series), axis=0)
    windows, end_indexes = [], []
    for end in range(WINDOW, len(series)):
        window = log_returns[end - WINDOW:end]
        windows.append(window.T.reshape(-1))
        end_indexes.append(end)
    return series, np.asarray(windows, dtype=np.float32), np.asarray(end_indexes), rate_payload["dates"]


def _fit_embeddings(windows, train_mask, seed=42):
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    train = windows[train_mask]
    mean = train.mean(axis=0)
    std = train.std(axis=0)
    std[std < 1e-6] = 1e-6
    normalized = np.clip((windows - mean) / std, -8, 8).astype(np.float32)
    model = AutoEncoder(normalized.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.003, weight_decay=1e-5)
    loss_fn = nn.MSELoss()
    tensor = torch.from_numpy(normalized[train_mask])
    model.train()
    generator = torch.Generator().manual_seed(seed)
    for _ in range(15):
        order = torch.randperm(len(tensor), generator=generator)
        for start in range(0, len(tensor), 128):
            batch = tensor[order[start:start + 128]]
            optimizer.zero_grad()
            loss = loss_fn(model(batch), batch)
            loss.backward()
            optimizer.step()
    model.eval()
    with torch.no_grad():
        embeddings = model.encoder(torch.from_numpy(normalized)).numpy()
        reconstruction = float(loss_fn(model(tensor), tensor))
    del model, optimizer, tensor, normalized
    gc.collect()
    return embeddings, reconstruction


def _nearest(embeddings, end_indexes, target_position, latest_allowed_end, count=NEIGHBORS):
    target = embeddings[target_position]
    distances = np.linalg.norm(embeddings - target, axis=1)
    order = np.argsort(distances)
    selected = []
    for position in order:
        end_index = int(end_indexes[position])
        if end_index > latest_allowed_end or abs(end_index - int(end_indexes[target_position])) < WINDOW:
            continue
        if any(abs(end_index - previous_end) < 20 for previous_end, _, _ in selected):
            continue
        selected.append((end_index, float(distances[position]), int(position)))
        if len(selected) >= count:
            break
    return selected


def _forecast(series, dates, embeddings, end_indexes, target_position, allowed_end):
    matches = _nearest(embeddings, end_indexes, target_position, allowed_end)
    current_index = int(end_indexes[target_position])
    current_rate = series[current_index, 0]
    result = {"matches": []}
    for horizon in HORIZONS:
        outcomes = np.asarray([math.log(series[end + horizon, 0] / series[end, 0]) for end, _, _ in matches
                               if end + horizon < len(series)])
        if len(outcomes) < 15:
            continue
        quantiles = np.quantile(outcomes, [.1, .5, .9])
        result[str(horizon)] = {
            "samples": int(len(outcomes)),
            "up_probability_pct": round(float(np.mean(outcomes > 0) * 100), 1),
            "down_probability_pct": round(float(np.mean(outcomes < 0) * 100), 1),
            "expected_change_pct": round(float(np.mean(np.exp(outcomes) - 1) * 100), 3),
            "median_change_pct": round(float((math.exp(float(quantiles[1])) - 1) * 100), 3),
            "rate_quantiles": {
                "p10": round(float(current_rate * math.exp(float(quantiles[0]))), 4),
                "p50": round(float(current_rate * math.exp(float(quantiles[1]))), 4),
                "p90": round(float(current_rate * math.exp(float(quantiles[2]))), 4),
            },
        }
    for end, distance, _ in matches[:12]:
        item = {"date": dates[end], "distance": round(distance, 3), "rate": round(float(series[end, 0]), 4)}
        for horizon in HORIZONS:
            if end + horizon < len(series):
                item[f"change_{horizon}d_pct"] = round(float((series[end + horizon, 0] / series[end, 0] - 1) * 100), 3)
        result["matches"].append(item)
    return result


def _walk_forward(series, windows, end_indexes, dates):
    years = sorted({int(value[:4]) for value in dates})[-5:]
    predictions = {20: [], 60: []}
    for year in years:
        train_mask = np.asarray([int(dates[index][:4]) < year for index in end_indexes])
        if train_mask.sum() < 1000:
            continue
        embeddings, _ = _fit_embeddings(windows, train_mask, seed=year)
        year_positions = [position for position, index in enumerate(end_indexes) if int(dates[index][:4]) == year]
        origins = year_positions[::20]
        for target_position in origins:
            origin = int(end_indexes[target_position])
            forecast = _forecast(series, dates, embeddings, end_indexes, target_position, origin - max(HORIZONS))
            for horizon in HORIZONS:
                data = forecast.get(str(horizon))
                if not data or origin + horizon >= len(series):
                    continue
                actual = math.log(series[origin + horizon, 0] / series[origin, 0])
                predicted = math.log(data["rate_quantiles"]["p50"] / series[origin, 0])
                probability = data["up_probability_pct"] / 100
                predictions[horizon].append((actual, predicted, probability))
    metrics = {}
    for horizon, rows in predictions.items():
        if not rows:
            continue
        actual = np.asarray([row[0] for row in rows])
        predicted = np.asarray([row[1] for row in rows])
        probabilities = np.asarray([row[2] for row in rows])
        direction_accuracy = np.mean(np.sign(actual) == np.sign(predicted))
        model_mae = np.mean(np.abs(actual - predicted))
        baseline_mae = np.mean(np.abs(actual))
        brier = np.mean((probabilities - (actual > 0)) ** 2)
        metrics[str(horizon)] = {
            "predictions": len(rows), "direction_accuracy_pct": round(float(direction_accuracy * 100), 1),
            "model_mae_pct": round(float(model_mae * 100), 3),
            "unchanged_baseline_mae_pct": round(float(baseline_mae * 100), 3),
            "beats_unchanged_baseline": bool(model_mae < baseline_mae),
            "brier_score": round(float(brier), 4),
            "approved": bool(len(rows) >= 40 and direction_accuracy >= .54 and model_mae < baseline_mae),
        }
    return {"years": years, "metrics": metrics}


def build_pattern_report(rate_payload):
    series, windows, end_indexes, dates = _prepare(rate_payload)
    usable_mask = end_indexes <= len(series) - max(HORIZONS) - 1
    embeddings, reconstruction = _fit_embeddings(windows, usable_mask)
    latest_position = len(end_indexes) - 1
    forecast = _forecast(series, dates, embeddings, end_indexes, latest_position, len(series) - max(HORIZONS) - 1)
    validation = _walk_forward(series, windows, end_indexes, dates)
    approved = any(metric.get("approved") for metric in validation["metrics"].values())
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"first_date": dates[0], "last_date": dates[-1], "points": len(dates),
                   "variables": ["EUR/CNY", "EUR/USD", "USD/CNY"]},
        "model": {"type": "非线性时序自编码器 + 隐空间近邻", "window_days": WINDOW,
                  "latent_dimensions": LATENT_DIM, "neighbors": NEIGHBORS,
                  "reconstruction_loss": round(reconstruction, 5)},
        "forecast": forecast, "walk_forward": validation,
        "approved_for_decision": approved,
        "status": "已通过最低验证门槛" if approved else "未超过简单基准，仅展示研究结果",
    }
