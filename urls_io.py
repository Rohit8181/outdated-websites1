"""urls_io.py - CSV ya Excel (.xlsx) se links padhta hai. Header kuch bhi ho (url/website/...) chalega."""
import csv

HEAD_WORDS = ("url", "website", "site", "domain", "link", "web")


def _rows(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        rows = [["" if c is None else str(c) for c in r] for r in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rows = list(csv.reader(fh))
    return [r for r in rows if any(c.strip() for c in r)]


def _looks_url(v):
    v = v.strip()
    return bool(v) and " " not in v and "." in v and not v.replace(".", "").isdigit()


def read_urls(path):
    """Links ki list (original likhawat me), blank/galat rows hata kar."""
    rows = _rows(path)
    if not rows:
        return []
    head = [c.strip().lower() for c in rows[0]]
    idx = next((i for i, h in enumerate(head) if any(k in h for k in HEAD_WORDS)), None)
    body = rows[1:]
    if idx is None:                     # header nahi mila: jis column me sabse zyada URL jaise values hon
        ncol = max(len(r) for r in rows)
        score = [sum(_looks_url(r[i]) for r in rows[:200] if i < len(r)) for i in range(ncol)]
        idx, body = score.index(max(score)), rows
    out = []
    for r in body:
        u = (r[idx] if idx < len(r) else "").strip().strip("\"'")
        if _looks_url(u):
            out.append(u)
    return out


def normalize_url(raw_url):
    url = raw_url.strip()
    if not url:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")
