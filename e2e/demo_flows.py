"""Check the static GitHub Pages demo build (Pyodide in the browser, no backend).

Usage: python e2e/demo_flows.py [base_url]   (default http://localhost:4400/stratum-demo/)
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4400/stratum-demo/"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
USER_SCRIPT = "start @stage1, @stage2(agt 1,2,3,4),@stage3(combine), @stage4(abc,abc) @stage5(human)"
errors: list[str] = []


def on_console(m):
    if m.type == "error":
        errors.append(m.text)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.evaluate("localStorage.clear(); localStorage.setItem('stratum.coach', '1')")
    page.goto(URL)
    page.wait_for_selector(".boot", timeout=10000)
    page.wait_for_selector(".boot", state="detached", timeout=120000)
    page.wait_for_selector(".node", timeout=30000)
    page.wait_for_timeout(800)
    print("status:", page.locator(".status-text").inner_text())
    print("nodes:", page.locator(".node").count(), "| health:", page.locator(".health").inner_text())

    page.locator("button.run").click()
    page.wait_for_selector(".gate", timeout=60000)
    page.locator(".gate .btn-ok").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.wait_for_timeout(500)
    print("run done nodes:", page.locator(".node[data-status='done']").count())
    page.screenshot(path=str(OUT / "demo-run.png"))

    page.locator(".tab", has_text="Python export").click()
    page.wait_for_timeout(600)
    print("export has Workflow:", "Workflow" in page.locator("pre.code").inner_text())

    page.locator(".view-tab", has_text="Chat").click()
    page.locator(".composer textarea").fill(USER_SCRIPT)
    page.locator(".send").click()
    page.wait_for_selector(".proposal", timeout=20000)
    page.locator(".proposal .btn-primary").last.click()
    page.locator(".view-tab", has_text="Graph").click()
    page.wait_for_selector(".node")
    page.wait_for_timeout(600)
    print("script graph:", page.locator(".node .node-name").all_inner_texts())

    page.locator(".view-tab", has_text="Chat").click()
    page.locator(".composer textarea").fill("add 2 more reviewers to stage 2")
    page.locator(".send").click()
    page.wait_for_function("document.querySelectorAll('.proposal').length >= 2", timeout=20000)
    page.screenshot(path=str(OUT / "demo-chat.png"))

    page.locator(".view-tab", has_text="Script").click()
    page.wait_for_selector(".editor textarea")
    page.locator(".editor textarea").fill("@a(b, c\n@d(e)")
    page.wait_for_selector(".error-bar", timeout=10000)
    print("script error:", page.locator(".error-bar").inner_text())
    browser.close()

print("console errors:", len(errors))
for e in errors[:10]:
    print("  ", e[:300])
