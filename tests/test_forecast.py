from claudepilot.usage.forecast import forecast

NOW = 1_000_000.0


def hist(points):
    return [{"ts": NOW - ago, "five_hour": pct, "seven_day": None} for ago, pct in points]


def test_linear_forecast_hits_before_reset():
    # 10 points per 30 min = 20/hour; at 60% there are 2h left
    fc = forecast(hist([(1800, 50)]), "five_hour", 60, resets_at=NOW + 4 * 3600, now=NOW)
    assert fc is not None
    assert abs(fc.rate_per_hour - 20) < 0.01
    assert abs(fc.hits_at - (NOW + 2 * 3600)) < 1
    assert not fc.resets_first


def test_reset_comes_first():
    fc = forecast(hist([(1800, 50)]), "five_hour", 60, resets_at=NOW + 3600, now=NOW)
    assert fc.resets_first and fc.hits_at is None


def test_needs_enough_span():
    assert forecast(hist([(60, 50)]), "five_hour", 60, None, NOW) is None


def test_flat_usage_no_forecast():
    assert forecast(hist([(1800, 60)]), "five_hour", 60, None, NOW) is None


def test_ignores_points_before_a_reset():
    # usage dropped from 90 to 5 => reset; only post-reset points count
    fc = forecast(hist([(3000, 80), (2400, 90), (1800, 5)]), "five_hour", 15, NOW + 4 * 3600, NOW)
    assert fc is not None
    assert abs(fc.rate_per_hour - 20) < 0.01
