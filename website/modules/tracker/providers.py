"""物流追踪方式注册与分发。

新增追踪方式时，只需在 ``TRACKING_PROVIDERS`` 中登记并提供 fetch/parse
函数；未提供实现的方式会自动在前端显示为不可选择。
"""

from dataclasses import dataclass
from typing import Callable, Optional

from modules.tracker.browser import fetch_tracking, parse_tracking_result


@dataclass(frozen=True)
class TrackingProvider:
    key: str
    name: str
    fetch: Optional[Callable] = None
    parse: Optional[Callable] = None

    @property
    def configured(self):
        return callable(self.fetch) and callable(self.parse)


TRACKING_PROVIDERS = {
    provider.key: provider
    for provider in (
        TrackingProvider("transfer_1", "转运1", fetch_tracking, parse_tracking_result),
        TrackingProvider("transfer_2", "转运2"),
        TrackingProvider("transfer_3", "转运3"),
    )
}

DEFAULT_PROVIDER = "transfer_1"


def get_provider(key):
    return TRACKING_PROVIDERS.get(key)


def public_provider_list():
    return [
        {"key": item.key, "name": item.name, "configured": item.configured}
        for item in TRACKING_PROVIDERS.values()
    ]
