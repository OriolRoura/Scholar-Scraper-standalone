#!/usr/bin/env python3
import sys
import os
from pathlib import Path
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Use the local `scholarly` package; import its public instance
from scholarly import scholarly
from gs_library import scholar_scraper
from gs_library.utilities import merge_and_save_results
# Try to import dedicated captcha exception for robust detection
try:
    from scholarly._proxy_generator import CaptchaDetectedException
except Exception:
    CaptchaDetectedException = None

# Ensure library logging goes to stderr so stdout stays clean for JSON
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s - %(levelname)s: %(message)s")


def load_config():
    """Load configuration from config.json file"""
    config_path = Path(__file__).parent / 'config.json'
    default_config = {
        'results_file': 'results.json',
        'rescrape_threshold_days': 7,
        'scholar_ids': []
    }
    
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**default_config, **config}
        except Exception as e:
            logging.warning(f'Failed to load config.json: {e}, using defaults')
    else:
        logging.info('config.json not found, using defaults')
    
    return default_config


def load_existing_results(results_file):
    """Load existing results from file, return empty list if file doesn't exist"""
    results_path = Path(results_file)
    
    if not results_path.exists():
        logging.info(f'Results file {results_file} does not exist, starting fresh')
        return []
    
    try:
        with open(results_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                logging.info(f'Loaded {len(data)} authors from {results_file}')
                return data
            else:
                logging.warning(f'Invalid format in {results_file}, expected list')
                return []
    except Exception as e:
        logging.error(f'Failed to load {results_file}: {e}')
        return []


def check_author_needs_scraping(author, threshold_days):
    """Check if author needs scraping based on publication timestamps"""
    scholar_id = author.get('scholar_id')
    if not scholar_id:
        return None
    
    now = datetime.now(timezone.utc)
    threshold = timedelta(days=threshold_days)
    publications = author.get('publications', [])
    
    if not publications:
        return scholar_id
    
    for pub in publications:
        last_scraped = pub.get('last_scraped')
        
        if not last_scraped:
            return scholar_id
        
        try:
            last_scraped_dt = datetime.fromisoformat(last_scraped.replace('Z', '+00:00'))
            age = now - last_scraped_dt
            
            if age > threshold:
                return scholar_id
        except Exception as e:
            logging.warning(f'Failed to parse timestamp: {e}')
            return scholar_id
    
    return None


# Load configuration
config = load_config()
results_file = config['results_file']
threshold_days = config['rescrape_threshold_days']
config_scholar_ids = config.get('scholar_ids', [])

logging.info(f'Configuration: results_file={results_file}, threshold_days={threshold_days}')

# Load existing results
existing_authors = load_existing_results(results_file)

# Use scholar IDs from config if provided, otherwise check existing authors
if config_scholar_ids:
    logging.info(f'Using {len(config_scholar_ids)} scholar IDs from config file')
    scholarIds = config_scholar_ids
else:
    logging.info('No scholar_ids in config, checking existing authors in results file')
    if not existing_authors:
        logging.warning('No existing authors found. Results file is empty or does not exist.')
        logging.info('Please add scholar_ids to config.json or populate results.json')
        scholarIds = []
    else:
        # Determine which authors need scraping
        scholarIds = []
        for author in existing_authors:
            scholar_id = check_author_needs_scraping(author, threshold_days)
            if scholar_id:
                scholarIds.append(scholar_id)
        
        logging.info(f'Found {len(scholarIds)} authors needing updates out of {len(existing_authors)} total')

if not scholarIds:
    logging.info('No authors to scrape')
    sys.exit(0)



def make_serializable(obj):
    # Recursively convert objects to JSON-serializable structures
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_serializable(v) for v in obj]
    if hasattr(obj, '__dict__'):
        return make_serializable(obj.__dict__)
    return str(obj)

# --- Patch: Add deduplication and sleep logic ---

#class RequestTracker:
#    def __init__(self):
#        self.seen_urls = set()

