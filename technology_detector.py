"""
technology_detector.py
=======================
Homepage HTML se saare "raw signals" nikalta hai jo scorer.py baad mein
use karega -- jaise viewport tag, tables, fixed-width containers,
deprecated tags, copyright year, CTA presence, waghera.

Ye sirf DATA EXTRACT karta hai, koi scoring decision nahi leta.
"""

import re
from bs4 import BeautifulSoup

COMMON_BOXED_WIDTHS = [720, 750, 760, 780, 800, 900, 940, 950, 960,
                        970, 980, 990, 1000, 1024, 1100, 1140, 1170, 1200]

DEPRECATED_TAGS = ["font", "center", "marquee", "blink", "frameset", "frame"]

HAMBURGER_KEYWORDS = ["hamburger", "burger", "navbar-toggler", "menu-toggle",
                       "mobile-menu", "menu-icon", "nav-toggle", "toggle-menu",
                       "mobile-nav", "menu-btn", "nav-burger"]

CTA_KEYWORDS = ["get a quote", "request a quote", "request quote", "rfq",
                "enquire now", "enquiry", "contact us", "buy now",
                "get in touch", "book a demo", "request a callback",
                "download brochure", "get started"]

OLD_BUILDER_HINTS = ["frontpage", "dreamweaver", "zetabyte", "web element",
                      "raj technologies", "daksh it", "netobjects fusion"]

BEST_VIEWED_RE = re.compile(
    r"best\s+(viewed|seen)\s+(in|with|at|on)|optimi[sz]ed\s+for\s+(internet\s+explorer|ie\b|1024|800)"
    r"|\b1024\s*[x\u00d7*]\s*768\b|\b800\s*[x\u00d7*]\s*600\b|internet\s+explorer\s*[4-8]",
    re.IGNORECASE)

DESIGNED_BY_RE = re.compile(
    r"(web\s*site|site|web)?\s*(design(ed)?|develop(ed)?|maintained|hosted|promoted)"
    r"(\s*(&|and|,)\s*(design(ed)?|develop(ed)?|maintained|hosted|promoted|search engine optimi[sz]ation))*\s+by\b",
    re.IGNORECASE)

PLUGIN_TEXT_RE = re.compile(r"flash\s+player|plugin\s+is\s+not\s+supported|shockwave", re.IGNORECASE)


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def detect_technology_signals(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    signals = {}

    # ---- Viewport / responsive signals ----
    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    if viewport_tag and viewport_tag.get("content"):
        content = viewport_tag.get("content", "").lower().replace(" ", "")
        signals["viewport_present"] = True
        signals["viewport_device_width"] = "width=device-width" in content
    else:
        signals["viewport_present"] = False
        signals["viewport_device_width"] = False

    inline_styles = " ".join(tag.get_text() for tag in soup.find_all("style"))
    signals["media_query_present"] = "@media" in inline_styles.lower()

    # ---- Fixed-width / boxed layout signals ----
    fixed_widths_found = []
    for tag in soup.find_all(style=True):
        style = tag.get("style", "").lower().replace(" ", "")
        for match in re.finditer(r"width:(\d+)px", style):
            w = int(match.group(1))
            if w in COMMON_BOXED_WIDTHS:
                fixed_widths_found.append(w)
    for tag in soup.find_all(width=True):
        w = re.sub(r"[^\d]", "", str(tag.get("width", "")))
        if w.isdigit() and int(w) in COMMON_BOXED_WIDTHS:
            fixed_widths_found.append(int(w))
    signals["fixed_width_containers"] = fixed_widths_found
    signals["has_fixed_width"] = len(fixed_widths_found) > 0

    # ---- Table-layout signals ----
    tables = soup.find_all("table")
    big_tables = [t for t in tables if len(t.find_all("tr")) > 1]
    signals["table_count"] = len(tables)
    signals["big_table_count"] = len(big_tables)

    # ---- Deprecated tags / old tech ----
    deprecated_count = 0
    for tag_name in DEPRECATED_TAGS:
        deprecated_count += len(soup.find_all(tag_name))
    signals["deprecated_tag_count"] = deprecated_count

    # Flash remnants
    flash_found = False
    for tag in soup.find_all(["embed", "object"]):
        tag_str = str(tag).lower()
        if ".swf" in tag_str or "shockwave" in tag_str:
            flash_found = True
            break
    signals["flash_remnants"] = flash_found

    # Inline CSS density (many style="" attributes = old-school markup)
    inline_style_count = len(soup.find_all(style=True))
    signals["inline_style_count"] = inline_style_count

    # ---- Framework / generator hints ----
    generator_tag = soup.find("meta", attrs={"name": "generator"})
    generator_content = (generator_tag.get("content", "") if generator_tag else "")
    signals["generator"] = generator_content

    all_hrefs_srcs = []
    for tag in soup.find_all(["link", "script"]):
        val = tag.get("href") or tag.get("src") or ""
        all_hrefs_srcs.append(val.lower())
    combined_links = " ".join(all_hrefs_srcs)

    signals["uses_bootstrap"] = "bootstrap" in combined_links
    old_bootstrap_hint = bool(re.search(r"bootstrap[/-]?[23]\.", combined_links))
    signals["old_bootstrap_hint"] = old_bootstrap_hint

    body_html_lower = html.lower()
    signals["old_builder_hint"] = any(hint in body_html_lower for hint in OLD_BUILDER_HINTS)

    # ---- Navigation / hamburger menu ----
    nav_present = bool(soup.find("nav")) or bool(soup.find("ul"))
    all_class_id_text = []
    for tag in soup.find_all(True):
        classes = tag.get("class") or []
        all_class_id_text.extend(classes)
        tag_id = tag.get("id")
        if tag_id:
            all_class_id_text.append(tag_id)
    combined_class_id = " ".join(all_class_id_text).lower()
    has_hamburger = any(k in combined_class_id for k in HAMBURGER_KEYWORDS)
    signals["nav_present"] = nav_present
    signals["nav_has_hamburger"] = has_hamburger

    # ---- Content freshness ----
    text = _extract_text(soup)
    signals["word_count"] = len(text.split())
    signals["image_count"] = len(soup.find_all("img"))

    year_matches = re.findall(r"(?:©|copyright)\D{0,10}(\d{4})", text, re.IGNORECASE)
    years_found = [int(y) for y in year_matches if 1995 <= int(y) <= 2100]
    signals["copyright_year"] = max(years_found) if years_found else None

    signals["fax_mentioned"] = bool(re.search(r"\bfax\b", text, re.IGNORECASE))
    signals["best_viewed_text"] = bool(BEST_VIEWED_RE.search(text))
    signals["designed_by_credit"] = bool(DESIGNED_BY_RE.search(text))
    signals["plugin_text"] = bool(PLUGIN_TEXT_RE.search(text))

    text_lower = text.lower()
    signals["has_cta"] = any(kw in text_lower for kw in CTA_KEYWORDS)

    return signals
