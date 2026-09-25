"""E2E: release (git branch) information and the dev-release picker.

The e2e server runs from this checkout, which is a git repository with
``origin/*`` remote-tracking branches, on mock hardware - so everything can
be browsed but switching is refused.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect


@pytest.fixture(scope="module")
def release(server):
    info = server.get("/api/update/info")
    if not info or not info.get("git"):
        pytest.skip("the checkout running the e2e server is not a git repository")
    return info


def test_release_api(server, release):
    assert release["supported"] is False and release["stable_branch"] == "main"
    assert re.fullmatch(r"[0-9a-f]{4,}", release["commit"])
    assert release["channel"] in ("stable", "dev")
    res = server.get("/api/update/branches")
    names = [b["name"] for b in res["branches"]]
    assert "main" in names and "HEAD" not in names
    assert [b for b in res["branches"] if b["stable"]] == [b for b in res["branches"] if b["name"] == "main"]
    status, body = server.post("/update", {"branch": "main"})
    assert status == 400 and "not supported" in body["error"]
    status, body = server.post("/update", {"branch": "--upload-pack=touch /tmp/pwned"})
    assert status == 400


def test_release_card_shows_the_running_release(ui, release):
    page = ui.goto("system")
    card = page.locator("section[aria-label=Updates]")
    expected = f"detached at {release['commit']}" if release["detached"] else release["branch"]
    expect(card.locator("[data-branch]")).to_have_text(expected)
    expect(card.locator("[data-channel]")).to_have_text("Stable" if release["channel"] == "stable" else "Dev")
    expect(card.locator("[data-commit]")).to_contain_text(release["commit"])
    if release["branch"] == "main":
        expect(card.locator("[data-switch-stable]")).to_be_hidden()
    else:
        expect(card.locator("[data-switch-stable]")).to_contain_text("Switch to stable (main)")


def test_branch_picker_filter_and_refusal(ui, release):
    page = ui.goto("system")
    page.locator("[data-switch-dev]").click()
    dlg = page.locator("dialog[open]")
    expect(dlg).to_contain_text("Choose a dev release")
    main = dlg.locator('[data-branch-opt="main"]')
    expect(main).to_be_visible()
    expect(main.locator(".chip")).to_contain_text("Stable")
    total = dlg.locator("[data-branch-opt]").count()
    assert total >= 1

    # Filter narrows the list; a query without matches shows the empty note.
    dlg.locator("[data-branch-filter]").fill("zz-no-such-branch")
    expect(dlg.locator("[data-branch-opt]:visible")).to_have_count(0)
    expect(dlg.locator("[data-branch-empty]")).to_be_visible()
    dlg.locator("[data-branch-filter]").fill("mai")
    expect(main).to_be_visible()
    visible = dlg.locator("[data-branch-opt]:visible").count()
    assert 1 <= visible <= total
    for name in dlg.locator("[data-branch-opt]:visible").evaluate_all("els => els.map(e => e.dataset.branchOpt)"):
        assert "mai" in name.lower()
    installed = release["branch"] == "main"
    if installed:   # the installed branch is shown but not selectable
        expect(main).to_be_disabled()

    # Cancel closes without doing anything.
    dlg.locator("button[value=cancel]").click()
    expect(page.locator("dialog[open]")).to_have_count(0)
    if installed:
        return
    # Pick a branch -> confirm -> refused on mock hardware.
    page.locator("[data-switch-dev]").click()
    page.locator("dialog[open] [data-branch-filter]").fill("main")
    page.locator('dialog[open] [data-branch-opt="main"]').click()
    expect(page.locator("dialog[open]")).to_contain_text("Switch to the stable release?")
    ui.confirm()
    ui.toast("not supported")
    expect(page.locator(".overlay")).to_have_count(0)


def test_dev_branch_confirm_warns(ui, release, server):
    dev = [b for b in server.get("/api/update/branches")["branches"] if not b["stable"] and not b["current"]]
    if not dev:
        pytest.skip("no dev branch on origin")
    name = dev[0]["name"]
    page = ui.goto("system")
    page.locator("[data-switch-dev]").click()
    page.locator(f"dialog[open] [data-branch-opt={json.dumps(name)}]").click()
    dlg = page.locator("dialog[open]")
    expect(dlg).to_contain_text("Install a dev release?")
    expect(dlg).to_contain_text(name)
    expect(dlg.locator(".dlg-warn")).to_contain_text("unstable")
    ui.confirm()
    ui.toast("not supported")


def test_release_card_on_phone(phone, release):
    page = phone.goto("system")
    card = page.locator("section[aria-label=Updates]")
    card.scroll_into_view_if_needed()
    expect(card.locator("[data-commit]")).to_contain_text(release["commit"])
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    page.locator("[data-switch-dev]").click()
    expect(page.locator('dialog[open] [data-branch-opt="main"]')).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    box = page.locator("dialog[open]").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390


def test_simulated_update_available_and_errors(ui):
    """Stubbed API answers: update available, detached HEAD and git errors."""
    page = ui.page
    info = {"supported": True, "stable_branch": "main", "git": True, "branch": "main", "detached": False,
            "channel": "stable", "commit": "abc1234", "commit_date": "2026-01-01T10:00:00+00:00",
            "subject": "Stable", "upstream": "origin/main", "behind": 3, "checked": True, "error": None}
    page.route("**/api/update/info*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                         body=json.dumps(info)))
    page.route("**/api/update/branches", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"branches": [], "current": None, "stable_branch": "main", "refreshed": False,
                         "warning": None, "error": "Branches unavailable: git is not installed"})))
    ui.goto("system")
    card = page.locator("section[aria-label=Updates]")
    expect(card.locator("[data-update-available]")).to_be_visible()
    expect(card.locator("[data-update-status]")).to_contain_text("3 new commits on origin/main")
    expect(card.locator("[data-switch-stable]")).to_be_hidden()
    expect(card.locator("[data-switch-dev]")).to_contain_text("Switch to dev release")
    card.locator("[data-switch-dev]").click()
    ui.toast("git is not installed")
    expect(page.locator("dialog[open]")).to_have_count(0)

    info.update(branch=None, detached=True, channel="dev", upstream=None, behind=None, checked=False)
    page.reload()
    ui.wait_connected()
    expect(card.locator("[data-branch]")).to_have_text("detached at abc1234")
    expect(card.locator("[data-channel]")).to_have_text("Dev")
    expect(card.locator("[data-update-status]")).to_contain_text("Not on a branch")
    expect(card.locator("[data-switch-stable]")).to_be_visible()
