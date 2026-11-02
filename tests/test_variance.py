"""Variance reference checks that need no database: effects add up exactly (M30-M33)."""
import numpy as np
import pytest

from finance_metrics import DRIVER_LINES, bva_net_income, variance_drivers
from generate_ledgers import generate


@pytest.fixture(scope="module")
def tables():
    return generate(42)


def test_volume_plus_rate_plus_other_equals_variance(tables):
    v = variance_drivers(tables).fillna(0.0)
    total = v.volume_effect + v.rate_effect + v.other_effect
    assert np.allclose(total, v.variance, atol=0.011)


def test_driver_lines_have_effects_and_other_lines_do_not(tables):
    v = variance_drivers(tables)
    driven = v[v.account_id.isin(DRIVER_LINES)]
    rest = v[~v.account_id.isin(DRIVER_LINES)]
    assert driven.volume_effect.notna().all() and (driven.other_effect == 0).all()
    assert rest.volume_effect.isna().all()


def test_line_variances_add_up_to_net_income_variance(tables):
    g = bva_net_income(tables)
    assert np.allclose(g.sum_of_line_variances, g.net_income_variance, atol=0.05)


def test_injected_events_show_up(tables):
    v = variance_drivers(tables)
    v["fy"] = v.period.str[:4]
    a = v.groupby(["fy", "account_id"])[["variance", "volume_effect", "rate_effect"]].sum()
    assert a.loc[("2023", 4000), "volume_effect"] > 0          # originations 3% above plan
    assert a.loc[("2024", 4000), "rate_effect"] < -250_000     # yield cut by competition
    assert a.loc[("2025", 5100), "variance"] < -1_000_000      # charge-offs 50% above plan
    assert a.loc[("2025", 6100), "variance"] < 0               # marketing over budget
    mar24 = v[(v.period == "2024-03") & (v.account_id == 6200)].iloc[0]
    assert mar24.variance < -100_000                           # one-off technology cost
