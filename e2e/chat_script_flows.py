"""Drive the Chat + Script views end to end."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
URL = "http://localhost:4200"
USER_SCRIPT = "start @stage1, @stage2(agt 1,2,3,4),@stage3(combine), @stage4(abc,abc) @stage5(human)"
errors: list[str] = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("shot", name)


def on_console(m):
    if m.type == "error":
        errors.append(m.text)


def names(page):
    return page.locator(".node .node-name").all_inner_texts()


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.evaluate("localStorage.clear()")
    page.goto(URL)
    page.wait_for_selector(".node", timeout=20000)

    # ── chat: english ──
    page.locator(".view-tab", has_text="Chat").click()
    page.wait_for_selector(".welcome")
    shot(page, "20-chat-welcome")
    page.locator(".example", has_text="Describe it").click()
    page.locator(".send").click()
    page.wait_for_selector(".proposal", timeout=15000)
    page.wait_for_timeout(400)
    shot(page, "21-chat-english")
    page.locator(".proposal .btn-primary").last.click()
    page.wait_for_selector(".applied-tag")
    page.locator(".view-tab", has_text="Graph").click()
    page.wait_for_selector(".node")
    page.wait_for_timeout(800)
    print("english graph:", names(page))
    shot(page, "22-graph-from-english")

    # ── chat: user's script ──
    page.locator(".view-tab", has_text="Chat").click()
    page.locator(".composer textarea").fill(USER_SCRIPT)
    page.locator(".send").click()
    page.wait_for_function("document.querySelectorAll('.proposal').length >= 2", timeout=15000)
    page.wait_for_timeout(400)
    shot(page, "23-chat-script")
    page.locator(".proposal .btn-primary").last.click()
    page.wait_for_function("document.querySelectorAll('.applied-tag').length >= 2")

    # ── chat: edit ──
    page.locator(".composer textarea").fill("add 2 more reviewers to stage 2")
    page.locator(".send").click()
    page.wait_for_function("document.querySelectorAll('.proposal').length >= 3", timeout=15000)
    page.wait_for_timeout(400)
    shot(page, "24-chat-edit")

    # ── graph: run the scripted graph ──
    page.locator(".view-tab", has_text="Graph").click()
    page.wait_for_selector(".node")
    page.wait_for_timeout(800)
    print("script graph:", names(page))
    shot(page, "25-graph-from-script")
    page.locator("button.run").click()
    page.wait_for_selector(".gate", timeout=60000)
    page.locator(".gate .btn-ok").click()
    page.wait_for_selector(".run-pill[data-status='completed']", timeout=30000)
    page.wait_for_timeout(500)
    shot(page, "26-script-graph-run")

    # ── script view: edit + apply ──
    page.locator(".view-tab", has_text="Script").click()
    page.wait_for_selector(".editor textarea")
    page.wait_for_timeout(700)
    shot(page, "27-script-synced")
    ta = page.locator(".editor textarea")
    text = ta.input_value()
    ta.fill(text + "\n@qa(qa_bot[pro] \"Check the final answer\" <- (stage2, combine))")
    page.wait_for_timeout(900)
    shot(page, "28-script-edited")
    page.locator(".bar .btn-primary").click()
    page.wait_for_timeout(700)
    page.locator(".view-tab", has_text="Graph").click()
    page.wait_for_selector(".node")
    page.wait_for_timeout(800)
    print("after script apply has qa_bot:", "qa_bot" in names(page))
    shot(page, "29-graph-after-script")

    # ── script error ──
    page.locator(".view-tab", has_text="Script").click()
    page.wait_for_selector(".editor textarea")
    page.locator(".editor textarea").fill("@a(b, c\n@d(e <- nowhere)")
    page.wait_for_selector(".error-bar", timeout=5000)
    print("error:", page.locator(".error-bar").inner_text())
    shot(page, "30-script-error")
    browser.close()

print("console errors:", len(errors))
for e in errors[:10]:
    print("  ", e[:300])
