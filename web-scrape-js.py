import asyncio
from playwright.async_api import async_playwright
import threading
import time
from queue import Empty, Queue
from urllib.parse import quote, urlparse
import urllib.request

mutex = threading.Lock()
failed_queue = Queue()

IGNORED_FAILURE_PATTERNS = (
    "googleads.g.doubleclick.net",
    "pagead2.googlesyndication.com",
    "https://www.google.com/",
    "https://ad.doubleclick.net/",
    "www.google-analytics.com",
    "www.googletagservices.com/",
    "www.googletagmanager.com/",
    "amazon-adsystem.com",
    "cdn.krxd.net",
    "cdn.jwplayer.com",
    "revenuecpmgate.com",
    "hsforms.net",
    "//dashthis.com/",
    "jsc.mgid.com",
    "www.googleadservices.com",
    "s3.amazonaws.com",
    "cdn.brandmetrics.com",
    "cdn.amplitude.com",
    "kettledroopingcontinuation.com",
    "cmp.quantcast.com",
    "certify-js.alexametrics.com",
    "siteintercept.qualtrics.com",
    "s7.addthis.com",
    "analytics.google.com",
)


def save_failed(failed_url, src_url, reason):
    failed_url_lower = failed_url.lower()
    reason_text = str(reason)

    if any(pattern in failed_url_lower for pattern in IGNORED_FAILURE_PATTERNS):
        return

    if ".js" in failed_url_lower and "ERR_NAME_NOT_RESOLVED" in reason_text:
        failed_queue.put((failed_url, src_url, reason_text))


def process_failed_request(failed_url, src_url, reason):
    with mutex:
        print("FAILED2: " + failed_url)
        parsed = urlparse(failed_url)
        domain = parsed.netloc.lower()

        if ":" in domain:
            domain = domain.split(":", 1)[0]

        with open("failed_urls.txt", "a", encoding="utf-8") as myfile:
            myfile.write(f"{domain},{reason},{src_url},{failed_url}\n")

    callback_url = "https://YOU_DOMAIN_HERE.com/add_to_js.php?domain=" + quote(domain)
    with urllib.request.urlopen(callback_url, timeout=10) as resp:
        resp.read()


def failed_request_worker():
    while True:
        item = failed_queue.get()
        if item is None:
            failed_queue.task_done()
            break

        try:
            process_failed_request(*item)
        except Exception as exc:
            with mutex:
                print(f"Failed to record JS error for {item[0]}: {exc}")
        finally:
            failed_queue.task_done()

async def render_page(browser, url: str, wait_for: str = "networkidle"):
    """
    Render a JavaScript-heavy page and return its fully rendered HTML.

    Args:
        url: The URL to render.
        wait_for: Wait strategy — 'networkidle', 'domcontentloaded', or 'load'.
    """

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()

    page.on("requestfailed", lambda req: save_failed(req.url, url, req.failure))

    print(f"Navigating to: {url}")
    try:
        await page.goto(url, wait_until=wait_for, timeout=40_000)
        await page.wait_for_load_state("networkidle")

        html = await page.content()
        title = await page.title()
        text = await page.evaluate("() => document.body.innerText")
        links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => ({ text: a.innerText.trim(), href: a.href }))"
        )
    except Exception as e:
        print(f"Attempted {url} failed: {e}")
        return None
    finally:
        await context.close()

    print(f"Page title   : {title}")
    print(f"HTML length  : {len(html):,} characters")
    print("\n--- First 500 chars of page text ---")
    print(text[:500])
    print(f"\nFound {len(links)} links on the page. {type(links)}")

    return html


async def main_thread(domains):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            while True:
                try:
                    domain = domains.get_nowait()
                except Empty:
                    break

                url = domain if domain.startswith(("http://", "https://")) else "http://" + domain
                await render_page(browser, url)
                domains.task_done()
        finally:
            await browser.close()

def thread_worker(domains):
    asyncio.run(main_thread(domains))


if __name__ == "__main__":
    print("Starting..")
    with open("toscrapedomains.csv", "r", encoding="utf-8") as infile:
        domains = Queue()
        for line in infile:
            domain = line.strip()
            if domain:
                domains.put(domain)

    failed_worker = threading.Thread(target=failed_request_worker, daemon=True)
    failed_worker.start()

    threads = [threading.Thread(target=thread_worker, args=(domains,)) for _ in range(10)]
    for t in threads:
        t.start()
        time.sleep(1)
    for t in threads:
        t.join()

    failed_queue.join()
    failed_queue.put(None)
    failed_worker.join()

    print("Done")
