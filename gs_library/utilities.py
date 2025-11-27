from json import JSONEncoder
import re
import unicodedata


def getObjectPublicAttributes(obj):
    """
    Get all the public attributes of an object.
    :param obj: The object to get the attributes from.
    :return: The dictionary of the public attributes.
    """
    return [attr for attr in dir(obj.__class__) if
            not callable(getattr(obj.__class__, attr))
            and not attr.startswith("__")
            and not attr.startswith("_")
            and not attr.startswith("_" + obj.__class__.__name__ + "__")]


class JSONEncoder(JSONEncoder):
    """
    Simple JSON encoder class that allows to serialize objects that are not serializable by default.
    """

    def default(self, o):
        """
        Returns the object's dictionary if it has one But only the attributes that are returned by the
        :meth:`getObjectPublicAttributes` function.
        :param o: The object to serialize
        :return: The object's dictionary
        """
        if not hasattr(o, '__dict__'):
            return JSONEncoder.default(self, o)

        if hasattr(o, '_class_attributes'):
            return {k: v for k, v in o.__dict__.items() if k in o._class_attributes}

        return {k: v for k, v in o.__dict__.items() if k in getObjectPublicAttributes(o)}


def _make_serializable(obj):
    """Lightweight conversion of objects to JSON-serializable structures."""
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if hasattr(obj, '__dict__'):
        return _make_serializable(obj.__dict__)
    return str(obj)


def _normalize_title_for_dedupe(title: str) -> str:
    """Normalize a publication title for comparison.

    - Normalize unicode (NFKD), remove diacritics
    - Lowercase, remove punctuation, collapse whitespace
    Returns a short canonical string to compare titles.
    """
    if not title:
        return ''
    try:
        # Normalize unicode and strip combining marks
        t = unicodedata.normalize('NFKD', title)
        t = ''.join(ch for ch in t if not unicodedata.combining(ch))
        # Remove punctuation (keep word characters and whitespace)
        t = re.sub(r"[^\w\s]", ' ', t)
        # Collapse whitespace and lowercase
        t = re.sub(r'\s+', ' ', t).strip().lower()
        return t
    except Exception:
        return title.strip().lower()


def merge_and_save_results(partial_authors, results_file_path):
    """Merge partial scraped authors with existing results file and save atomically.

    - Dedupe authors by `scholar_id`.
    - For each author, dedupe publications by `author_pub_id`, preferring newer entries from `partial_authors`.
    - Stamp missing `last_scraped` timestamps with current UTC ISO time.
    - Write atomically to `results_file_path`.
    """
    import json
    import os
    import datetime

    # Load existing authors
    existing = []
    if os.path.exists(results_file_path):
        try:
            with open(results_file_path, 'r', encoding='utf-8') as f:
                existing = json.load(f) or []
        except Exception:
            existing = []

    author_map = {}
    for a in existing:
        sid = a.get('scholar_id')
        if sid:
            author_map[sid] = a

    now_iso = datetime.datetime.utcnow().isoformat() + 'Z'

    for scraped in partial_authors:
        if not scraped:
            continue
        # normalize scraped to dict
        if hasattr(scraped, '__dict__'):
            scraped = _make_serializable(scraped)
        if not isinstance(scraped, dict):
            # fallback: stringify
            continue
        scholar_id = scraped.get('scholar_id')
        if not scholar_id:
            continue

        # Ensure publications present
        new_pubs = scraped.get('publications', []) or []
        if scholar_id in author_map:
            old_pubs = author_map[scholar_id].get('publications', []) or []
            pub_map = {p.get('author_pub_id'): p for p in old_pubs if p.get('author_pub_id')}
            for pub in new_pubs:
                pid = pub.get('author_pub_id')
                if not pid:
                    continue
                # If we already have an older publication entry, merge fields
                # so that missing fields in the new (sparse) one don't wipe
                # richer data from the existing entry.
                if pid in pub_map:
                    existing = pub_map[pid] or {}
                    merged = existing.copy()
                    # Overwrite with any fields provided by the new publication
                    merged.update(pub)
                    pub_map[pid] = merged
                else:
                    pub_map[pid] = pub
            # update author entry
            author_map[scholar_id] = scraped
            author_map[scholar_id]['publications'] = list(pub_map.values())
        else:
            # new author
            author_map[scholar_id] = scraped
            author_map[scholar_id]['publications'] = new_pubs

    # Stamp missing last_scraped on publications
    for a in author_map.values():
        pubs = a.get('publications', []) or []
        for p in pubs:
            if isinstance(p, dict) and not p.get('last_scraped'):
                p['last_scraped'] = now_iso

    # Deduplicate publications across all authors by normalized title
    # (fall back to `author_pub_id` when title is missing).
    seen_titles = set()
    for a in author_map.values():
        pubs = a.get('publications', []) or []
        new_pubs = []
        for p in pubs:
            if not isinstance(p, dict):
                continue

            # Extract title from common locations
            title = p.get('title') or (p.get('bib') and p['bib'].get('title')) or ''
            normalized = _normalize_title_for_dedupe(title)

            # If no title available, fall back to author_pub_id
            if not normalized:
                pid = p.get('author_pub_id') or ''
                normalized = f"__id__:{pid}"

            if normalized in seen_titles:
                # Skip duplicate publication (title already seen)
                continue
            seen_titles.add(normalized)
            new_pubs.append(p)

        # Replace publications with deduped list
        author_map[a.get('scholar_id')] = a
        a['publications'] = new_pubs

    merged = list(author_map.values())

    # Write atomically
    temp_path = results_file_path + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, results_file_path)
    except Exception as e:
        # Attempt best-effort write to final path
        try:
            with open(results_file_path, 'w', encoding='utf-8') as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception:
            raise
