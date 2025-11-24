import json
import random
import time
import logging
import os
import tempfile
import datetime
from typing import List

from scholarly import scholarly, ProxyGenerator
import requests
# Try to import the dedicated captcha exception if available
try:
    from scholarly._proxy_generator import CaptchaDetectedException
except Exception:
    CaptchaDetectedException = None

from .CustomScholarlyTypes import SimplifiedAuthor
from .utilities import JSONEncoder

MAX_RETRIES = 1

# Rate limiting: random delay between requests (in seconds)
MIN_DELAY = 3
MAX_DELAY = 8

# Jitter: Probability of adding extra long pause (mimics human behavior)
JITTER_PROBABILITY = 0.2  # 20% chance
JITTER_MIN = 20  # Extra seconds
JITTER_MAX = 40

# Maximum time to spend on one author before giving up (seconds)
MAX_TIME_PER_AUTHOR = 30

# Keywords that indicate CAPTCHA or blocking
CAPTCHA_KEYWORDS = ['captcha', 'unusual traffic', 'not a robot', 'verify you', 'blocked']

# Realistic browser user agents to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
]


def set_new_proxy():
    """
    Set a new proxy for the scholarly library.
    :return: The new proxy.
    """

    pg = ProxyGenerator()

    for i in range(MAX_RETRIES):
        try:
            if pg.FreeProxies() and scholarly.use_proxy(pg):
                break
        except:
            pass
    return pg


def check_for_captcha(error_msg: str):
    """
    Check if error message indicates CAPTCHA or blocking.
    :param error_msg: Error message to check.
    :return: True if CAPTCHA detected, False otherwise.
    """
    error_lower = str(error_msg).lower()
    return any(keyword in error_lower for keyword in CAPTCHA_KEYWORDS)


def getAuthorData(scholarId: str, skip_ids: set = None):
    """
    Retrieve the author's data from Google Scholar.
    :param scholarId: The id of the author on Google Scholar.
    :return: The author's data.
    """
    # Navigator now handles request throttling. Do not sleep here to avoid double-waiting.
    
    # Set realistic browser headers
    user_agent = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://scholar.google.com/',
    }
    try:
        updated = False

        # 1) Try the common `_SESSION` attribute (older or patched setups)
        if hasattr(scholarly, '_SESSION'):
            try:
                scholarly._SESSION.headers.update(headers)
                updated = True
            except Exception:
                pass

        # 2) Try to access internal Navigator (private attr name mangled)
        nav = getattr(scholarly, '_Scholarly__nav', None)
        if nav is not None:
            # update session1 and session2 if present
            for sname in ('_session1', '_session2'):
                sess = getattr(nav, sname, None)
                if sess is not None and hasattr(sess, 'headers'):
                    try:
                        sess.headers.update(headers)
                        updated = True
                    except Exception:
                        pass

            # also try proxy manager sessions (pm1/pm2)
            for pm in (getattr(nav, 'pm1', None), getattr(nav, 'pm2', None)):
                if pm is None:
                    continue
                sess = getattr(pm, '_session', None)
                if sess is not None and hasattr(sess, 'headers'):
                    try:
                        sess.headers.update(headers)
                        updated = True
                    except Exception:
                        pass

        if updated:
            logging.debug(f'Set User-Agent on scholarly session(s): {user_agent[:50]}...')
        else:
            logging.debug('Could not set headers on scholarly sessions (no compatible session object found)')
    except Exception as e:
        logging.debug(f'Could not set headers: {e}')
    
    # Retrieve the author's data
    author = scholarly.search_author_id(scholarId)
    # Cast the author to Author object. Pass skip_ids so inner
    # publication handling can avoid expensive per-publication fills.
    return SimplifiedAuthor(author, skip_ids=skip_ids)


# Threaded function for queue processing.
def crawl(scholarID: str, skip_ids: set = None):
    """
    Crawl the author's data from Google Scholar.
    :param scholarID: A Google Scholar ID string.
    :return: The author's data or None if an error occurred.
    """
    data = None
    start_time = time.time()

    # Try to get the data 10 times at most
    for i in range(MAX_RETRIES):
        # Check if we've exceeded the time limit
        elapsed = time.time() - start_time
        if elapsed > MAX_TIME_PER_AUTHOR:
            logging.error(f'Timeout: Spent {elapsed:.1f}s on {scholarID}, giving up.')
            raise Exception(f'CAPTCHA_DETECTED: Timeout after {elapsed:.1f}s - likely stuck on CAPTCHA')
        
        try:
            data = getAuthorData(scholarID, skip_ids=skip_ids)
            break
        except Exception as e:
            error_msg = str(e)
            logging.warning(f'Error scraping {scholarID} (attempt {i+1}/{MAX_RETRIES}): {error_msg}')
            
            # Check if CAPTCHA detected
            if check_for_captcha(error_msg):
                logging.error(f'CAPTCHA detected for {scholarID}! Stopping this author.')
                raise Exception(f'CAPTCHA_DETECTED: {error_msg}')
            
            # Exponential backoff before retry
            if i < MAX_RETRIES - 1:
                backoff = min(2 ** i, 60)  # Max 60 seconds
                logging.info(f'Retrying in {backoff}s...')
                time.sleep(backoff)
                set_new_proxy()

    return data


