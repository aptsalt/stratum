"""Drive the Stratum UI end to end and screenshot each state."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
URL = "http://localhost:4200"
errors: list[str] = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("shot", name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.evaluate("localStorage.clear()")
    page.goto(URL)
    page.wait_for_selector(".node", timeout=20000)
    page.wait_for_timeout(1200)
    print("nodes:", page.locator(".node").count(), "edges:", page.locator("path.edge").count(),
          "joins:", page.locator(".join-badge").count(), "health:", page.locator(".health").inner_text())
    shot(page, "01-studio")

    # select a node → inspector
    page.locator(".node", has_text="writer").first.click()
    page.wait_for_timeout(400)
    shot(page, "02-inspector-writer")

    # run research brief → gate
    page.locator("button.run").click()
    page.wait_for_timeout(1800)
    shot(page, "03-running")
    page.wait_for_selector(".gate", timeout=60000)
    page.wait_for_timeout(300)
    shot(page, "04-gate")
    page.locator(".gate .btn-ok").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.wait_for_timeout(600)
    shot(page, "05-completed")
    print("research: done nodes", page.locator(".node[data-status='done']").count())

    # support triage (router) → reject
    page.locator(".tpl", has_text="Support Triage").click()
    page.wait_for_timeout(800)
    page.locator("button.run").click()
    page.wait_for_selector(".gate", timeout=60000)
    page.wait_for_timeout(300)
    shot(page, "06-triage-gate")
    page.locator(".gate .btn-bad").click()
    page.locator(".gate textarea").fill("tone is off")
    page.locator(".gate .btn-bad").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.wait_for_timeout(500)
    print("triage: skipped", page.locator(".node[data-status='skipped']").count())
    shot(page, "07-triage-rejected")

    # code export + IR + dynamic
    page.locator(".tab", has_text="Python export").click()
    page.wait_for_timeout(600)
    shot(page, "08-code-graph")
    page.locator(".code-bar button", has_text="Dynamic workflow").click()
    page.wait_for_timeout(900)
    shot(page, "09-code-dynamic")
    page.locator(".tab", has_text="Compiled graph").click()
    page.wait_for_timeout(400)
    shot(page, "10-ir")

    # dynamic mode run of code review swarm
    page.locator(".tpl", has_text="Code Review Swarm").click()
    page.wait_for_timeout(800)
    page.locator("button.run").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=60000)
    page.wait_for_timeout(500)
    shot(page, "11-swarm-dynamic")

    # command palette
    page.keyboard.press("Control+k")
    page.wait_for_timeout(300)
    page.keyboard.type("router")
    page.wait_for_timeout(200)
    shot(page, "12-palette")
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    shot(page, "13-added-router")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(400)

    # light theme + learn
    page.locator(".theme").click()
    page.wait_for_timeout(500)
    shot(page, "14-light")
    page.locator(".theme").click()
    page.goto(URL + "/learn")
    page.wait_for_timeout(1200)
    shot(page, "15-learn")
    page.set_viewport_size({"width": 420, "height": 900})
    page.wait_for_timeout(500)
    shot(page, "16-learn-mobile")
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    print("learn mobile horizontal overflow:", overflow)
    browser.close()

print("console errors:", len(errors))
for e in errors[:15]:
    print("  ", e[:300])
sys.exit(0)
