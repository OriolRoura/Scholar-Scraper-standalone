
Scholar Scraper


Purpose
-------
This repository contains a standalone Google Scholar scraping tool. It enumerates authors
and their publications, fetches per-publication metadata (abstract, journal, pages,
publisher, URLs, citation metrics, etc.), and writes consolidated output to a JSON file
suitable for downstream ingestion (for example a WordPress importer).

Requirements
------------
Create and activate a Python virtual environment first so dependencies are
installed into an isolated environment (recommended). After the venv is active,
install the project's required Python packages using the single command below.

```powershell
pip install -r .\requirements.txt
```

Configuration
-------------
The scraper reads `config.json` to determine behavior. Example `config.json`:

```json
{
  "results_file": "results.json",
  "rescrape_threshold_days": 7,
  "scholar_ids": ["xxxxxxxx"]
}
```

- `results_file`: path to the JSON results file written by the scraper.
- `rescrape_threshold_days`: how many days to skip re-scraping an already-scraped
  publication (if a publication's `last_scraped` timestamp is newer than this, the
  publication will be skipped to save requests).
- `scholar_ids`: list of Google Scholar author IDs to process.

Quick run
---------
From the repository root (PowerShell example):

```powershell
.\.venv\Scripts\Activate.ps1
python scraper.py
```

Primary outputs
---------------
- `results.json` — consolidated authors and publications. 
  a `last_scraped` timestamp when it was processed.
- `.cache/last_solved_session.json` — optional cookies/localStorage created
  after a manual CAPTCHA solve. Reusing this file can avoid repeated manual solves.

