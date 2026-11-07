"""
screenshot_capture.py
======================
Playwright use karke:
  - Desktop screenshot (1920x1080, upar ka hissa = jo visitor pehli nazar
    mein dekhta hai)
  - LAYOUT MEASUREMENT: page render hone ke baad browser se naapna ki
    content kitna chauda hai, dono taraf kitni khali jagah hai, text
    ka size kitna hai, menu image-based hai ya nahi, waghera.
    (Ye CSS file se nahi, ASLI rendered page se naapta hai -- isliye
    external CSS mein likhe fixed widths bhi pakad leta hai.)
  - Mobile screenshot (390px) + overflow check
  - Basic performance metrics
"""

import os
import re
import time
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import config


LAYOUT_JS = r"""
() => {
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const all = Array.from(document.body ? document.body.querySelectorAll('*') : []);
  const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','META','LINK','HEAD','TITLE','BR','HR','OPTION']);
  const media = new Set(['IMG','VIDEO','CANVAS','IFRAME','EMBED','OBJECT','SVG']);
  const isTransparent = c => !c || c === 'transparent' || c === 'rgba(0, 0, 0, 0)';
  const canvasBg = document.body ? getComputedStyle(document.body).backgroundColor : '';

  let minL = Infinity, maxR = 0, counted = 0;
  const fontSamples = [];
  let totalText = 0, justText = 0;
  let imgOnlyLinks = 0, fullBleed = 0, bordered = 0, tableCount = 0;

  for (const el of all) {
    const tag = el.tagName.toUpperCase();
    if (skip.has(tag)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;

    if (tag === 'TABLE') tableCount++;

    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
    own = own.replace(/\s+/g, ' ').trim();
    const isMedia = media.has(tag);

    if ((own.length > 0 || isMedia) && cs.position !== 'fixed') {
      if (r.right > 0 && r.left < vw + 1) {
        minL = Math.min(minL, Math.max(0, r.left));
        maxR = Math.max(maxR, Math.min(vw, r.right));
        counted++;
      }
    }
    if (own.length > 0) {
      const fs = parseFloat(cs.fontSize);
      if (fs) {
        fontSamples.push([fs, own.length]);
        totalText += own.length;
        if (cs.textAlign === 'justify') justText += own.length;
      }
    }
    if (tag === 'A') {
      const hasImg = el.querySelector('img') !== null;
      const txt = (el.textContent || '').replace(/\s+/g, '').length;
      const href = el.getAttribute('href') || '';
      // gallery (badi photo ka link) aur social icons "menu" nahi hote
      const notMenu = /\.(jpe?g|png|gif|webp|bmp|svg)(\?|#|$)/i.test(href) ||
                      /(facebook|twitter|linkedin|instagram|youtube|pinterest|yelp|bbb\.org|aicpa|google|\bx\.com)/i.test(href);
      if (hasImg && txt === 0 && !notMenu) imgOnlyLinks++;
    }
    if (r.width >= vw * 0.95 && r.height >= 30 && cs.position !== 'fixed') {
      const hasBg = !isTransparent(cs.backgroundColor) && cs.backgroundColor !== canvasBg;
      const hasBgImg = cs.backgroundImage && cs.backgroundImage !== 'none';
      if (hasBg || hasBgImg) fullBleed++;
    }
    if (r.width >= vw * 0.4 && r.width <= vw * 0.95 && r.height >= 200) {
      const bw = parseFloat(cs.borderLeftWidth) || 0;
      if (bw >= 1 && cs.borderLeftStyle !== 'none') bordered++;
    }
  }

  fontSamples.sort((a, b) => a[0] - b[0]);
  let acc = 0, median = null;
  for (const [fs, len] of fontSamples) {
    acc += len;
    if (acc >= totalText / 2) { median = fs; break; }
  }

  let webfonts = 0;
  try { webfonts = Array.from(document.fonts).filter(f => f.status === 'loaded').length; } catch (e) {}

  return {
    vw: vw,
    content_left: counted ? minL : null,
    content_right: counted ? maxR : null,
    counted_elements: counted,
    median_font_px: median,
    justified_ratio: totalText ? justText / totalText : 0,
    image_only_links: imgOnlyLinks,
    full_bleed_bands: fullBleed,
    bordered_containers: bordered,
    layout_table_count: tableCount,
    webfont_count: webfonts,
    body_font_family: document.body ? getComputedStyle(document.body).fontFamily : '',
    page_height: document.documentElement.scrollHeight
  };
}
"""


def _safe_filename(url: str) -> str:
    name = re.sub(r"https?://", "", url)
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    return name[:120]