#    def fetch(self, url, fetch_func):
#        if url in self.seen_urls:
#            logging.info(f"SKIP: Already fetched {url}")
#            return None
#        self.seen_urls.add(url)
#        # Sleep randomly between 1 and 3 seconds before each request
#        time.sleep(random.uniform(1, 3))
#        return fetch_func(url)

#request_tracker = RequestTracker()



try:
    # Call the scraper with the list of scholar IDs that need updating
    logging.info(f'Starting scrape for {len(scholarIds)} authors...')
    
    # Build skip_ids from fresh publications
    skip_ids = []
    now = datetime.now(timezone.utc)
    threshold = timedelta(days=threshold_days)
    
    for author in existing_authors:
        for pub in author.get('publications', []):
            last_scraped = pub.get('last_scraped')
            if last_scraped:
                try:
                    last_scraped_dt = datetime.fromisoformat(last_scraped.replace('Z', '+00:00'))
                    age = now - last_scraped_dt
                    if age <= threshold:
                        pub_id = pub.get('author_pub_id')
                        if pub_id:
                            skip_ids.append(pub_id)
                except Exception:
                    pass
    
    if skip_ids:
        logging.info(f'Skipping {len(skip_ids)} fresh publications')
    
    results = scholar_scraper.start_scraping(scholarIds, skip_ids=skip_ids)
    
except Exception as e:
    error_msg = str(e)
    
    # Check if CAPTCHA was detected (either by exception type or message)
    is_captcha = False
    if CaptchaDetectedException is not None and isinstance(e, CaptchaDetectedException):
        is_captcha = True
    elif 'CAPTCHA_DETECTED' in error_msg.upper():
        is_captcha = True
    if is_captcha:
        logging.error('CAPTCHA detected during scraping; will save partial results and exit.')
        partial_results = getattr(scholar_scraper, 'authorsList', None)
        if partial_results:
            try:
                merge_and_save_results(partial_results, results_file)
                logging.info(f'Partial results saved to {results_file} (exit on CAPTCHA)')
            except Exception as save_error:
                logging.error(f'Failed to save partial results on CAPTCHA: {save_error}')
        else:
            logging.warning('No partial results available to save on CAPTCHA')
        sys.exit(3)
    else:
        # Re-raise non-CAPTCHA exceptions
        raise

