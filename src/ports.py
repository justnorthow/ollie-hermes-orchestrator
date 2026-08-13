from dataclasses import dataclass
from typing import Iterable

GATEWAY_BASE = 8642
DASHBOARD_BASE = 9119
# Ports occupied by platform services rather than agent dashboards.
DASHBOARD_RESERVED = {9120, 9123}  # cortex, orchestrator


@dataclass(frozen=True)
class PortAllocation:
    gateway: int
    dashboard: int


def _next_free(start: int, taken: set[int], reserved: set[int] = frozenset()) -> int:
    p = start
    while p in taken or p in reserved:
        p += 1
    return p


def allocate_ports(
    existing_gateways: Iterable[int],
    existing_dashboards: Iterable[int],
) -> PortAllocation:
    g_taken = set(existing_gateways) | {GATEWAY_BASE}  # base is always default profile
    d_taken = set(existing_dashboards) | {DASHBOARD_BASE}
    gateway = _next_free(GATEWAY_BASE + 1, g_taken)
    # Dashboards skip 9120 (cortex). Start scanning from 9121.
    dashboard = _next_free(DASHBOARD_BASE + 2, d_taken, DASHBOARD_RESERVED)
    return PortAllocation(gateway=gateway, dashboard=dashboard)
