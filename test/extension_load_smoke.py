import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


def main():
    with tempfile.TemporaryDirectory(prefix="ai-quiz-extension-") as profile:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile,
                headless=False,
                args=[
                    "--headless=new",
                    f"--disable-extensions-except={EXTENSION}",
                    f"--load-extension={EXTENSION}"
                ]
            )

            for _ in range(30):
                if any(
                    worker.url.endswith("/background.js")
                    for worker in context.service_workers
                ):
                    break
                time.sleep(0.2)

            worker_urls = [worker.url for worker in context.service_workers]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("chrome://extensions", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            item_count = page.evaluate(
                """
                () => {
                  const manager = document.querySelector("extensions-manager");
                  const list = manager?.shadowRoot?.querySelector("extensions-item-list");
                  return list?.shadowRoot?.querySelectorAll("extensions-item").length || 0;
                }
                """
            )
            assert item_count == 1, f"Expected 1 extension, found {item_count}"
            print("Extension load smoke test passed")
            print(worker_urls[0] if worker_urls else "Chrome accepted the unpacked extension")
            context.close()


if __name__ == "__main__":
    main()
