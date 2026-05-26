from src.ports import allocate_ports, PortAllocation


def test_first_allocation_returns_default_pair():
    a = allocate_ports(existing_gateways=[], existing_dashboards=[])
    # default gateway lives on 8642, default dashboard on 9119
    assert a == PortAllocation(gateway=8643, dashboard=9121)


def test_skips_cortex_dashboard_port_9120():
    a = allocate_ports(existing_gateways=[8642, 8643], existing_dashboards=[9119, 9121])
    assert a.dashboard != 9120  # cortex
    assert a == PortAllocation(gateway=8644, dashboard=9122)


def test_returns_next_free_after_dense_allocation():
    a = allocate_ports(
        existing_gateways=[8642, 8643, 8644, 8645],
        existing_dashboards=[9119, 9121, 9122, 9123],
    )
    assert a == PortAllocation(gateway=8646, dashboard=9124)


def test_finds_gaps():
    a = allocate_ports(existing_gateways=[8642, 8644], existing_dashboards=[9119, 9122])
    assert a == PortAllocation(gateway=8643, dashboard=9121)
