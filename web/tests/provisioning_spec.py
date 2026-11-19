"""Regression test of the operator's provisioning path in the GS web UI
(spec ui-provisioning, part W) — headless Chromium against the MOCK gs-api.

    cd web && npm run build
    <venv with playwright>/bin/python tests/provisioning_spec.py

It starts `node mock/server.mjs --dist dist` (the production bundle + the
contract's /api shapes), then walks: the one Fleet list (a colony-only SK row,
the com badges) -> Add device with a wrong ssh user (422 auth shows the GS key)
-> an unreachable address -> Add device that provisions (the Run drawer
progresses, polls serially every 2 s) -> page reload resumes the drawer -> a
second Add of the same name gives "already running: open" (409) -> success with
the result -> a failing run shows the failed task -> an opkg rig shows its
skipped-by-design items -> Deploy runtime from a row. Exit code 0 = all passed.

env: CHROME (chromium binary), SHOTS_DIR (screenshots), MOCK_PORT, MOCK_RUN_SECONDS.
"""
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

WEB = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("MOCK_PORT", "18099"))
BASE = f"http://127.0.0.1:{PORT}/"
RUN_S = float(os.environ.get("MOCK_RUN_SECONDS", "20"))
SHOTS = Path(os.environ.get("SHOTS_DIR", "/bigdata/work/architect/ui-test/shots-w"))
CHROME = os.environ.get("CHROME", str(Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome"))
PUBKEY_PREFIX = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMock"

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


def wait_status(page, statuses, timeout_s):
    try:
        _wait_status(page, statuses, timeout_s)
    except Exception as e:  # noqa: BLE001
        shot(page, "TIMEOUT-waiting-for-" + "-".join(statuses))
        print("timeout:", e, flush=True)
    return page.get_attribute("[data-testid=run-drawer]", "data-status")


def _wait_status(page, statuses, timeout_s):
    page.wait_for_function(
        "(sts) => { const d = document.querySelector('[data-testid=run-drawer]');"
        " return d && sts.includes(d.dataset.status) }", arg=statuses, timeout=timeout_s * 1000)
    return page.get_attribute("[data-testid=run-drawer]", "data-status")


def open_fleet(page):
    page.locator("aside nav div.nav-item", has_text="Fleet").first.click()
    expect(page.get_by_test_id("row-ai64-proto")).to_be_visible(timeout=10000)


def add_device(page, name, address, user="debian", role=None):
    page.get_by_test_id("add-device-btn").click()
    m = page.get_by_test_id("add-device")
    m.locator("input[name=name]").fill(name)
    m.locator("input[name=address]").fill(address)
    m.locator("input[name=user]").fill(user)
    if role:
        m.locator("select[name=role]").select_option(role)
    m.get_by_role("button", name="Add device").click()
    return m


def main():
    if not (WEB / "dist" / "index.html").exists():
        sys.exit("build first: npm run build")
    SHOTS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MOCK_RUN_SECONDS=str(RUN_S))
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
        run(env)
    finally:
        mock.terminate()
        mock.wait(5)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


def run(env):
    with sync_playwright() as p:
        kw = {"executable_path": CHROME} if Path(CHROME).exists() else {}
        br = p.chromium.launch(headless=True, **kw)
        ctx = br.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # run-poll bookkeeping: per run id, request times + max concurrent in flight
        polls = defaultdict(list)
        inflight = defaultdict(int)
        maxinflight = defaultdict(int)

        def rid(url):
            path = url.split("?")[0]
            return path.rsplit("/", 1)[1] if "/api/runs/" in path else None

        def on_req(r):
            i = rid(r.url)
            if i:
                polls[i].append(time.time())
                inflight[i] += 1
                maxinflight[i] = max(maxinflight[i], inflight[i])

        def on_done(r):
            i = rid(r.url)
            if i:
                inflight[i] -= 1
        page.on("request", on_req)
        page.on("requestfinished", on_done)
        page.on("requestfailed", on_done)

        page.goto(BASE)
        open_fleet(page)
        time.sleep(0.5)

        # ── 1. ONE list: the colony-only SK row + the com badges ──
        sk = page.get_by_test_id("row-sk-1")
        check("SK-like colony-only row visible", sk.is_visible() and sk.get_by_test_id("src-colony").is_visible())
        check("SK row has no Mender auth status", sk.locator("td").nth(8).inner_text().strip() == "—")
        for st in ("mtls", "plaintext", "down", "unknown"):
            check(f"com badge {st} shown", page.get_by_test_id(f"com-{st}").count() >= 1)
        hidden = re.compile(r"Preauthorize|Connect Device|Connect a new device|reload|Probe|Add to VPN|Set identity", re.I)
        check("no Probe / Set-identity / Add-to-VPN / Set-IP / Preauthorize buttons in Fleet",
              page.get_by_role("button", name=hidden).count() == 0 and page.locator("button[title='set local IP']").count() == 0)
        shot(page, "fleet-one-list")

        # ── 2. preflight auth -> the GS key ──
        m = add_device(page, "ai64-new", "10.0.0.5", user="wrong")
        expect(m.get_by_test_id("preflight-auth")).to_be_visible(timeout=10000)
        key = m.get_by_test_id("gs-pubkey").input_value(timeout=5000)
        check("auth error shows the GS public key", key.startswith(PUBKEY_PREFIX), key[:60])
        check("auth error names ~wrong/.ssh/authorized_keys",
              "~wrong/.ssh/authorized_keys" in m.get_by_test_id("preflight-auth").inner_text())
        shot(page, "add-device-auth-key")

        # ── 3. unreachable in plain words ──
        m.locator("input[name=address]").fill("10.0.0.250")
        m.locator("input[name=user]").fill("debian")
        m.get_by_role("button", name="Add device").click()
        expect(m.get_by_test_id("preflight-unreachable")).to_be_visible(timeout=10000)
        check("unreachable shown in plain words", "cannot be reached" in m.get_by_test_id("preflight-unreachable").inner_text())
        shot(page, "add-device-unreachable")

        # ── 4. a provision that succeeds: the drawer progresses ──
        m.locator("input[name=address]").fill("10.0.0.5")
        m.get_by_role("button", name="Add device").click()
        drawer = page.get_by_test_id("run-drawer")
        expect(drawer).to_be_visible(timeout=10000)
        check("Add device opens the Run drawer", True)
        shot(page, "drawer-provision-started")
        t_end = time.time() + RUN_S * 0.6
        while time.time() < t_end:              # >= 2 finished tasks + the running row
            st = drawer.get_attribute("data-status")
            ntasks = drawer.get_by_test_id("run-tasks").locator("li").count() if st != "loading" else 0
            if ntasks >= 3:
                break
            time.sleep(0.3)
        check("drawer progresses (running, tasks listed)", st == "running" and ntasks >= 2, f"{st} {ntasks}")
        shot(page, "drawer-provision-running")

        # ── 5. reload mid-run: the drawer resumes ──
        page.reload()
        expect(page.get_by_test_id("run-drawer")).to_be_visible(timeout=10000)
        drawer = page.get_by_test_id("run-drawer")
        expect(drawer.locator("h3")).to_contain_text("ai64-new", timeout=10000)
        check("reload resumes the drawer of the running run", "ai64-new" in drawer.locator("h3").inner_text())
        shot(page, "drawer-resumed-after-reload")

        # ── 6. second click -> 409 "already running: open" ──
        drawer.get_by_role("button", name="Close").click()
        open_fleet(page)
        row = page.get_by_test_id("row-ai64-new")
        expect(row.get_by_test_id("deploy-ai64-new")).to_be_disabled(timeout=10000)
        check("Deploy runtime disabled while a run is active", row.get_by_test_id("deploy-ai64-new").is_disabled())
        m = add_device(page, "ai64-new", "10.0.0.5")
        expect(m.get_by_test_id("already-running")).to_be_visible(timeout=10000)
        check("409 shows 'already running: open'", "already running" in m.get_by_test_id("already-running").inner_text())
        shot(page, "add-device-409-already-running")
        t_open = time.time()
        m.get_by_test_id("already-running").get_by_role("button", name="open").click()
        expect(page.get_by_test_id("run-drawer").locator("h3")).to_contain_text("ai64-new", timeout=5000)
        check("409 'open' opens that run", "ai64-new" in page.get_by_test_id("run-drawer").locator("h3").inner_text())

        # ── 7. success: the result ──
        st = wait_status(page, ["success", "failure"], RUN_S + 15)
        res = page.get_by_test_id("run-result")
        check("provision ends in success", st == "success", st)
        txt = res.inner_text() if res.count() else ""
        check("result shows vpn ip, certificate until, accepted",
              "VPN IP" in txt and "certificate until" in txt and "accepted in Mender" in txt, txt[:200])
        shot(page, "drawer-provision-success")
        run_id = page.get_by_test_id("run-id").inner_text().replace("run ", "").strip()
        before = len(polls[run_id])
        time.sleep(5)
        check("polling stops when final", len(polls[run_id]) == before, f"{before} -> {len(polls[run_id])}")
        ts = [t for t in polls[run_id] if t >= t_open]     # one uninterrupted drawer
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        check("never two run requests in flight", max(maxinflight.values() or [0]) <= 1, dict(maxinflight))
        check("run poll interval ~2 s", len(gaps) >= 3 and min(gaps) >= 1.8, [round(g, 2) for g in gaps][:12])
        page.get_by_test_id("run-drawer").get_by_role("button", name="Close").click()

        # ── 8. a failing run: the failed task + message ──
        add_device(page, "ai64-fail", "10.0.0.7")
        expect(page.get_by_test_id("run-drawer")).to_be_visible(timeout=10000)
        st = wait_status(page, ["success", "failure"], RUN_S + 15)
        err = page.get_by_test_id("run-error")
        etxt = err.inner_text() if err.count() else ""
        check("failure shows the failing task and its message",
              st == "failure" and "network : join the tailnet" in etxt and "timed out" in etxt, f"{st} {etxt[:120]}")
        shot(page, "drawer-provision-failure")
        page.get_by_test_id("run-drawer").get_by_role("button", name="Close").click()

        # ── 9. an opkg (SK-like) rig: skipped by design, no Mender ──
        add_device(page, "sk-2", "10.0.0.32", role="zonal")
        expect(page.get_by_test_id("run-drawer")).to_be_visible(timeout=10000)
        st = wait_status(page, ["success", "failure"], RUN_S + 15)
        rtxt = page.get_by_test_id("run-result").inner_text() if st == "success" else ""
        check("opkg rig: skipped by design + no Mender", "skipped by design" in rtxt and "no Mender" in rtxt, rtxt[:200])
        shot(page, "drawer-opkg-skipped-by-design")
        page.get_by_test_id("run-drawer").get_by_role("button", name="Close").click()
        open_fleet(page)
        expect(page.get_by_test_id("row-sk-2").get_by_test_id("src-colony")).to_be_visible(timeout=15000)
        check("the new opkg rig is a colony-only row", True)

        # ── 10. Deploy runtime from a row -> the same drawer ──
        page.get_by_test_id("deploy-ai64-proto").click()
        dlg = page.get_by_test_id("deploy-runtime")
        expect(dlg.locator("select[name=runtime_version]")).to_be_visible(timeout=10000)
        opts = dlg.locator("select[name=runtime_version] option").all_inner_texts()
        check("runtime versions from the runtime plane (one per version)", len(opts) == 2 and opts[0].startswith("0.3.0"), opts)
        shot(page, "deploy-runtime-dialog")
        dlg.get_by_role("button", name="Deploy →").click()
        expect(page.get_by_test_id("run-drawer")).to_be_visible(timeout=10000)
        check("Deploy runtime opens the drawer", "Deploy runtime ai64-proto" in page.get_by_test_id("run-drawer").locator("h3").inner_text())
        st = wait_status(page, ["success", "failure"], RUN_S + 15)
        check("deploy runtime ends in success", st == "success", st)
        shot(page, "drawer-deploy-success")
        page.get_by_test_id("run-drawer").get_by_role("button", name="Close").click()
        time.sleep(1.5)
        shot(page, "fleet-after-runs")

        check("no page errors", not errors, errors[:3])
        br.close()


if __name__ == "__main__":
    main()