async def _goto(page, url: str):
    """
    Robust page load: networkidle par aksar purani/slow sites atak jaati
    hain (aur FAILED dikhti hain). Isliye pehle DOM load, phir thoda
    wait -- kaam ki cheez render ho jati hai.
    """
    response = await page.goto(url, timeout=config.NAVIGATION_TIMEOUT_MS,
                               wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("load", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(1500)
    return response


async def _measure_layout(page) -> dict:
    try:
        raw = await page.evaluate(LAYOUT_JS)
    except Exception:
        return None
    if not raw or raw.get("content_left") is None:
        return None
    vw = raw["vw"]
    left = raw["content_left"]
    right_edge = raw["content_right"]
    raw["left_margin_px"] = round(left)
    raw["right_margin_px"] = round(vw - right_edge)
    raw["content_width_px"] = round(right_edge - left)
    raw["content_ratio"] = round((right_edge - left) / vw, 3) if vw else 1.0
    return raw


async def capture_site(browser, url: str, screenshot_dir: str) -> dict:
    result = {
        "status": "FAILED",
        "final_url": url,
        "html": "",
        "desktop_screenshot_path": None,
        "mobile_screenshot_path": None,
        "mobile_overflow_ratio": None,
        "layout_metrics": None,
        "load_time_ms": None,
        "resource_count": 0,
        "total_bytes": 0,
        "error": None,
    }

    if not config.SKIP_SCREENSHOTS:
        os.makedirs(screenshot_dir, exist_ok=True)
    base_name = _safe_filename(url)

    context = None
    try:
        # ---------- DESKTOP PASS ----------
        context = await browser.new_context(
            viewport=config.DESKTOP_VIEWPORT,
            user_agent=config.USER_AGENT_DESKTOP,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        resource_count = 0
        total_bytes = 0

        def _on_response(response):
            nonlocal resource_count, total_bytes
            resource_count += 1
            try:
                length = response.headers.get("content-length")
                if length:
                    total_bytes += int(length)
            except Exception:
                pass

        page.on("response", _on_response)

        start_time = time.time()
        response = await _goto(page, url)
        load_time_ms = int((time.time() - start_time) * 1000)

        if response is None:
            result["error"] = "No response from server"
            await context.close()
            context = None
            return result

        if response.status in (401, 403, 429):
            result["status"] = "BLOCKED"
            result["error"] = f"HTTP {response.status}"
            await context.close()
            context = None
            return result

        if response.status >= 400:                      # 404/500 wali band site = analysis nahi
            result["status"] = "FAILED"
            result["error"] = f"HTTP {response.status}"
            await context.close()
            context = None
            return result

        result["final_url"] = page.url
        result["load_time_ms"] = load_time_ms
        result["resource_count"] = resource_count
        result["total_bytes"] = total_bytes
        result["html"] = await page.content()
        result["layout_metrics"] = await _measure_layout(page)

        if not config.SKIP_SCREENSHOTS:
            desktop_path = os.path.join(screenshot_dir, f"{base_name}_desktop.png")
            # Above-the-fold (1920x1080) -- exactly wahi jo visitor pehli nazar mein dekhta hai
            await page.screenshot(path=desktop_path, full_page=False, timeout=15000)
            result["desktop_screenshot_path"] = desktop_path

        await context.close()
        context = None

        # ---------- MOBILE PASS ----------
        context = await browser.new_context(
            viewport=config.MOBILE_VIEWPORT,
            user_agent=config.USER_AGENT_MOBILE,
            ignore_https_errors=True,
            is_mobile=True,
            has_touch=True,
        )
        page = await context.new_page()
        await _goto(page, url)

        try:
            scroll_width = await page.evaluate("document.documentElement.scrollWidth")
            viewport_width = config.MOBILE_VIEWPORT["width"]
            overflow_ratio = scroll_width / viewport_width if viewport_width else 1.0
        except Exception:
            overflow_ratio = None
        result["mobile_overflow_ratio"] = overflow_ratio

        if not config.SKIP_SCREENSHOTS:
            mobile_path = os.path.join(screenshot_dir, f"{base_name}_mobile.png")
            await page.screenshot(path=mobile_path, full_page=False, timeout=15000)
            result["mobile_screenshot_path"] = mobile_path

        await context.close()
        context = None

        result["status"] = "SUCCESS"
        return result

    except PlaywrightTimeoutError as e:
        result["status"] = "PARTIAL" if result["html"] else "FAILED"
        result["error"] = f"Timeout: {e}"
        return result
    except Exception as e:
        err_text = str(e).lower()
        if "403" in err_text or "err_blocked" in err_text:
            result["status"] = "BLOCKED"
        else:
            result["status"] = "PARTIAL" if result["html"] else "FAILED"
        result["error"] = str(e)
        return result
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
