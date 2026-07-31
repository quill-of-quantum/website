from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: int
    function: Callable
    run_on_start: bool = True


def registered_jobs():
    from modules.exchange.jobs import compute_analysis, compute_indicators, compute_pattern_model, compute_seasonality, refresh_history, refresh_quotes

    return [
        Job("exchange.refresh_quotes", 60, refresh_quotes),
        Job("exchange.refresh_history", 24 * 60 * 60, refresh_history),
        Job("exchange.compute_indicators", 60 * 60, compute_indicators),
        Job("exchange.compute_seasonality", 24 * 60 * 60, compute_seasonality),
        Job("exchange.compute_pattern_model", 24 * 60 * 60, compute_pattern_model),
        Job("exchange.compute_analysis", 60, compute_analysis),
    ]