try:
    # If the scraper already returned a JSON string, avoid double-encoding.
    # If it returned native Python objects, make them serializable and dump.
    json_text = None
    if isinstance(results, (bytes, bytearray)):
        try:
            results = results.decode('utf-8')
        except Exception:
            results = str(results)

    def _stamp_last_scraped(obj):
        """Walk authors->publications and stamp missing last_scraped with UTC ISO-8601."""
        try:
            # Use a Z-terminated UTC timestamp for consistency with other helpers
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            if isinstance(obj, list):
                for author in obj:
                    if not isinstance(author, dict):
                        continue
                    pubs = author.get('publications') if isinstance(author.get('publications'), list) else []
                    for p in pubs:
                        if not isinstance(p, dict):
                            continue
                        if not p.get('last_scraped'):
                            p['last_scraped'] = now_iso
            elif isinstance(obj, dict):
                pubs = obj.get('publications') if isinstance(obj.get('publications'), list) else []
                for p in pubs:
                    if not isinstance(p, dict):
                        continue
                    if not p.get('last_scraped'):
                        p['last_scraped'] = now_iso
        except Exception:
            pass

    if isinstance(results, str):
        # Try to see if it's a JSON-encoded string containing the real structure.
        try:
            parsed = json.loads(results)
            # If parsing yields a string, it means the result was a
            # JSON-encoded string (double-encoded). Try one more decode.
            if isinstance(parsed, str):
                try:
                    parsed2 = json.loads(parsed)
                    parsed = parsed2
                except Exception:
                    # couldn't double-decode; fall back to the parsed string
                    pass
            # parsed is now hopefully a native Python structure (list/dict/etc).
            # Stamp missing last_scraped timestamps in parsed structure before dumping
            try:
                _stamp_last_scraped(parsed)
            except Exception:
                pass
            json_text = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            # If it isn't parseable, assume it's already the JSON text we want
            # (or plain text). Output as-is to avoid wrapping it again.
            json_text = results
    else:
        serializable = make_serializable(results)
        try:
            _stamp_last_scraped(serializable)
        except Exception:
            pass
        json_text = json.dumps(serializable, ensure_ascii=False)

    # Parse the scraped results
    if isinstance(results, str):
        try:
            parsed = json.loads(results)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            results = parsed
        except Exception:
            pass
    
    # Convert to list if needed
    if not isinstance(results, list):
        results = [results] if results else []
    
    # Merge old and new results
    author_map = {}
    
    # Index existing authors
    for author in existing_authors:
        scholar_id = author.get('scholar_id')
        if scholar_id:
            author_map[scholar_id] = author
    
    # Merge with newly scraped authors
    for scraped_author in results:
        if not scraped_author:
            continue
        
        scholar_id = scraped_author.get('scholar_id')
        if not scholar_id:
            continue
        
        if scholar_id in author_map:
            # Merge publications (remove duplicates, keep newest)
            old_pubs = author_map[scholar_id].get('publications', [])
            new_pubs = scraped_author.get('publications', [])
            
            pub_map = {}
            
            # Add old publications
            for pub in old_pubs:
                pub_id = pub.get('author_pub_id')
                if pub_id:
                    pub_map[pub_id] = pub
            
            # Override with new publications
            for pub in new_pubs:
                pub_id = pub.get('author_pub_id')
                if not pub_id:
                    continue
                if pub_id in pub_map:
                    existing = pub_map[pub_id] or {}
                    merged = existing.copy()
                    merged.update(pub)
                    pub_map[pub_id] = merged
                else:
                    pub_map[pub_id] = pub
            
            # Update author with merged publications
            author_map[scholar_id] = scraped_author
            author_map[scholar_id]['publications'] = list(pub_map.values())
        else:
            # New author
            author_map[scholar_id] = scraped_author
    
    # Convert back to list
    merged_results = list(author_map.values())
    
    # Convert to JSON
    if not isinstance(json_text, str):
        json_text = str(json_text)
    
    final_json = json.dumps(merged_results, ensure_ascii=False, indent=2)
    
    # Save using shared helper which performs dedupe, stamping and atomic write
    try:
        merge_and_save_results(merged_results, results_file)
        logging.info(f'Successfully wrote results to {results_file}')
        logging.info(f'Total: {len(merged_results)} authors')
        # Emit the saved file to stdout for compatibility
        try:
            with open(results_file, 'r', encoding='utf-8') as f:
                saved_text = f.read()
            sys.stdout.write(saved_text)
            sys.stdout.flush()
        except Exception:
            # Fallback to emitting the JSON we generated
            sys.stdout.write(final_json)
            sys.stdout.flush()
        sys.exit(0)
    except Exception as e:
        logging.error(f'Failed to write results to {results_file}: {e}')
        sys.exit(2)
    
except Exception as e:
    logging.exception('Scraper failed:')
    error_msg = str(e)
    # If this was a CaptchaDetectedException, save partial results and exit 3
    is_captcha = False
    if CaptchaDetectedException is not None and isinstance(e, CaptchaDetectedException):
        is_captcha = True
    elif 'CAPTCHA_DETECTED' in error_msg.upper():
        is_captcha = True

    if is_captcha:
        logging.error('CAPTCHA detected at top-level! Attempting to save partial results and exit.')
        partial_results = getattr(scholar_scraper, 'authorsList', None)
        if partial_results:
            try:
                merge_and_save_results(partial_results, results_file)
                logging.info(f'Partial results saved to {results_file} (exit on CAPTCHA)')
            except Exception as save_error:
                logging.error(f'Failed to save partial results on CAPTCHA: {save_error}')
        else:
            logging.warning('No partial results available to save on CAPTCHA')
        sys.exit(3)

    # Don't double-handle CAPTCHA errors otherwise
    if 'CAPTCHA_DETECTED' not in error_msg:
        print('ERROR: ' + error_msg, file=sys.stderr)

    sys.exit(2)
