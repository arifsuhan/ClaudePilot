import json

import pytest

from claudepilot import install


def test_install_preserves_user_hooks_and_chains_statusline():
    settings = {
        "model": "sonnet",
        "statusLine": {"type": "command", "command": "~/my-status.sh"},
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]},
    }
    new, chained = install.apply_install(settings)
    assert chained == "~/my-status.sh"
    assert install._owned(new["statusLine"]["command"])
    assert len(new["hooks"]["UserPromptSubmit"]) == 2
    assert new["model"] == "sonnet"
    assert all(install.is_installed(new).values())
    # idempotent
    again, _ = install.apply_install(new)
    assert len(again["hooks"]["UserPromptSubmit"]) == 2

    restored = install.apply_uninstall(new, chained)
    assert restored == settings


def test_uninstall_without_prior_statusline_removes_keys():
    new, chained = install.apply_install({})
    assert install.apply_uninstall(new, chained) == {}


def test_install_roundtrip_on_disk(isolated_dirs):
    path = isolated_dirs / "claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"statusLine": {"type": "command", "command": "orig"}}))
    _, bak, chained = install.install(path)
    assert bak.exists() and chained == "orig"
    install.uninstall(path)
    assert json.loads(path.read_text()) == {"statusLine": {"type": "command", "command": "orig"}}


def test_refuses_broken_json(isolated_dirs):
    path = isolated_dirs / "claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(ValueError):
        install.install(path)
    assert path.read_text() == "{not json"


def test_guard_hook_registered_with_tool_matcher():
    new, _ = install.apply_install({})
    [entry] = new["hooks"]["PreToolUse"]
    assert "Read" in entry["matcher"] and "Bash" in entry["matcher"]
    assert install._owned(entry["hooks"][0]["command"])


def test_statusline_only_install():
    settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}}
    full, _ = install.apply_install(settings)
    only, _ = install.apply_install(install.apply_uninstall(full, None), hooks=False)
    assert install._owned(only["statusLine"]["command"])
    assert only["hooks"] == settings["hooks"]  # ours removed, user's kept
    assert install.is_installed(only, hooks_expected=False) == {"statusLine": True}


def test_old_style_hook_commands_still_recognised_and_replaced():
    old = {"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "/x/python -m claudepilot.hooks prompt"}]}]}}
    new, _ = install.apply_install(old)
    assert len(new["hooks"]["UserPromptSubmit"]) == 1  # replaced, not duplicated
    assert install.apply_uninstall(new, None) == {}
