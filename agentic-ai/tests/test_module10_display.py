from types import SimpleNamespace

import pytest

from module10.models import ReviewResponse
@pytest.mark.parametrize("rich", [False, True])
def test_terminal_preserves_evidence_dates_in_answers_and_values(monkeypatch, capsys, rich):
    from demos import module10_demo as demo
    monkeypatch.setattr(demo, "_RICH", rich)
    text = "Evidence recorded on September 19, 2026 at 9:53 AM."
    body = {
        "status": "completed", "release": {"recovery_planning_enabled": True, "reason": {"kind": "TARGET_MATCH"}},
        "review": text, "recovery_plan": {"text": text, "path": "recovery-plan-demo.md"},
        "events": [{"agent": "DevOps Companion", "tool": "inspect_deployment", "status": "completed", "summary": text}],
    }
    context = SimpleNamespace(runtime=SimpleNamespace(mode="cloud"), base_url="https://demo.example",
                              request=lambda team: ReviewResponse(200, body))
    demo.show_investigation(context, planning_enabled=True)
    demo.show_values("Evidence", [("Recorded", "2026-09-19T14:53:56Z")])
    output = capsys.readouterr().out
    for original in ("September 19", "2026", "9:53", "14:53"):
        assert original in output
    assert "date omitted" not in output and "timestamp omitted" not in output
    assert body["review"] == text, "Stored evidence must retain the original text"
