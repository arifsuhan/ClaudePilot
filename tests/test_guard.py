import pytest

from claudepilot import state
from claudepilot.hooks import guard


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/proj/.env"}),
        ("Read", {"file_path": "/proj/.env.production"}),
        ("Read", {"file_path": "/home/u/.ssh/id_ed25519"}),
        ("Read", {"file_path": "/home/u/.aws/credentials"}),
        ("Edit", {"file_path": "certs/server.pem"}),
        ("Grep", {"pattern": "KEY", "path": ".env.local"}),
        ("Glob", {"pattern": "**/.env"}),
        ("Bash", {"command": "cat .env"}),
        ("Bash", {"command": "source .env && npm start"}),
        ("Bash", {"command": "docker run --env-file=.env app"}),
        ("Bash", {"command": "ls ~/.ssh"}),
        ("Bash", {"command": "node app.js < .env"}),
        ("Read", {"file_path": "infra/terraform.tfstate"}),
    ],
)
def test_detects_secrets(tool, tool_input):
    assert guard.check(tool, tool_input)


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/proj/.env.example"}),
        ("Read", {"file_path": "/home/u/.ssh_notes.md"}),
        ("Read", {"file_path": "/home/u/.ssh/id_ed25519.pub"}),
        ("Read", {"file_path": "src/environment.ts"}),
        ("Read", {"file_path": "src/keyboard.py"}),
        ("Bash", {"command": "npm test"}),
        ("Bash", {"command": 'git commit -m "document the .env setup"'}),
        ("Grep", {"pattern": "API_KEY", "path": "src"}),
    ],
)
def test_allows_normal_files(tool, tool_input):
    assert guard.check(tool, tool_input) is None


def test_run_default_asks_and_logs_name_only():
    out = guard.run({"session_id": "s1", "tool_name": "Bash",
                     "tool_input": {"command": "cat /srv/app/.env | grep SECRET_TOKEN=abc123"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    raw = (state.state_dir() / "events.jsonl").read_text()
    assert '"file": ".env"' in raw and "abc123" not in raw and "/srv/app" not in raw


def test_run_deny_and_off_modes():
    state.write_json(state.state_dir() / "guard_mode.json", {"mode": "deny"})
    out = guard.run({"tool_name": "Read", "tool_input": {"file_path": ".env"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    state.write_json(state.state_dir() / "guard_mode.json", {"mode": "off"})
    assert guard.run({"tool_name": "Read", "tool_input": {"file_path": ".env"}}) is None


def test_clean_call_returns_nothing():
    assert guard.run({"tool_name": "Read", "tool_input": {"file_path": "README.md"}}) is None
