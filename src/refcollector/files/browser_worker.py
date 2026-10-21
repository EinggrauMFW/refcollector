"""Isolated SeleniumBase entry point. No core package imports or saved cookies.

Run only through BrowserPdfFallback, which supplies the guarded proxy and bounds
the process lifetime. SeleniumBase is installed in a separate trusted environment.
API: https://seleniumbase.io/help_docs/cdp_mode_methods/
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.checkboxes = []
        self.has_pdf = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "checkbox":
            identifier = values.get("id", "")
            if re.fullmatch(r"altcha_checkbox_\d+", identifier):
                self.checkboxes.append(identifier)
        if values.get("id") == "pdf" or (
            tag == "a" and "/storage/" in values.get("href", "") and ".pdf" in values.get("href", "")
        ):
            self.has_pdf = True


def resolve_page(browser, *, sleep=time.sleep):
    """Inspect before a single targeted click, then return the resulting page."""
    clicked = False
    for _ in range(30):
        html = browser.get_page_source()
        if len(html.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Browser HTML exceeds 1 MiB")
        controls = Controls()
        controls.feed(html)
        title = browser.get_title()
        if controls.has_pdf:
            return {
                "url": browser.get_current_url(),
                "html": html,
                "clicked": clicked,
                "title": title,
                "user_agent": browser.get_user_agent(),
            }
        if (
            not clicked
            and title == "Sci-Hub: are you are robot?"
            and len(controls.checkboxes) == 1
            and "Are you a robot?" in html
        ):
            browser.click("#" + controls.checkboxes[0])
            clicked = True
            sleep(12)
        else:
            sleep(1)
    return {"url": browser.get_current_url(), "html": html, "clicked": clicked, "title": title}


def main():
    request_path = Path(sys.argv[1])
    (request_path.parent / "ready").write_text(
        json.dumps({"pid": os.getpid(), "runtime": getattr(sys, "_base_executable", sys.executable)}),
        encoding="ascii",
    )
    # The parent must attach the worker to its process tree before Chrome starts.
    for _ in range(100):
        if (request_path.parent / "start").is_file():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Browser process ownership handshake timed out")
    from seleniumbase import sb_cdp

    request = json.loads(request_path.read_text(encoding="utf-8"))
    browser = sb_cdp.Chrome(
        request["url"],
        headless=False,
        browser_executable_path=request["binary"],
        user_data_dir=str(request_path.parent / "profile"),
        proxy=request["proxy"],
        browser_args=[
            "--proxy-bypass-list=<-loopback>",
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ],
    )
    try:
        result = resolve_page(browser)
        (request_path.parent / "result.json").write_text(json.dumps(result), encoding="utf-8")
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
