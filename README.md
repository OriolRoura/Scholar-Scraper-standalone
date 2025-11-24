```markdown
Scholar Scraper

Purpose
-------
This repository contains a standalone Google Scholar scraping tool. It enumerates authors
and their publications, fetches per-publication metadata (abstract, journal, pages,
publisher, URLs, citation metrics, etc.), and writes consolidated output to a JSON file
suitable for downstream ingestion (for example a WordPress importer).

Requirements
------------
Install core runtime dependencies into a Python virtual environment. Below are
equivalent commands for the most common shells — pick the one that matches your
environment.

Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r .\requirements.txt
```

Windows (Command Prompt)

```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
py -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS / Linux (bash / zsh)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Notes
- If your system's Python is invoked with `python` instead of `python3`, use
  `python` for the POSIX example above.
- If you prefer not to use a virtual environment, you can install globally with
  `pip install -r requirements.txt` (not recommended — virtualenvs avoid
  dependency conflicts).
- Optional Selenium/browser automation dependencies live in
  `requirements-optional.txt`. Install them only when you want the scraper to
  automatically open a visible browser for manual CAPTCHA solves:

```powershell
pip install -r .\requirements-optional.txt
```

What the commands do
- `python -m venv .venv`: Creates an isolated virtual environment in `.venv`.
- `source .venv/bin/activate` / `.\.venv\Scripts\Activate.ps1` /
  `.\.venv\Scripts\activate.bat`: Activates the virtual environment in the
  current shell.
- `pip install -U pip`: Updates pip inside the virtual environment.
- `pip install -r requirements.txt`: Installs the project's core Python
  dependencies.

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

