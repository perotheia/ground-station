"""Regression test of the app rollout path in the GS web UI (spec hil-app-ota, G2/G3) —
headless Chromium against the MOCK gs-api.

    cd web && npm run build
    <venv with playwright>/bin/python tests/rollout_spec.py

It starts `node mock/server.mjs --dist dist`, then walks: Releases lists the Mender artifacts
(signed / unsigned, the runtime each needs) -> Rollouts, New Rollout: only accepted Mender devices
to pick -> an unsigned artifact is warned about before it can be created -> a bookworm build onto
the trixie AI-64 is refused by the GS (422, the reason shown) -> wc 0.1.0 to ONE device: the
Rollout detail shows the device progressing live (outcome polled every 5 s, never two requests in
flight, polling stops when final) and ends in success -> wc 0.1.1: "rolled back to
wc-0.1.0-trixie-arm64" with the commit-gate lines of the device log -> the unsigned artifact,
confirmed: failure with the signature error -> the Rollouts table carries the outcomes.
Exit code 0 = all passed.

env: CHROME (chromium binary), SHOTS_DIR (screenshots), MOCK_PORT, MOCK_ROLLOUT_SECONDS.
"""
import os
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

WEB = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("MOCK_PORT", "18098"))
BASE = f"http://127.0.0.1:{PORT}/"
ROLL_S = float(os.environ.get("MOCK_ROLLOUT_SECONDS", "12"))
SHOTS = Path(os.environ.get("SHOTS_DIR", "/bigdata/work/architect/ui-test/shots-rollout"))
CHROME = os.environ.get("CHROME", str(Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome"))

results = []
n = [0]


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""), flush=True)


def shot(page, name):
    n[0] += 1
    f = SHOTS / f"{n[0]:02d}-{name}.png"
    page.screenshot(path=str(f), full_page=True)
    print("shot", f, flush=True)


def nav(page, label):
    page.locator("aside nav div.nav-item", has_text=label).first.click()


def outcome_of(page):
    return page.get_attribute("[data-testid=rollout-outcome]", "data-outcome")


def wait_outcome(page, outcomes, timeout_s):
    try:
        page.wait_for_function(
            "(o) => { const d = document.querySelector('[data-testid=rollout-outcome]');"
            " return d && o.includes(d.dataset.outcome) }", arg=outcomes, timeout=timeout_s * 1000)
    except Exception as e:  # noqa: BLE001
        shot(page, "TIMEOUT-waiting-for-" + "-".join(outcomes))
        print("timeout:", e, flush=True)
    return outcome_of(page)


def open_new(page):
    page.get_by_test_id("new-rollout-btn").click()
    dlg = page.get_by_test_id("app-rollout")
    expect(dlg).to_be_visible(timeout=10000)
    expect(dlg.locator("select[name=artifact] option[value='wc-0.1.0-trixie-arm64']")).to_be_attached(timeout=10000)
    expect(dlg.get_by_test_id("ro-dev-ai64-proto")).to_be_visible(timeout=10000)
    return dlg


def main():
    if not (WEB / "dist" / "index.html").exists():
        sys.exit("build first: npm run build")
    SHOTS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MOCK_ROLLOUT_SECONDS=str(ROLL_S))
    mock = subprocess.Popen(["node", str(WEB / "mock" / "server.mjs"), "--port", str(PORT), "--dist", str(WEB / "dist")],
                            env=env)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        urllib.request.urlopen(urllib.request.Request(BASE + "__mock/reset", method="POST"))
        run()
    finally:
        mock.terminate()
        mock.wait(5)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


