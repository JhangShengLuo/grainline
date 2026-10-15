from pathlib import Path

SPECS = Path(__file__).resolve().parents[2] / "specs" / "flow"

import pytest

from app.sim import SimConfig, World, simulate
from app.tracking import TrackingPlan, load_plans

SMALL = SimConfig(visitors=300)


@pytest.fixture(scope="session")
def plans() -> dict[int, TrackingPlan]:
    return load_plans(SPECS / "tracking")


@pytest.fixture(scope="session")
def worlds(plans: dict[int, TrackingPlan]) -> dict[int, World]:
    return {v: simulate(p, SMALL) for v, p in plans.items()}

import duckdb

from app import warehouse


@pytest.fixture(scope="session")
def cons(worlds: dict[int, World]) -> dict[int, duckdb.DuckDBPyConnection]:
    return {v: warehouse.build(w) for v, w in worlds.items()}
