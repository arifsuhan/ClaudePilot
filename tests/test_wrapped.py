from claudepilot import wrapped

NOW = 2_000_000_000.0
DAY = 86_400


def ev(kind, ago, **kw):
    return {"kind": kind, "ts": NOW - ago, **kw}


def test_compute_summarises_the_week():
    events = [
        ev("route", 100, current="opus", recommended="haiku", action="switch"),
        ev("route", 200, current="opus", recommended="opus", action="match"),
        ev("route", 300, current="sonnet", recommended="haiku", action="stay"),
        ev("route", 8 * DAY, current="opus", recommended="haiku"),  # outside the window
        ev("auto_route", 90, status="ok"),
        ev("tokens", 100, level="lite"),
        ev("tokens", 200, level="caveman"),
        ev("guard", 50, file=".env", decision="ask"),
        ev("alert", 60, message="5h usage at 90%"),
    ]
    history = [
        {"ts": NOW - 5000, "five_hour": 50, "seven_day": 20},
        {"ts": NOW - 4000, "five_hour": 92, "seven_day": 30},
        {"ts": NOW - 3000, "five_hour": 100, "seven_day": 35},
        {"ts": NOW - 2000, "five_hour": 5, "seven_day": 36},
        {"ts": NOW - 1000, "five_hour": 100, "seven_day": 50},
    ]
    w = wrapped.compute(events, history, NOW)
    assert w.prompts == 3
    assert w.model_mix == {"opus": 2, "sonnet": 1}
    assert w.oversized == 2 and w.kept_for_cache == 1 and w.auto_switches == 1
    assert w.token_levels == {"lite": 1, "caveman": 1}
    assert w.guard_hits == 1 and w.alerts == 1
    assert w.peak_five_hour == 100 and w.peak_seven_day == 50
    assert w.limit_hits == 2 and w.near_misses == 1
    assert w.busiest_hour is not None


def test_lines_are_shareable_and_private():
    w = wrapped.compute([ev("route", 10, current="opus", recommended="haiku"),
                         ev("guard", 5, file=".env", decision="ask")], [], NOW)
    text = "\n".join(t for t, _ in wrapped.lines(w))
    assert "Prompts routed: 1" in text and "smaller model" in text and ".env" in text
    assert "stepped in once" in text


def test_empty_state():
    text = "\n".join(t for t, _ in wrapped.lines(wrapped.compute([], [], NOW)))
    assert "No data yet" in text
