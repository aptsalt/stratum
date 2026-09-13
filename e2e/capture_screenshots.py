"""Capture the README screenshots into docs/screenshots/. Needs the API on :8000 and the UI on :4200."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:4200"
ENGLISH = "Research a topic with 4 analysts, combine their findings, draft and edit in a loop, then I approve"


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def fresh(page):
    page.goto(URL)
    page.evaluate("localStorage.clear(); localStorage.setItem('stratum.coach', '1')")
    page.goto(URL)
    page.wait_for_selector(".node", timeout=20000)
    page.wait_for_timeout(1000)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
    fresh(page)

    # Graph view: live run → human gate → completed timeline
    page.locator(".node", has_text="synthesizer").first.click()
    page.locator("button.run").click()
    page.wait_for_timeout(2300)
    shot(page, "01-live-run")
    page.wait_for_selector(".gate", timeout=60000)
    page.wait_for_timeout(400)
    shot(page, "02-human-gate")
    page.locator(".gate .btn-ok").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.wait_for_timeout(700)
    shot(page, "03-run-complete")

    # Router: one branch taken, the others marked "not taken"
    page.locator(".tpl", has_text="Support Triage").click()
    page.wait_for_timeout(800)
    page.locator("button.run").click()
    page.wait_for_selector(".gate", timeout=60000)
    page.locator(".gate .btn-ok").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.locator(".scroller").evaluate("el => el.scrollTo(0, 0)")
    page.wait_for_timeout(700)
    shot(page, "04-router-branch")

    # Chat: English → proposal with preview + diff
    page.locator(".view-tab", has_text="Chat").click()
    page.wait_for_selector(".welcome")
    page.wait_for_timeout(400)
    shot(page, "05-chat-welcome")
    page.locator(".composer textarea").fill(ENGLISH)
    page.locator(".send").click()
    page.wait_for_selector(".proposal", timeout=15000)
    page.wait_for_timeout(500)
    shot(page, "06-chat-proposal")
    page.locator(".proposal .btn-primary").last.click()
    page.wait_for_selector(".applied-tag")

    # Script: live edit with preview + diff
    page.locator(".view-tab", has_text="Script").click()
    page.wait_for_selector(".editor textarea")
    page.wait_for_timeout(800)
    ta = page.locator(".editor textarea")
    ta.fill(ta.input_value() + '\n@qa(fact_checker[pro, search] "Verify every claim" <- (analysts, combine))')
    page.wait_for_timeout(1200)
    shot(page, "07-script-editor")
    page.locator(".bar .btn-primary").click()
    page.wait_for_timeout(600)

    # Back to graph: exported Python + compiled ADK graph
    page.locator(".view-tab", has_text="Graph").click()
    page.wait_for_selector(".node")
    page.locator(".tab", has_text="Python export").click()
    page.locator(".code-bar button", has_text="Dynamic workflow").click()
    page.wait_for_timeout(1000)
    shot(page, "08-python-export")
    page.locator(".tab", has_text="Compiled graph").click()
    page.wait_for_timeout(500)
    shot(page, "09-compiled-graph")

    # Light theme + guide
    page.locator(".tpl", has_text="Research Brief").click()
    page.locator(".tab", has_text="Run").click()
    page.locator(".theme").click()
    page.wait_for_timeout(900)
    shot(page, "10-light-theme")
    page.locator(".theme").click()
    page.locator(".nav a", has_text="How it works").click()  # in-app nav also works on a static server
    page.wait_for_timeout(1200)
    shot(page, "11-learn")
    browser.close()
