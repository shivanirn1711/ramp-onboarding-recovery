"""App-level checks with Streamlit's built-in test runner (no browser needed).

    python -m pytest tests -q        (or: python tests/test_app.py)

Checks that the demo data always starts at 0 of 75 reviewed, and that
loading it again after some approvals resets the review to 0.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def reviewed(at):
    plan = at.session_state["plan"]
    return int((plan["approval_status"] != "Pending").sum()), len(plan)


def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_demo_starts_at_zero_and_reload_resets():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert "plan" not in at.session_state            # nothing loaded before the click

    at.button(key="demo_main").click().run()
    assert not at.exception
    assert reviewed(at) == (0, 75)
    assert metric(at, "Messages reviewed") == "0 / 75"
    assert metric(at, "Recoverable") == "67"

    bulk = next(b for b in at.button if b.label == "Approve these")
    bulk.click().run()
    assert reviewed(at)[0] > 0                        # some approvals happened

    reload = next(b for b in at.sidebar.button if b.label == "Load demo data")
    reload.click().run()
    assert not at.exception
    assert reviewed(at) == (0, 75)                    # a fresh load starts from zero again
    assert metric(at, "Messages reviewed") == "0 / 75"


if __name__ == "__main__":
    test_demo_starts_at_zero_and_reload_resets()
    print("ok: demo starts at 0 of 75 and reloading resets it")
