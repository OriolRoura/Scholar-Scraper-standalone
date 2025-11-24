"""Minimal on-demand Selenium helper used to manually solve captchas.

This helper launches a headful browser only when called, waits for a manual
solve (either indefinitely or up to a timeout), extracts cookies and localStorage
and returns them so the caller can inject them into HTTP sessions.

It intentionally keeps behavior conservative and dependency-optional: callers
should import it dynamically and handle ImportError if Selenium is not installed.
"""
from __future__ import annotations

import time
import json
from typing import Tuple, Dict, List, Optional

def manual_solve(url: str,
                 browser: str = 'chrome',
                 user_data_dir: Optional[str] = None,
                 wait_indefinite: bool = True,
                 timeout: int = 300,
                 persist_cookies_path: Optional[str] = None) -> Tuple[List[dict], Dict[str, str]]:
    """Open a browser to `url`, let the user solve the captcha, and return cookies + localStorage.

    Parameters
    - url: The page to open (captcha or profile URL).
    - browser: 'chrome' or 'firefox' (chrome by default).
    - user_data_dir: optional path to a persistent profile.
    - wait_indefinite: if True, wait until user closes or page appears solved. If False, wait up to `timeout` seconds.
    - timeout: maximum seconds to wait when wait_indefinite is False.
    - persist_cookies_path: optional path where cookies/localStorage are saved as JSON.

    Returns: (cookies_list, localstorage_dict)
    """
    try:
        # Import here so the module can remain import-safe when Selenium isn't installed.
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.webdriver.firefox.service import Service as GeckoService
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.firefox.options import Options as FirefoxOptions
        # webdriver-manager is optional but convenient
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.firefox import GeckoDriverManager
        except Exception:
            ChromeDriverManager = None
            GeckoDriverManager = None
    except Exception as e:
        raise ImportError('Selenium and webdriver-manager must be installed to use manual_solve') from e

    driver = None
    opts = None
    try:
        if browser.lower() == 'firefox':
            opts = FirefoxOptions()
            # keep headful
            if user_data_dir:
                opts.set_preference('profile', user_data_dir)
            # install or use geckodriver
            if GeckoDriverManager is not None:
                service = GeckoService(GeckoDriverManager().install())
            else:
                service = GeckoService()
            driver = webdriver.Firefox(service=service, options=opts)
        else:
            opts = ChromeOptions()
            if user_data_dir:
                opts.add_argument(f"--user-data-dir={user_data_dir}")
            # avoid sandbox issues
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            # install or use chromedriver
            if ChromeDriverManager is not None:
                service = ChromeService(ChromeDriverManager().install())
            else:
                service = ChromeService()
            driver = webdriver.Chrome(service=service, options=opts)

        driver.get(url)

        start = time.time()
        solved = False
        while True:
            src = ''
            try:
                src = driver.page_source.lower()
            except Exception:
                src = ''

            # Heuristics: if page contains regular profile rows or our usual content,
            # assume captcha was cleared. Also break if the URL changed away from captcha.
            if 'gsc_a_tr' in src or 'citations?user=' in driver.current_url.lower():
                solved = True
            if 'captcha' not in src and 'verify' not in src:
                solved = True

            if solved:
                break

            if not wait_indefinite and (time.time() - start) > timeout:
                raise TimeoutError(f'Manual solve timed out after {timeout}s')

            time.sleep(2)

        # Extract cookies and localStorage
        cookies = driver.get_cookies()
        try:
            local = driver.execute_script("var s={}; for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i); s[k]=localStorage.getItem(k);} return s;")
        except Exception:
            local = {}

        payload = {'cookies': cookies, 'localStorage': local}
        if persist_cookies_path:
            try:
                with open(persist_cookies_path, 'w', encoding='utf-8') as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return cookies, local
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