class ScholarScraper:
    """
    :class:`ScholarScraper <ScholarScraper>` object used to retrieve the data of a list of authors from Google Scholar.
    """

    def __init__(self, scholarIds: List[str] = [], max_threads: int = 10):
        """
        :param scholarIds: The list of the ids of the authors on Google Scholar.
        :param max_threads: The maximum number of threads to use for the scraping process.
        """

        self.scholarIds = scholarIds
        self.max_threads = max_threads
        self.authorsList = []
        self.threads = []
        # Track whether a manual_solve has already been attempted for this run
        self.manual_solve_attempted = False

    def _results_path(self) -> str:
        # results.json lives in the repository root (one level above gs_library)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'results.json'))

    def _save_partial_results(self):
        # Use shared merge-and-save helper so partial saves are deduped and
        # consistent with final merging logic.
        from .utilities import merge_and_save_results
        path = self._results_path()
        try:
            merge_and_save_results(self.authorsList, path)
            logging.info(f'Partial results merged and saved to {path}')
        except Exception as e:
            logging.error(f'Failed to merge & save partial results to {path}: {e}')

    def start_scraping(self, scholarIds: List[str] = None, max_threads: int = None, skip_ids: List[str] = None):
        """
        Start the scraping process.
        :param scholarIds: The list of the ids of the authors on Google Scholar.
        :param max_threads: The maximum number of threads to use for the scraping process.
        :return: The list of the authors' data as JSON.
        """
        self.authorsList = []
        self.threads = []
        self.scholarIds = scholarIds if scholarIds else self.scholarIds
        self.max_threads = max_threads if max_threads else self.max_threads
        # Normalize skip_ids into a set for fast membership checks
        self.skip_ids = set(skip_ids) if skip_ids else set()

        # Attempt to load the last successful manual-solve session (cookies + localStorage)
        try:
            import tempfile
            import glob
            # Prefer a canonical cache file at repo root: .cache/last_solved_session.json
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            cache_dir = os.path.join(repo_root, '.cache')
            os.makedirs(cache_dir, exist_ok=True)
            canonical = os.path.join(cache_dir, 'last_solved_session.json')

            session_file = canonical if os.path.exists(canonical) else None

            if session_file and os.path.exists(session_file):
                try:
                    with open(session_file, 'r', encoding='utf-8') as fh:
                        payload = json.load(fh)
                    cookies = payload.get('cookies', []) if isinstance(payload, dict) else []
                    if cookies:
                        def _inject(sess, cookies_list):
                            # Only insert proper Cookie objects. Do not fall back to
                            # writing raw string values into the cookie jar, which
                            # can corrupt the jar and cause AttributeError during
                            # HTTP processing.
                            for c in cookies_list:
                                if not isinstance(c, dict):
                                    continue
                                name = c.get('name')
                                val = c.get('value')
                                domain = c.get('domain', None)
                                path = c.get('path', '/')
                                if name is None:
                                    continue
                                try:
                                    cookie = requests.cookies.create_cookie(name=name, value=val, domain=domain, path=path)
                                    sess.cookies.set_cookie(cookie)
                                except Exception:
                                    logging.debug(f'Could not create/set cookie {name} on session; skipping.')

                        try:
                            if hasattr(scholarly, '_SESSION'):
                                _inject(scholarly._SESSION, cookies)
                        except Exception:
                            pass

                        nav = getattr(scholarly, '_Scholarly__nav', None)
                        if nav is not None:
                            for sname in ('_session1', '_session2'):
                                sess = getattr(nav, sname, None)
                                if sess is not None:
                                    _inject(sess, cookies)

                            for pm in (getattr(nav, 'pm1', None), getattr(nav, 'pm2', None)):
                                if pm is None:
                                    continue
                                sess = getattr(pm, '_session', None)
                                if sess is not None:
                                    _inject(sess, cookies)

                        logging.info(f'Loaded {len(cookies)} cookies from saved session file: {session_file}')
                        # Validate injected cookies with a lightweight GET to detect
                        # whether Google is still returning the `/sorry` captcha page.
                        try:
                            test_url = 'https://scholar.google.com/'
                            test_sess = None
                            if hasattr(scholarly, '_SESSION') and getattr(scholarly, '_SESSION') is not None:
                                test_sess = scholarly._SESSION
                            else:
                                test_sess = requests.Session()
                                try:
                                    for c in cookies:
                                        if not isinstance(c, dict) or c.get('name') is None:
                                            continue
                                        ck = requests.cookies.create_cookie(
                                            name=c.get('name'),
                                            value=c.get('value'),
                                            domain=c.get('domain', None),
                                            path=c.get('path', '/'),
                                            expires=c.get('expiry')
                                        )
                                        test_sess.cookies.set_cookie(ck)
                                except Exception:
                                    pass

                            try:
                                # Use the same headers as getAuthorData for validation
                                validation_headers = {
                                    'User-Agent': random.choice(USER_AGENTS),
                                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                                    'Accept-Language': 'en-US,en;q=0.9',
                                    'DNT': '1',
                                    'Connection': 'keep-alive',
                                    'Upgrade-Insecure-Requests': '1',
                                    'Referer': 'https://scholar.google.com/',
                                }
                                test_sess.headers.update(validation_headers)
                                r = test_sess.get(test_url, allow_redirects=True, timeout=15)
                                final_url = getattr(r, 'url', '')
                                status = getattr(r, 'status_code', None)
                                blocked = False
                                if status in (429, 302) or (isinstance(final_url, str) and 'sorry' in final_url):
                                    blocked = True
                                else:
                                    try:
                                        body = (r.text or '').lower()
                                        if 'captcha' in body or 'unusual traffic' in body:
                                            blocked = True
                                    except Exception:
                                        pass

                                if blocked:
                                    logging.info('Cached session validation failed (captcha or 429). Removing cached session and continuing; manual solve will be handled centrally when needed')
                                    try:
                                        if session_file and os.path.exists(session_file):
                                            os.remove(session_file)
                                            logging.info(f'Removed cached session file {session_file} due to failed validation')
                                    except Exception:
                                        logging.debug('Could not remove cached session file during validation step')
                                else:
                                    logging.info('Cached session validated OK; proceeding without manual solve')
                                    # Ensure scholarly and navigator use the same validated session
                                    try:
                                        # Assign the validated session object to scholarly._SESSION
                                        try:
                                            scholarly._SESSION = test_sess
                                        except Exception:
                                            pass

                                        # Ensure nav sessions (if present) also reference the same session
                                        nav = getattr(scholarly, '_Scholarly__nav', None)
                                        if nav is not None:
                                            for sname in ('_session1', '_session2'):
                                                try:
                                                    setattr(nav, sname, test_sess)
                                                except Exception:
                                                    pass
                                            for pm in (getattr(nav, 'pm1', None), getattr(nav, 'pm2', None)):
                                                if pm is None:
                                                    continue
                                                try:
                                                    setattr(pm, '_session', test_sess)
                                                except Exception:
                                                    pass
                                    except Exception:
                                        logging.debug('Could not bind validated session into scholarly navigator')
                            except Exception as rex:
                                logging.debug(f'Validation request failed: {rex}')
                        except Exception:
                            pass
                except Exception as le:
                    logging.debug(f'Could not load saved session file {session_file}: {le}')
        except Exception:
            pass

        # Run sequentially (no multithreading) to respect request-rate limits.
        for scholarId in self.scholarIds:
            try:
                res = crawl(scholarId, skip_ids=self.skip_ids)
                if res is not None:
                    self.authorsList.append(res)
            except Exception as e:
                err = str(e)
                is_captcha = False
                if CaptchaDetectedException is not None and isinstance(e, CaptchaDetectedException):
                    is_captcha = True
                elif 'CAPTCHA_DETECTED' in err.upper():
                    is_captcha = True

                if is_captcha:
                    logging.error(f'CAPTCHA detected for {scholarId}: saving partial results and stopping. Manual solve is handled centrally.')
                    self._save_partial_results()
                    # Propagate a clear error so the top-level runner can save/exit as needed
                    raise Exception(f'CAPTCHA_DETECTED: CAPTCHA encountered while processing {scholarId}')
                else:
                    logging.warning(f'Error scraping {scholarId}: {e}. Continuing with next author.')

        return json.dumps(self.authorsList, cls=JSONEncoder, sort_keys=True, indent=4, ensure_ascii=False)