def run():
    with sync_playwright() as p:
        kw = {"executable_path": CHROME} if Path(CHROME).exists() else {}
        br = p.chromium.launch(headless=True, **kw)
        ctx = br.new_context(viewport={"width": 1600, "height": 1100})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # outcome-poll bookkeeping: per deployment id, request times + max concurrent in flight
        polls = defaultdict(list)
        inflight = defaultdict(int)
        maxinflight = defaultdict(int)
        detail_inflight = defaultdict(int)        # per deployment, outcome + rollout requests together
        detail_max = defaultdict(int)

        def kind(url):
            path = url.split("?")[0]
            parts = path.split("/")
            if "/api/deployments/" in path and parts[-1] in ("outcome", "rollout"):
                return parts[-2], parts[-1]
            return None, None

        def on_req(r):
            i, k = kind(r.url)
            if i:
                detail_inflight[i] += 1
                detail_max[i] = max(detail_max[i], detail_inflight[i])
            if k == "outcome":
                polls[i].append(time.time())
                inflight[i] += 1
                maxinflight[i] = max(maxinflight[i], inflight[i])

        def on_done(r):
            i, k = kind(r.url)
            if i:
                detail_inflight[i] -= 1
            if k == "outcome":
                inflight[i] -= 1
        page.on("request", on_req)
        page.on("requestfinished", on_done)
        page.on("requestfailed", on_done)

        page.goto(BASE)

        # ── 1. Releases: the Mender artifacts ──
        nav(page, "Releases")
        arts = page.get_by_test_id("mender-artifacts")
        expect(arts.get_by_test_id("artifact-wc-0.1.0-trixie-arm64")).to_be_visible(timeout=10000)
        rows = arts.locator("tbody tr[data-testid^=artifact-]")
        check("Releases lists the Mender artifacts", rows.count() == 5, rows.count())
        unsigned = arts.get_by_test_id("artifact-wc-0.1.2-trixie-arm64")
        check("an unsigned artifact is shown as such", unsigned.get_by_test_id("unsigned").is_visible())
        signed = arts.get_by_test_id("artifact-wc-0.1.0-trixie-arm64")
        stxt = signed.inner_text()
        check("a signed artifact: signed, version, kind, device type, runtime it needs, size",
              signed.get_by_test_id("signed").is_visible() and "0.1.0" in stxt and "theia-swp" in stxt
              and "theia-rig" in stxt and "trixie-arm64 runtime" in stxt and "KiB" in stxt, stxt)
        shot(page, "releases-mender-artifacts")

        # ── 2. New Rollout: artifact + accepted Mender devices only ──
        nav(page, "Rollouts")
        dlg = open_new(page)
        check("devices: accepted Mender devices only (no pending, no colony-only rig)",
              dlg.get_by_test_id("ro-dev-ai64-lab").is_visible()
              and dlg.get_by_test_id("ro-dev-ai64-bench").count() == 0
              and dlg.get_by_test_id("ro-dev-sk-1").count() == 0)

        # ── 3. unsigned: the UI warns first ──
        dlg.locator("select[name=artifact]").select_option("wc-0.1.2-trixie-arm64")
        expect(dlg.get_by_test_id("unsigned-warning")).to_be_visible(timeout=5000)
        check("unsigned artifact: a warning, Create disabled until confirmed",
              dlg.get_by_test_id("ro-create").is_disabled() and "NOT signed" in dlg.get_by_test_id("unsigned-warning").inner_text())
        dlg.get_by_test_id("ro-dev-ai64-proto").locator("input").check()
        shot(page, "new-rollout-unsigned-warning")
        dlg.locator("input[name=ack-unsigned]").check()
        check("confirming the warning enables Create", dlg.get_by_test_id("ro-create").is_enabled())

        # ── 4. runtime mismatch: refused by the GS (422), the reason shown ──
        dlg.locator("select[name=artifact]").select_option("wc-0.2.0-bookworm-arm64")
        check("the dialog flags the device's runtime mismatch",
              dlg.get_by_test_id("ro-mismatch-ai64-proto").is_visible()
              and dlg.get_by_test_id("ro-mismatch-ai64-lab").count() == 0)
        dlg.get_by_test_id("ro-create").click()
        expect(dlg.get_by_test_id("ro-error")).to_be_visible(timeout=5000)
        etxt = dlg.get_by_test_id("ro-error").inner_text()
        check("the GS refuses the bookworm build on the trixie rig, with the reason",
              "runtime mismatch" in etxt and "ai64-proto" in etxt and "bookworm-arm64" in etxt
              and "0.3.1-trixie-arm64" in etxt, etxt)
        check("no deployment was created by the refused rollout",
              page.locator("tbody tr[data-testid^=dep-]").count() == 0)
        shot(page, "new-rollout-runtime-mismatch-422")

        # ── 5. wc 0.1.0 to ONE device: live progress -> success ──
        dlg.locator("select[name=artifact]").select_option("wc-0.1.0-trixie-arm64")
        check("no mismatch flag for the matching build", dlg.get_by_test_id("ro-mismatch-ai64-proto").count() == 0)
        dlg.get_by_test_id("ro-create").click()
        expect(page.get_by_test_id("app-rollout")).to_be_hidden(timeout=5000)
        detail = page.get_by_test_id("rollout-detail")
        expect(detail).to_be_visible(timeout=5000)
        dev = detail.get_by_test_id("ro-device-ai64-proto")
        expect(dev).to_be_visible(timeout=8000)
        check("the rollout targets one device", detail.locator("[data-testid^=ro-device-]").count() == 1)
        seen, t_end, mid = [], time.time() + ROLL_S + 15, False
        while time.time() < t_end:
            st = dev.get_attribute("data-status")
            if st and (not seen or seen[-1] != st):
                seen.append(st)
            if st in ("downloading", "installing") and not mid:
                shot(page, f"rollout-live-{st}")
                mid = True
            if outcome_of(page) not in ("in_progress", "loading", None):
                break
            time.sleep(0.3)
        check("live progress: the device status moves (pending -> downloading/installing -> success)",
              any(s in seen for s in ("downloading", "installing")) and seen[-1] == "success", seen)
        out = outcome_of(page)
        check("outcome success", out == "success" and "success" in detail.get_by_test_id("rollout-outcome-text").inner_text(), out)
        shot(page, "rollout-success")
        dep1 = max(polls, key=lambda k: polls[k][0])
        before = len(polls[dep1])
        time.sleep(6)
        check("outcome polling stops when final", len(polls[dep1]) == before, f"{before} -> {len(polls[dep1])}")
        gaps = [b - a for a, b in zip(polls[dep1], polls[dep1][1:])]
        check("outcome poll interval ~5 s", len(gaps) >= 2 and min(gaps) >= 4.8, [round(g, 2) for g in gaps])

        # ── 6. wc 0.1.1: the commit gate fails -> rolled back to wc 0.1.0 ──
        dlg = open_new(page)
        dlg.locator("select[name=artifact]").select_option("wc-0.1.1-trixie-arm64")
        dlg.get_by_test_id("ro-dev-ai64-proto").locator("input").check()
        dlg.get_by_test_id("ro-create").click()
        expect(page.get_by_test_id("app-rollout")).to_be_hidden(timeout=5000)
        out = wait_outcome(page, ["success", "rolled_back", "failure"], ROLL_S + 15)
        detail = page.get_by_test_id("rollout-detail")
        otxt = detail.get_by_test_id("rollout-outcome-text").inner_text()
        check("outcome: rolled back to wc-0.1.0-trixie-arm64",
              out == "rolled_back" and "rolled back to wc-0.1.0-trixie-arm64" in otxt, f"{out} {otxt}")
        dev = detail.get_by_test_id("ro-device-ai64-proto")
        rtxt = dev.get_by_test_id("device-reason").inner_text() if dev.get_by_test_id("device-reason").count() else ""
        check("the device shows the commit-gate lines of its log",
              "COMMIT FAILED" in rtxt and "ROLLBACK current" in rtxt, rtxt[:200])
        shot(page, "rollout-rolled-back")

        # ── 7. the unsigned artifact, confirmed: failure + the signature error ──
        dlg = open_new(page)
        dlg.locator("select[name=artifact]").select_option("wc-0.1.2-trixie-arm64")
        dlg.get_by_test_id("ro-dev-ai64-proto").locator("input").check()
        dlg.locator("input[name=ack-unsigned]").check()
        dlg.get_by_test_id("ro-create").click()
        expect(page.get_by_test_id("app-rollout")).to_be_hidden(timeout=5000)
        out = wait_outcome(page, ["success", "rolled_back", "failure"], ROLL_S + 15)
        dev = page.get_by_test_id("rollout-detail").get_by_test_id("ro-device-ai64-proto")
        rtxt = dev.get_by_test_id("device-reason").inner_text() if dev.get_by_test_id("device-reason").count() else ""
        check("unsigned: failure with the signature error (redacted)",
              out == "failure" and "Signature verification" in rtxt and "X-Amz-Signature=***" in rtxt, f"{out} {rtxt[:160]}")
        shot(page, "rollout-unsigned-failure")

        # ── 8. the Rollouts table: three app deployments and their outcomes ──
        time.sleep(7)                              # the table polls every 6 s
        table = page.locator("tbody tr[data-testid^=dep-]")
        ttxt = "\n".join(table.all_inner_texts())
        check("the Rollouts table lists the three rollouts with their outcomes",
              table.count() == 3 and "rolled back to wc-0.1.0-trixie-arm64" in ttxt and "success" in ttxt, ttxt[:300])
        check("never two Rollout-detail requests in flight",
              max(maxinflight.values() or [0]) <= 1 and max(detail_max.values() or [0]) <= 1,
              (dict(maxinflight), dict(detail_max)))
        shot(page, "rollouts-table")

        check("no page errors", not errors, errors[:3])
        br.close()


if __name__ == "__main__":
    main()
