from claudepilot.explorer.git import parse_porcelain


def test_parse_branch_and_changes():
    text = (
        "## main...origin/main [ahead 2, behind 1]\n"
        " M src/app.py\nA  tests/t.py\n D old.py\n?? new.txt\nR  a.py -> b.py\n"
    )
    s = parse_porcelain(text)
    assert s.branch == "main" and s.upstream == "origin/main"
    assert (s.ahead, s.behind) == (2, 1)
    assert ("M", "src/app.py") in s.changes
    assert ("?", "new.txt") in s.changes
    assert ("R", "b.py") in s.changes
    assert s.counts == {"M": 1, "A": 1, "D": 1, "?": 1}


def test_parse_no_upstream_and_fresh_repo():
    assert parse_porcelain("## feature\n").branch == "feature"
    assert parse_porcelain("## No commits yet on main\n").branch == "main"
