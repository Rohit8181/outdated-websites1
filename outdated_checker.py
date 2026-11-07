#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outdated_checker.py - purani + mobile-friendly NAHI websites ko dhundta hai (resumable).

  python outdated_checker.py scan websites.csv              # check karo (resume apne aap)
  python outdated_checker.py scan websites.csv --render     # + browser se size/mobile measure (zyada accurate)
  python outdated_checker.py scan websites.csv --retry-errors
  python outdated_checker.py export                         # CSV files dobara banao
  python outdated_checker.py calibrate labels.csv           # labels (url,label 1/0) se accuracy napo

Install:  pip install requests beautifulsoup4 lxml
Render:   pip install playwright  &&  python -m playwright install chromium
"""
import argparse, asyncio, csv, datetime as dt, json, re, socket, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings()
try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

VERSION = "v13 FINAL - purana BOX + mobile-friendly NAHI; browser block ho to code-se backup; rejected.csv me wajah"
THIS_YEAR = dt.date.today().year
HOT_MIN, HIGH_MIN = 50, 35          # score thresholds (calibrate se tune karo)
MIN_VISUAL = 3                      # kam se kam itne DIKHNE wale purane signal chahiye (galat leads rokne ke liye)
# Wo signals jo site ke LOOK ko batate hain (copyright/http/no-doctype/old-jquery sirf supporting hain)
VISUAL = {"boxed", "fixed_width", "off_center", "table_layout", "deprecated", "body_attrs", "spacer", "img_nav",
          "tiny_font", "tiny_font_r", "justify", "flash", "frames", "best_viewed", "counter"}
# ---- v12: aapki requirement (screenshots wali type) ----
MAX_BOX_W = 1200        # box ki chaudai is se zyada ho to "box" nahi (full-width site)
MIN_BOX_W = 380
MIN_OLD = 1             # box ke alawa kam se kam itne purane-look ke saboot (table/font tag/image menu/spacer...)
OLD_LOOK = {"table_layout", "deprecated", "body_attrs", "spacer", "img_nav", "flash", "frames", "best_viewed",
            "counter", "dreamweaver", "old_builder", "old_libs"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

# ----------------------------------------------------------------------------- signals
class S(list):
    def add(self, id, fam, w, lab, txt=None, anchor=False):
        self.append(dict(id=id, fam=fam, w=w, lab=lab, txt=txt or lab, anchor=anchor))

MODERN_RE = re.compile(r"wixstatic\.com|parastorage|squarespace\.com|webflow\.com|cdn\.shopify|_next/static|"
                       r"__nuxt|data-reactroot|ng-version|elementor|et_pb_|wp-block-|gatsby|framer\.com", re.I)
RESP_RE = re.compile(r"bootstrap|foundation\.(min\.)?css|tailwind|col-(xs|sm|md|lg)-\d|uk-grid|bulma", re.I)
FLASH_RE = re.compile(r"\.swf\b|shockwave|swfobject|ac_runactivecontent|flashvars", re.I)
DW_RE = re.compile(r"MM_(swapImg|preloadImages|findObj|jumpMenu|openBrWindow|showHideLayers)|InstanceBegin|"
                   r"#BeginTemplate|FrontPage|vti_|NetObjects|mso-", re.I)
SOCIAL_RE = re.compile(r"facebook|twitter|linkedin|instagram|youtube|pinterest|yelp|bbb\.org|google|aicpa|chamber|\bx\.com", re.I)
BUILDER_RE = re.compile(r"homestead|cch\s*site\s*builder|frontpage|dreamweaver|\\biweb\\b|\\bnvu\\b|kompozer|coffeecup|trellian|sitespinner|netobjects|webstarts|yola", re.I)
BV_RE = re.compile(r"best\s+(viewed|seen)\s+(in|with|at|on)|optimi[sz]ed\s+for\s+(screen\s+)?(resolution|"
                   r"\d{3,4}\s*[x\u00d7]\s*\d{3,4}|internet\s+explorer|netscape)|\b(800|1024)\s*[x\u00d7]\s*(600|768)\b", re.I)
CTR_RE = re.compile(r"visitor\s*(counter|number)|hit\s*counter|you\s+are\s+(the\s+)?visitor|guest\s*book|"
                    r"under\s+construction|page\s+hits|webcounter", re.I)
OLDLIB_RE = re.compile(r"prototype\.js|mootools|scriptaculous|yui[-/.]|yahoo-dom|dojo\.js|ext-all|lightbox\.js|"
                       r"shadowbox|thickbox|cufon|sifr", re.I)
COPY_RE = re.compile(r"(?:\u00a9|\(c\)|copyright)\s*(?:&copy;)?[^0-9]{0,30}?((?:19|20)\d{2})"
                     r"(?:\s*(?:-|\u2013|\u2014|to)\s*((?:19|20)\d{2}))?", re.I)
WRAP_RE = re.compile(r"(^|[\s,>#.])(body|wrapper|container|page|main|outer|layout|content|wrap|site|holder|master)\b", re.I)


def fixed_width(soup, css):
    """Fixed pixel width (500-1100px) of the main page box, or None."""
    for t in soup.find_all("table", limit=6):
        w = (t.get("width") or "").strip()
        if w.isdigit() and 500 <= int(w) <= 1100:
            return int(w)
    for el in soup.find_all(["div", "table", "body"], limit=12):
        m = re.search(r"(?<![-\w])width\s*:\s*(\d{3,4})px", (el.get("style") or "").lower())
        if m and 500 <= int(m.group(1)) <= 1100:
            return int(m.group(1))
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        if WRAP_RE.search(m.group(1)):
            w = re.search(r"(?<![-\w])width\s*:\s*(\d{3,4})px", m.group(2))
            if w and 500 <= int(w.group(1)) <= 1100:
                return int(w.group(1))
    return None


def analyze_html(html, final_url="", css_texts=(), ssl_error=False, css_missing=0):
    """Pure function: HTML in -> (signals, meta). Network nahi chalata, isliye test ho sakta hai."""
    soup = BeautifulSoup(html, PARSER)
    low = html.lower()
    css = "\n".join([t.get_text() for t in soup.find_all("style")] + list(css_texts)).lower()
    text = soup.get_text(" ", strip=True)
    srcs = " ".join(s.get("src", "") for s in soup.find_all("script")).lower()
    sg, meta = S(), {}
    modern = bool(MODERN_RE.search(low))

    # --- tech / layout
    if FLASH_RE.search(html):
        sg.add("flash", "TECH", 25, "Flash content", "Flash/plugin content (dead since 2020)", True)
    if soup.find("frameset"):
        sg.add("frames", "LAYOUT", 25, "Frames layout", "Old frames-based layout", True)
    tables = soup.find_all("table")
    lay = [t for t in tables if t.find_parent("td") or any(t.has_attr(a) for a in
           ("width", "cellpadding", "cellspacing", "bgcolor"))]
    top = [t for t in tables if not t.find_parent("table")]
    share = sum(len(t.get_text(" ", strip=True)) for t in top) / max(1, len(text))
    if len(lay) >= 3:
        sg.add("table_layout", "LAYOUT", 20, "Table-based layout", f"Page built with {len(lay)} layout tables", True)
    elif top and len(text) > 200 and share >= 0.6:
        sg.add("table_layout", "LAYOUT", 20, "Table-based layout", f"Most page content ({round(share * 100)}%) sits inside tables", True)
    elif len(lay) == 2:
        sg.add("table_layout", "LAYOUT", 12, "Table-based layout", "Page built with layout tables", True)
    elif len(lay) == 1:
        sg.add("table_layout", "LAYOUT", 6, "Table layout (partial)")
    dep = sum(len(soup.find_all(t)) for t in ("font", "center", "marquee", "blink", "big", "strike",
                                              "basefont", "bgsound", "applet", "tt"))
    if dep >= 10:
        sg.add("deprecated", "MARKUP", 18, "Deprecated HTML tags", f"{dep} deprecated tags (font/center/marquee)", True)
    elif dep >= 2:
        sg.add("deprecated", "MARKUP", 10, "Deprecated HTML tags", f"{dep} deprecated tags (font/center/marquee)", True)
    elif dep == 1:
        sg.add("deprecated", "MARKUP", 8, "Deprecated HTML tag")
    body = soup.find("body")
    if body and any(body.has_attr(a) for a in ("bgcolor", "text", "link", "vlink", "background",
                                              "topmargin", "leftmargin", "marginwidth")):
        sg.add("body_attrs", "MARKUP", 6, "Old <body> attributes")
    imgs = soup.find_all("img")
    if any(re.search(r"(?:^|[/_.-])(spacer|blank|clear|pixel|shim|transparent|trans|tp|dot|space|1x1)\.(gif|png)(?:\?|$)", i.get("src", ""), re.I)
           or (str(i.get("width")) == "1" and str(i.get("height")) == "1") for i in imgs):
        sg.add("spacer", "MARKUP", 10, "Spacer GIFs", "Spacer GIF images used for layout", True)
    if DW_RE.search(html):
        sg.add("dreamweaver", "TECH", 18, "Dreamweaver/FrontPage code", "Dreamweaver/FrontPage-era code", True)
    gen = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    if BUILDER_RE.search((gen.get("content") if gen else "") or "") or BUILDER_RE.search(text[-400:]):
        sg.add("old_builder", "TECH", 18, "Old site-builder platform", "Built with an old site-builder (Homestead/CCH Site Builder/FrontPage...)", True)
    head = html[:600].lower()
    dm = re.search(r"<!doctype[^>]*>", head)
    if not dm:
        sg.add("no_doctype", "TECH", 8, "No doctype", "No doctype (quirks mode)")
    elif re.search(r"html 4|xhtml|html 3|html 2", dm.group(0)):
        sg.add("old_doctype", "TECH", 6, "Old doctype", "Old doctype (HTML4/XHTML)")
    jq = [(int(a), int(b)) for a, b in re.findall(r"jquery[-/.@](\d+)\.(\d+)", srcs)]
    if jq and min(jq) < (1, 12):
        sg.add("old_jquery", "TECH", 10, "Very old jQuery", f"jQuery {min(jq)[0]}.{min(jq)[1]} (very old)")
    elif jq and min(jq) < (3, 0):
        sg.add("old_jquery", "TECH", 4, "Old jQuery", f"jQuery {min(jq)[0]}.x (old)")
    if OLDLIB_RE.search(srcs):
        sg.add("old_libs", "TECH", 8, "Old JS libraries", "Old JS libraries (prototype/mootools/lightbox...)")
    if "document.write(" in low or re.search(r"<script[^>]+language\s*=", low):
        sg.add("old_js", "TECH", 5, "Old JavaScript style")
    if len(text) > 200 and not soup.find(["header", "nav", "section", "article", "footer", "main", "aside"]):
        sg.add("no_html5", "TECH", 6, "No HTML5 structure", "No HTML5 structure tags")

    # --- content clues
    yrs = [int(m.group(2) or m.group(1)) for m in COPY_RE.finditer(text)]
    yrs = [y for y in yrs if y <= THIS_YEAR + 1]
    if yrs:
        meta["copyright"] = max(yrs)
        age = THIS_YEAR - max(yrs)
        if age >= 12:
            sg.add("copyright", "CONTENT", 22, f"Old copyright ({max(yrs)})", f"Copyright year {max(yrs)} ({age} yrs old)", True)
        elif age >= 8:
            sg.add("copyright", "CONTENT", 15, f"Old copyright ({max(yrs)})", f"Copyright year {max(yrs)} ({age} yrs old)", True)
        elif age >= 5:
            sg.add("copyright", "CONTENT", 8, f"Copyright {max(yrs)}")
        elif age >= 3:
            sg.add("copyright", "CONTENT", 3, f"Copyright {max(yrs)}")
    if re.search(r"covid-?19|coronavirus", text, re.I):
        sg.add("covid", "CONTENT", 6, "COVID-era notice still on site")
    up = [int(y) for y in re.findall(r"/uploads/(20\d\d)/", low)]
    if up and THIS_YEAR - max(up) >= 8:
        sg.add("stale_uploads", "CONTENT", 10, f"Last upload {max(up)}", f"Newest image uploaded in {max(up)} (content not updated for {THIS_YEAR - max(up)} yrs)")
    if BV_RE.search(text) or BV_RE.search(html):
        sg.add("best_viewed", "CONTENT", 22, "'Best viewed in 1024x768'", "'Best viewed in...' / old screen-resolution note", True)
    if CTR_RE.search(text):
        sg.add("counter", "CONTENT", 10, "Visitor counter / guestbook", "Visitor counter / guestbook / under construction", True)
    k = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
    if k and len((k.get("content") or "").split(",")) >= 10:
        sg.add("kw_stuffing", "CONTENT", 4, "Keyword-stuffed meta")

    # --- design clues
    links = soup.find_all("a", href=True)
    imgl = [a for a in links if a.find("img") and not a.get_text(strip=True)
            and not SOCIAL_RE.search(a["href"])
            and not SOCIAL_RE.search(" ".join(i.get("src", "") + " " + i.get("alt", "") for i in a.find_all("img")))]
    roll = [i for i in imgs if i.has_attr("onmouseover") or i.has_attr("onmouseout")]
    inp = soup.find_all("input", attrs={"type": re.compile(r"^image$", re.I)})
    areas = len(soup.find_all("area"))
    clk = [i for i in imgs if i.has_attr("onclick") or (i.parent is not None and i.parent.has_attr("onclick"))]
    n_menu = max(len(imgl), len(roll), len(clk)) + len(inp)
    if n_menu >= 3 or areas >= 3:
        sg.add("img_nav", "DESIGN", 12, "Image-based menu", f"Menu/links are images ({max(n_menu, areas)} image-only links)", True)
    if re.search(r"text-align:\s*justify|align=[\"']?justify", low):
        sg.add("justify", "DESIGN", 4, "Justified old-style text")
    if re.search(r"(body|td|p)\s*[,{][^}]*font-size:\s*(9|10|11|12)px|font-size:\s*(x-small|xx-small|8pt|9pt|10pt)", css):
        sg.add("tiny_font", "DESIGN", 5, "Tiny body text")

    # --- mobile
    vp = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    vp_ok = bool(vp and "width=device-width" in (vp.get("content") or "").lower().replace(" ", ""))
    has_media = bool(re.search(r"@media[^{]*(max|min)-(device-)?width", css))
    resp_fw = bool(RESP_RE.search(low))
    fw = None if (has_media or resp_fw) else fixed_width(soup, css)
    if fw:
        sg.add("fixed_width", "LAYOUT", 15, f"Fixed-width page ({fw}px)", f"Fixed-width page box ({fw}px) - not fluid", True)
        meta["fixed_px"] = fw
    if not vp_ok:
        sg.add("no_viewport", "MOBILE", 10, "No viewport meta", "No viewport meta tag (site not built for mobile)"
               if not vp else "Viewport tag present but not device-width")
    if not (has_media or resp_fw):
        sg.add("no_media", "MOBILE", 6, "No responsive CSS", "No @media queries (no responsive CSS)")
    if vp_ok and not (has_media or resp_fw):
        mobile = not fw                 # viewport hai par fixed width => nahi; fluid => haan
    else:
        mobile = vp_ok and (has_media or resp_fw)
    if vp_ok and css_missing and not has_media:
        mobile = True                   # CSS poora nahi mila => pakka nahi, isliye lead nahi banate
        meta["uncertain"] = True
    if final_url.lower().startswith("http://") or ssl_error:
        sg.add("no_https", "SECURITY", 8, "No HTTPS (Not secure)", "Site opens on http:// (browser shows 'Not secure')")

    # --- modern negatives
    flex = bool(re.search(r"display:\s*(flex|grid)|grid-template|flex-wrap", css))
    if flex:
        sg.add("flexgrid", "MODERN", -8, "Modern CSS (flex/grid)")
    if re.search(r"srcset=|<picture|loading=[\"']?lazy", low):
        sg.add("modern_img", "MODERN", -5, "Responsive images")
    meta.update(mobile_friendly=bool(mobile or modern), modern=modern, vp_ok=vp_ok, resp_modern=bool(resp_fw or flex or modern),
                fp=dict(links=len(links), a_img=sum(1 for a in links if a.find("img")), img_links=len(imgl), roll=len(roll),
                        area=areas, tables=len(tables), lay=len(lay), share=round(share, 2), dep=dep, imgs=len(imgs),
                        text=len(text), css=len(css_texts), css_miss=css_missing, gen=(gen.get("content") if gen else "")[:30]))
    return sg, meta


DOM_IMPORT = {"table_layout", "deprecated", "body_attrs", "spacer", "dreamweaver", "old_builder", "flash", "frames",
              "copyright", "best_viewed", "counter", "justify", "img_nav", "old_libs", "old_js", "no_html5",
              "kw_stuffing", "covid", "stale_uploads", "old_jquery"}


def apply_render(sg, meta, m):
    """Browser measurement (1920px desktop + phone) ko score me jodo. Ye platform-marker se zyada bharosemand hai."""
    meta["rendered"] = True
    meta["modern"] = False
    vw, span, L, R = m.get("vw", 0), m.get("span", 0), m.get("left", 0), m.get("right", 0)
    meta["span"] = span
    if span >= 380 and vw >= 1200:
        sg[:] = [s for s in sg if s["id"] != "fixed_width"]
        pct = round(100 * span / vw)
        centered = L >= 120 and R >= 120 and abs(L - R) <= 0.2 * max(L, R) + 30
        if centered and span <= 1100:
            sg.add("boxed", "LAYOUT", 22 if m.get("fullbleed", 0) == 0 else 18, f"Boxed layout ({int(span)}px)",
                   f"Fixed-width centered box: content only {int(span)}px wide ({pct}% of a {int(vw)}px screen)", True)
        elif centered and span <= 1350 and m.get("fullbleed", 0) == 0:
            sg.add("boxed", "LAYOUT", 12, f"Boxed layout ({int(span)}px)",
                   f"Centered {int(span)}px box with no full-width sections ({pct}% of screen)", True)
        elif not centered and max(L, R) >= 150:
            sg.add("off_center", "LAYOUT", 16, f"Off-center layout ({int(span)}px)",
                   f"Fixed-width content stuck to one side ({int(span)}px wide, {int(max(L, R))}px blank on one side)", True)
    if m.get("mob_iw"):
        sg[:] = [s for s in sg if s["id"] not in ("no_viewport", "no_media")]
        if m["mob_iw"] >= 600:
            meta["mobile_friendly"] = False
            sg.add("mob_desktop", "MOBILE", 12, "Not mobile friendly", "Phone opens the desktop layout (zoomed out)")
        elif m["mob_sw"] > m["mob_iw"] + 4:
            meta["mobile_friendly"] = False
            sg.add("mob_overflow", "MOBILE", 12, "Not mobile friendly", "Page scrolls sideways on a phone")
        else:
            meta["mobile_friendly"] = True
    have = {s["id"] for s in sg}
    for s2 in m.get("dom_sigs", []):
        if s2["id"] in DOM_IMPORT and s2["id"] not in have:      # JS se bani / redirect ke baad ki asli page ke signals
            sg.append(s2)
            have.add(s2["id"])
    if m.get("font"):
        sg[:] = [s for s in sg if s["id"] != "tiny_font"]      # browser ka naap static guess se behtar hai
    if m.get("font") and m["font"] <= 13.5:
        sg.add("tiny_font_r", "DESIGN", 5, f"Tiny body text (~{round(m['font'])}px)")


STRONG = {"table_layout", "deprecated", "body_attrs", "spacer", "img_nav", "flash", "frames", "best_viewed",
          "counter", "dreamweaver", "old_builder", "old_libs"}          # site ka purana LOOK
SOFT = {"old_jquery", "old_doctype", "no_doctype", "copyright", "stale_uploads", "covid"}   # madadgaar saboot


def lead_gate(ids0, meta):
    """(browser se pakka, backup). Aapki 65 known-good leads se milaya hua rule:
       R1  phone par mobile-friendly NAHI + chhota box/ek taraf chipka (<=1200px) + kam se kam 1 purana saboot
       R2  phone par mobile-friendly NAHI + 3+ purane look ke saboot, ya table layout + 2 madadgaar saboot (full-width purani sites bhi)
       R3  phone test pass par site bahut purani dikhti hai: box + 3+ saboot, ya 4+ saboot"""
    span = meta.get("span") or 0
    strong, soft = len(ids0 & STRONG), len(ids0 & SOFT)
    m = bool(ids0 & {"mob_desktop", "mob_overflow"})
    box = bool(ids0 & {"boxed", "off_center"}) and MIN_BOX_W <= span <= MAX_BOX_W
    rend = bool(meta.get("rendered"))
    r1 = m and box and (strong + soft) >= MIN_OLD
    r2 = m and (strong >= 3 or ("table_layout" in ids0 and soft >= 2))
    r3 = (not m) and ((box and strong >= 3) or strong >= 4)
    gate = rend and (r1 or r2 or r3)
    fx = meta.get("fixed_px") or 0
    backup = (not rend and not meta.get("mobile_friendly") and "fixed_width" in ids0
              and "no_viewport" in ids0 and MIN_BOX_W <= fx <= MAX_BOX_W and strong + soft >= MIN_OLD + 1)
    return gate, backup


def reject_reason(sigs, meta, score):
    ids0 = {s["id"] for s in sigs if s.get("w", 0) > 0}
    span = int(meta.get("span") or 0)
    strong, soft = len(ids0 & STRONG), len(ids0 & SOFT)
    if meta.get("modern"):
        return "Modern site (Wix/Squarespace/JS app)"
    if not meta.get("rendered"):
        if meta.get("mobile_friendly"):
            return "Browser khuli nahi + code ke hisaab se mobile-friendly"
        return "Browser se khuli nahi (site GitHub server ko rok rahi hai) aur code-backup ke saboot kam"
    m = bool(ids0 & {"mob_desktop", "mob_overflow"})
    box = bool(ids0 & {"boxed", "off_center"}) and MIN_BOX_W <= span <= MAX_BOX_W
    if lead_gate(ids0, meta)[0]:
        return f"Rule pass par score {score} < {HOT_MIN} (kam saboot)"
    if m and not box and strong < 3:
        why = f"content {span}px (box nahi)" if span > MAX_BOX_W else "box layout nahi mila"
        return f"Mobile-friendly nahi par purane look ke saboot sirf {strong} (3 chahiye) aur {why}"
    if m and box:
        return "Box hai par purane saboot nahi mile"
    if not m:
        return f"Phone par theek khulti hai aur purane look ke saboot sirf {strong} (4 chahiye, ya box ke saath 3)"
    return f"Score {score} < {HOT_MIN}"


def finalize(sg, meta):
    score = max(0, min(100, sum(s["w"] for s in sg)))
    if meta.get("modern"):
        score = min(score, 25)
    age = [s for s in sg if s["w"] > 0 and s["fam"] not in ("MOBILE", "SECURITY")]
    anc = [s for s in age if s["anchor"]]
    mob = bool(meta.get("mobile_friendly"))
    nv = len([x for x in age if x["id"] in VISUAL])
    ids0 = {s["id"] for s in sg if s["w"] > 0}
    gate, backup = lead_gate(ids0, meta)
    if meta.get("modern"):
        tier = "NOT A LEAD"
    elif (gate or backup) and score >= HOT_MIN:
        tier = "HOT LEAD"
    else:
        tier = "NOT A LEAD"
    cls = ("STRONGLY OUTDATED" if score >= 70 else "OUTDATED" if score >= 50 else
           "SLIGHTLY DATED" if score >= 30 else "MODERN")
    ids = {s["id"] for s in sg}
    if ids & {"frames", "spacer", "dreamweaver"}:
        era = "~1998-2006 (estimated)"
    elif ids & {"flash", "table_layout", "no_doctype", "best_viewed", "counter"}:
        era = "~2004-2012 (estimated)"
    elif ids & {"fixed_width", "boxed", "old_jquery", "old_doctype", "copyright"}:
        era = "~2010-2016 (estimated)"
    else:
        era = "~2016+ (estimated)"
    top = sorted([s for s in sg if s["w"] > 0], key=lambda s: -s["w"])
    conf = round(min(0.99, 0.45 + 0.08 * len(age) + 0.05 * len(anc)), 2) if tier in ("HOT LEAD", "HIGH PRIORITY") else 0
    if tier == "HOT LEAD" and not meta.get("rendered"):
        conf = min(conf, 0.6)
    return dict(score=score, tier=tier, cls=cls, mobile=int(mob), n_anchor=len(anc), n_age=len(age), era=era,
                strong=" | ".join(s["lab"] for s in top[:6]),
                reasons=(("[BROWSER CHECK NAHI HUI - code se andaza, khud khol ke dekho] | " if (tier == "HOT LEAD" and not meta.get("rendered")) else "")
                         + f"[{len(age)} outdated signals agree] | " + " | ".join(s["txt"] for s in top)), conf=conf)

# ----------------------------------------------------------------------------- network
_tl = threading.local()
_net_lock = threading.Lock()


def session():
    if not hasattr(_tl, "s"):
        _tl.s = requests.Session()
        _tl.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return _tl.s


def online():
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)):
        try:
            socket.create_connection(host, 3).close()
            return True
        except OSError:
            continue
    return False


def wait_online():
    with _net_lock:
        shown = False
        while not online():
            if not shown:
                print("\n!! Internet band hai - wapas aane tak ruk rahe hain (kuch save nahi kho raha)...")
                shown = True
            time.sleep(10)
        if shown:
            print("== Internet wapas aa gaya, scan jaari.")


def fetch(url, depth=0):
    s = session()
    cands = [url] if re.match(r"^https?://", url, re.I) else ["https://" + url, "http://" + url]
    last = None
    for u in cands:
        for verify in (True, False):
            try:
                r = s.get(u, timeout=(6, 12), verify=verify, allow_redirects=True, stream=True)
                raw = r.raw.read(2_000_000, decode_content=True)
                r.close()
                try:
                    html = raw.decode("utf-8")
                except UnicodeDecodeError:
                    html = raw.decode("cp1252", errors="replace")
                m = re.search(r'<meta[^>]+http-equiv=["\']?refresh[^>]+url=([^"\'>\s]+)', html[:3000], re.I)
                if m and depth < 1 and len(html) < 1500:
                    return fetch(urljoin(r.url, m.group(1)), depth + 1)
                return r, html, (not verify)
            except requests.exceptions.SSLError as e:
                last = e
                continue
            except requests.RequestException as e:
                last = e
                break
    raise last


def analyze_url(url):
    r, html, ssl_err = fetch(url)
    low = html.lower()
    if r.status_code in (403, 429, 503) and re.search(r"cloudflare|just a moment|captcha|access denied", low):
        return dict(url=url, status="BLOCKED", final_url=r.url, msg=f"HTTP {r.status_code} (bot protection)")
    if r.status_code >= 400:
        return dict(url=url, status="ERROR", final_url=r.url, msg=f"HTTP {r.status_code}")
    if re.search(r"domain\s+(is\s+)?for\s+sale|buy\s+this\s+domain|parked\s+(free|domain)|account\s+suspended", low):
        return dict(url=url, status="PARKED", final_url=r.url, msg="Parked / suspended domain")
    soup0 = BeautifulSoup(html, PARSER)
    if len(soup0.get_text(" ", strip=True)) < 120 and re.search(r'id=["\'](root|app|__next)["\']', low):
        return dict(url=url, status="OK", final_url=r.url, sigs=[], meta=dict(mobile_friendly=True, modern=True),
                    msg="JS app (modern, skipped)")
    css_texts, missing = [], 0
    for ln in soup0.find_all("link", href=True)[:20]:
        rel = " ".join(ln.get("rel") or []).lower()
        if "stylesheet" not in rel or re.search(r"googleapis|gstatic|fontawesome|bootstrapcdn", ln["href"]):
            continue
        if len(css_texts) + missing >= 6:
            break
        try:
            c = session().get(urljoin(r.url, ln["href"]), timeout=8, verify=False)
            if c.status_code == 200:
                css_texts.append(c.text[:400_000])
            else:
                missing += 1
        except requests.RequestException:
            missing += 1
    sg, meta = analyze_html(html, r.url, css_texts, ssl_err, missing)
    return dict(url=url, status="OK", final_url=r.url, sigs=list(sg), meta=meta, msg="")


def scan_one(url):
    fails = 0
    while True:
        try:
            return analyze_url(url)
        except (requests.ConnectionError, requests.Timeout) as e:
            if not online():
                wait_online()
                continue
            fails += 1
            if fails >= 2 or isinstance(e, requests.Timeout):
                return dict(url=url, status="ERROR", final_url="", msg=type(e).__name__ + ": " + str(e)[:110])
            time.sleep(2)
        except Exception as e:  # noqa: BLE001
            return dict(url=url, status="ERROR", final_url="", msg=type(e).__name__ + ": " + str(e)[:110])

# ----------------------------------------------------------------------------- render (optional)
JS_MEASURE = """() => {
  const vw = document.documentElement.clientWidth;
  if (!document.body) return { vw, span: 0, left: 0, right: 0, fullbleed: 0, font: 0 }; let L = 1e9, R = 0, n = 0, fb = 0;
  for (const el of document.body.querySelectorAll('*')) {
    if (++n > 4000) break;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed' || +cs.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || r.top > 3000 || r.right <= 0 || r.left >= vw) continue;
    const media = ['IMG','SVG','CANVAS','VIDEO','IFRAME'].includes(el.tagName.toUpperCase());
    let txt = false;
    for (const x of el.childNodes) if (x.nodeType === 3 && x.textContent.trim().length > 1) { txt = true; break; }
    if (!media && !txt) continue;
    if (media && r.width >= vw * 0.98) continue;
    let a = r.left, b = r.right;
    if (txt && !media) { const g = document.createRange(); g.selectNodeContents(el); const q = g.getBoundingClientRect(); if (q.width > 0) { a = q.left; b = q.right; } }
    if (b > vw + 2 || a < -2) continue;
    L = Math.min(L, a); R = Math.max(R, b);
  }
  for (const el of document.querySelectorAll('body > *, body > * > *, body > * > * > *')) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (r.width >= vw * 0.98 && r.height >= 30 && r.top < 3000 &&
        (cs.backgroundImage !== 'none' || !/rgba\\(0, 0, 0, 0\\)|transparent/.test(cs.backgroundColor))) fb++;
  }
  const ok = R > L;
  return { vw, span: ok ? R - L : 0, left: ok ? L : 0, right: ok ? vw - R : 0, fullbleed: fb,
           font: parseFloat(getComputedStyle(document.body).fontSize) };
}"""


async def _eval(pg, js, tries=4):
    last = None
    for _ in range(tries):
        try:
            return await pg.evaluate(js)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "context was destroyed" in msg or "navigation" in msg.lower() or "Cannot read properties of null" in msg:
                last = e
                try:
                    await pg.wait_for_load_state("load", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
                await pg.wait_for_timeout(1200)
                continue
            raise
    raise last or RuntimeError("page navigation never settled")


async def _goto(pg, url):
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        await pg.goto(url, wait_until="domcontentloaded", timeout=20000)
    except PWTimeout:
        pass                                   # dheemi site: jitna load hua utne se naapo
    try:
        await pg.wait_for_load_state("load", timeout=6000)
    except Exception:  # noqa: BLE001
        pass
    await pg.wait_for_timeout(700)


async def _skip_heavy(route):
    if route.request.resource_type in ("media", "font"):
        await route.abort()
    else:
        await route.continue_()


async def measure(browser, url):
    out = {}
    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=UA, ignore_https_errors=True)
    await ctx.route("**/*", _skip_heavy)
    try:
        pg = await ctx.new_page()
        await _goto(pg, url)
        out.update(await _eval(pg, JS_MEASURE))
        try:
            out["dom_sigs"] = list(analyze_html(await pg.content(), pg.url)[0])
        except Exception:  # noqa: BLE001
            out["dom_sigs"] = []
    finally:
        await ctx.close()
    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                    device_scale_factor=2, user_agent=UA, ignore_https_errors=True)
    await ctx.route("**/*", _skip_heavy)
    try:
        pg = await ctx.new_page()
        await _goto(pg, url)
        d = await _eval(pg, "() => ({iw: window.innerWidth, sw: document.documentElement.scrollWidth})")
        out.update(mob_iw=d["iw"], mob_sw=d["sw"])
    finally:
        await ctx.close()
    return out


async def render_all(items, workers, deadline=None):
    from playwright.async_api import async_playwright
    res, done = {}, [0]
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        sem = asyncio.Semaphore(workers)

        async def one(u, target):
            async with sem:
                if deadline and time.time() > deadline:
                    return
                alt = ("https://" + target[7:]) if target.startswith("http://") else (
                    "http://" + target[8:] if target.startswith("https://") else target)
                for k, tg in enumerate((target, alt)):
                    try:
                        res[u] = await asyncio.wait_for(measure(browser, tg), 70 if k == 0 else 45)
                        break
                    except Exception as e:  # noqa: BLE001
                        if k == 1 or alt == target:
                            print("  render fail:", u, type(e).__name__, str(e).splitlines()[0][:110] if str(e) else "")
                done[0] += 1
                if done[0] % 100 == 0:
                    print(f"  ... browser check {done[0]}/{len(items)} ho gayi", flush=True)
        await asyncio.gather(*[one(u, t) for u, t in items])
        await browser.close()
    return res

# ----------------------------------------------------------------------------- storage
SCHEMA = """create table if not exists results(url text primary key, status text, final_url text, score int, tier text,
 cls text, mobile int, n_anchor int, n_age int, era text, strong text, reasons text, conf real, sigs text, meta text,
 msg text, rendered int default 0, checked_at text)"""


def db_open(path):
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    return con


def save_row(con, r):
    f = dict(score=None, tier="", cls="", mobile=None, n_anchor=None, n_age=None, era="", strong="", reasons="", conf=0)
    if r["status"] == "OK" and "sigs" in r:
        if r["meta"].get("modern") and not r["sigs"]:
            f.update(score=0, tier="NOT A LEAD", cls="MODERN", mobile=1, n_anchor=0, n_age=0)
        else:
            f.update(finalize(r["sigs"], r["meta"]))
    con.execute("insert or replace into results values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["url"], r["status"], r.get("final_url", ""), f["score"], f["tier"], f["cls"], f["mobile"],
                 f["n_anchor"], f["n_age"], f["era"], f["strong"], f["reasons"], f["conf"],
                 json.dumps(r.get("sigs", [])), json.dumps(r.get("meta", {})), r.get("msg", ""),
                 int(bool(r.get("meta", {}).get("rendered"))), dt.datetime.now().isoformat(timespec="seconds")))


def export(db, outdir=".", only=None):
    con = db_open(db)
    q = ("select url,status,final_url,score,cls,tier,mobile,era,strong,reasons,conf,msg,meta,sigs,checked_at from results "
         "order by score desc")
    cols = ["url", "outdated_score", "classification", "lead_priority", "mobile_responsive", "content_width_px",
            "estimated_design_era", "strong_signals", "top_reasons", "confidence", "status", "final_url", "checked_at"]
    allr, hot, err, hot_all, not_hot, rej = [], [], [], [], [], []
    for u, st, fu, sc, cls, tier, mob, era, strong, rea, conf, msg, meta, sigs, chk in con.execute(q).fetchall():
        mine = only is None or u in only
        if st != "OK":
            if mine:
                err.append([u, st, msg])
            continue
        if sc is not None and json.loads(sigs or "[]"):   # current thresholds se dobara tier
            f = finalize(json.loads(sigs), json.loads(meta or "{}"))
            sc, cls, tier, mob, era, strong, rea, conf = (f["score"], f["cls"], f["tier"], f["mobile"], f["era"],
                                                           f["strong"], f["reasons"], f["conf"])
        w = json.loads(meta or "{}").get("span") or json.loads(meta or "{}").get("fixed_px") or ""
        row = [u, sc, cls, tier, bool(mob), int(w) if w else "", era, strong, rea, conf, st, fu, chk]
        hot_flag = tier in ("HOT LEAD", "HIGH PRIORITY")
        if not hot_flag:
            not_hot.append([u])
            if mine:
                try:
                    rej.append([u, sc, reject_reason(json.loads(sigs or "[]"), json.loads(meta or "{}"), sc)])
                except Exception:  # noqa: BLE001
                    rej.append([u, sc, "?"])
        if hot_flag:
            hot_all.append(row)
        if mine:
            allr.append(row)
            if hot_flag:
                hot.append(row)
    for name, header, data in (("all_results.csv", cols, allr), ("hot_leads.csv", cols, hot),
                               ("errors.csv", ["url", "status", "reason"], err),
                               ("hot_leads_all_time.csv", cols, hot_all),
                               ("not_hot_all_time.csv", ["url"], not_hot),
                               ("rejected.csv", ["url", "score", "lead_kyu_nahi_bani"], rej)):
        with open(f"{outdir}/{name}", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(data)
    print(f"Saved: hot_leads.csv = is file ki {len(hot)} hot leads | all_results.csv ({len(allr)}) | errors.csv ({len(err)}) | "
          f"hot_leads_all_time.csv ({len(hot_all)}, ab tak ki saari)")

# ----------------------------------------------------------------------------- orchestration
def _rows(path):
    """CSV ya Excel (.xlsx/.xlsm) dono padhta hai."""
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


def read_urls(path, with_label=False):
    out, seen = [], set()
    rows = _rows(path)
    if not rows:
        return out
    head = [c.strip().lower() for c in rows[0]]
    idx = next((i for i, h in enumerate(head) if any(k in h for k in ("url", "website", "site", "domain", "link", "web"))), None)
    lab = head.index("label") if "label" in head else None
    body = rows[1:]
    if idx is None:                      # header nahi mila: jis column me sabse zyada URL jaise values hon wahi
        ncol = max(len(r) for r in rows)
        score = [sum(_looks_url(r[i]) for r in rows[:200] if i < len(r)) for i in range(ncol)]
        idx, body = score.index(max(score)), rows
    for r in body:
        u = (r[idx] if idx < len(r) else "").strip().strip("\"'")
        if not _looks_url(u):
            continue
        key = re.sub(r"^(https?://)?(www\.)?", "", u.lower()).rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append((u, (r[lab].strip() if lab is not None and lab < len(r) else "")) if with_label else u)
    return out


def skip_set(path, render, retry_errors):
    """Purane results.db me jo sites poori tarah ho chuki hain (read-only padho)."""
    import os
    if not path or not os.path.exists(path):
        return set()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = set()
    try:
        for url, st, rendered, sigs in con.execute("select url,status,rendered,sigs from results"):
            if st == "ERROR":
                if not retry_errors:
                    out.add(url)
            elif not render or rendered == 1 or st != "OK" or (sigs or "[]") == "[]":
                out.add(url)
    finally:
        con.close()
    return out


def run_scan(urls, db, workers=20, retry_errors=False, render=False, deadline=None, skip_from=None):
    con = db_open(db)
    done = dict(con.execute("select url,status from results"))
    todo = [u for u in urls if u not in done or (retry_errors and done[u] == "ERROR")]
    if skip_from:
        sk = skip_set(skip_from, render, retry_errors)
        todo = [u for u in todo if u not in sk]
    print(f"Total {len(urls)} | pehle se ho chuki {len(urls) - len(todo)} | ab check hongi {len(todo)}")
    n = 0
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        def job(u):
            return None if deadline and time.time() > deadline else scan_one(u)
        futs = [ex.submit(job, u) for u in todo]
        for f in as_completed(futs):
            r = f.result()
            if r is None:
                continue
            save_row(con, r)
            n += 1
            if n % 25 == 0:
                con.commit()
            tier = ""
            if r["status"] == "OK" and "sigs" in r:
                tier = " -> " + (finalize(r["sigs"], r["meta"])["tier"] if r["sigs"] else "NOT A LEAD")
            print(f"[{n}/{len(todo)}] {r['url']} {r['status']}{tier} {r.get('msg', '')}")
    except KeyboardInterrupt:
        print("\nRoka gaya - ab tak ka result save hai. Dobara chalao to wahin se shuru hoga.")
        ex.shutdown(wait=False, cancel_futures=True)
    finally:
        con.commit()
    if render:
        run_render(db, urls, workers=min(workers, 8), deadline=deadline)


def render_candidates(con, urls):
    """Har asli site ko browser se check karo (sirf JS-shell / khali pages chhodo). Static guess par bharosa nahi."""
    want, out = set(urls), []
    for url, fu, sigs, meta in con.execute(
            "select url,final_url,sigs,meta from results where status='OK' and rendered=0"):
        if url in want and json.loads(sigs or "[]"):
            out.append((url, fu, sigs, meta))
    return out


def run_render(db, urls, workers=6, deadline=None):
    con = db_open(db)
    rows = render_candidates(con, urls)
    print(f"Browser se measure hogi: {len(rows)} candidate sites")
    if not rows:
        return
    res = asyncio.run(render_all([(r[0], r[1] or r[0]) for r in rows], workers, deadline))
    for url, fu, sigs, meta in rows:
        if url not in res:
            continue
        sg = S()
        sg.extend(json.loads(sigs))
        mt = json.loads(meta)
        apply_render(sg, mt, res[url])
        f = finalize(sg, mt)
        con.execute("update results set score=?,tier=?,cls=?,mobile=?,n_anchor=?,n_age=?,era=?,strong=?,reasons=?,"
                    "conf=?,sigs=?,meta=?,rendered=1 where url=?",
                    (f["score"], f["tier"], f["cls"], f["mobile"], f["n_anchor"], f["n_age"], f["era"], f["strong"],
                     f["reasons"], f["conf"], json.dumps(list(sg)), json.dumps(mt), url))
    con.commit()


def calibrate(path, db, workers, render):
    labelled = [(u, l) for u, l in read_urls(path, True) if l in ("0", "1")]
    if not labelled:
        sys.exit("labels file me 'label' column me 1 (outdated lead) ya 0 (nahi) bharo.")
    run_scan([u for u, _ in labelled], db, workers, False, render)
    con = db_open(db)
    data = {u: (sc, mob, na, nag) for u, sc, mob, na, nag in
            con.execute("select url,score,mobile,n_anchor,n_age from results where status='OK'")}
    pairs = [(data[u], l == "1") for u, l in labelled if u in data]
    print(f"\n{len(pairs)} labelled sites mili. Threshold vs accuracy:")
    print("thr | precision | recall | flagged")
    for thr in range(25, 85, 5):
        tp = fp = fn = 0
        for (sc, mob, na, nag), y in pairs:
            pred = (not mob) and sc >= thr and na >= 1 and nag >= 3
            tp += pred and y
            fp += pred and not y
            fn += (not pred) and y
        p = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        print(f"{thr:3d} | {p:8.1%} | {rc:6.1%} | {tp + fp}")
    print("Tip: sabse chhota threshold chuno jahan precision 99% ho; HOT_MIN me wahi daalo.")


def merge(db, shards):
    """Alag-alag shard databases ko main results.db me jodo."""
    con = db_open(db)
    n = 0
    for sp in shards:
        con.execute("attach database ? as s", (sp,))
        n += con.execute("insert or replace into results select * from s.results").rowcount
        con.commit()
        con.execute("detach database s")
    print(f"merge: {n} rows {len(shards)} shard se jode gaye -> {db}")


def recall(path, db, workers, render):
    """Jaani-pehchani achhi leads (label 1) me se kitni checker ne pakdi - sabse seedha accuracy test."""
    urls = read_urls(path)
    run_scan(urls, db, workers, False, render)
    con = db_open(db)
    rows = {r[0]: r for r in con.execute("select url,status,tier,score,mobile,era,strong,rendered,meta,sigs from results")}
    msgs = dict(con.execute("select url,msg from results"))
    hit, miss, err = [], [], []
    for u in urls:
        r = rows.get(u)
        if not r or r[1] != "OK":
            err.append((u, r[1] if r else "scan nahi hui"))
        elif r[2] in ("HOT LEAD", "HIGH PRIORITY"):
            hit.append(r)
        else:
            miss.append(r)
    tot = len(hit) + len(miss)
    print(f"\nRECALL: {len(hit)}/{tot} = {len(hit) / tot:.0%} jaani-pehchani leads pakdi gayi" if tot else "\nKoi site check nahi hui.")
    print(f"Khuli hi nahi / error: {len(err)}")
    for u, why in err:
        print(f"  x {u} ({why}: {msgs.get(u, '')})")
    if miss:
        print("\nChhoot gayi sites (score | mobile-friendly? | tier | signals):")
        for r in sorted(miss, key=lambda r: -(r[3] or 0)):
            mt = json.loads(r[8] or "{}")
            raw = sum(x["w"] for x in json.loads(r[9] or "[]"))
            print(f" - {r[0]} | score {r[3]} (raw {raw}) | mobile {'Yes' if r[4] else 'No'} | browser-check {'haan' if r[7] else 'NAHI'}"
                  f" | width {mt.get('span', '-')} | {r[6]}")
            print(f"     fp: {mt.get('fp')}")


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "export", "calibrate", "recall", "merge"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--db", default="results.db")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--shard", help="i/N: list ka sirf i-wa hissa (N hisson me) check karo")
    ap.add_argument("--skip-from", help="purani results.db jisme ho chuki sites dobara na lo")
    ap.add_argument("--only", help="export: sirf is file ki sites ka result")
    ap.add_argument("--min-visual", type=int, help="(purana option, ab use nahi hota)")
    ap.add_argument("--max-width", type=int, help="box ki max chaudai px me (default 1200)")
    ap.add_argument("--min-old", type=int, help="box ke alawa kam se kam itne purane saboot (default 1)")
    ap.add_argument("--hot-min", type=int, help="HOT LEAD ke liye minimum score (default 50)")
    ap.add_argument("--max-minutes", type=float, help="itne minute baad nayi sites lena band (resume baad me)")
    a = ap.parse_intermixed_args()
    a.file = a.files[0] if a.files else None
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    print("outdated_checker", VERSION)
    if a.hot_min:
        globals()["HOT_MIN"] = a.hot_min
    if a.min_visual:
        globals()["MIN_VISUAL"] = a.min_visual
    if a.max_width:
        globals()["MAX_BOX_W"] = a.max_width
    if a.min_old is not None:
        globals()["MIN_OLD"] = a.min_old
    deadline = time.time() + a.max_minutes * 60 if a.max_minutes else None
    if a.cmd == "merge":
        return merge(a.db, a.files)
    if a.cmd == "export":
        return export(a.db, only=set(read_urls(a.only)) if a.only else None)
    if not a.file:
        sys.exit("CSV file ka naam do.")
    if a.cmd == "scan":
        urls = read_urls(a.file)
        if a.shard:
            i, n = (int(x) for x in a.shard.split("/"))
            urls = urls[i::n]
            print(f"Shard {i + 1}/{n}: is computer ke hisse me {len(urls)} sites")
        run_scan(urls, a.db, a.workers, a.retry_errors, a.render, deadline, a.skip_from)
        if not a.shard:
            export(a.db, only=set(urls))
    elif a.cmd == "recall":
        recall(a.file, a.db, a.workers, a.render)
    else:
        calibrate(a.file, a.db, a.workers, a.render)


if __name__ == "__main__":
    main()
