"""
website_analyzer.py
====================
Ek single website ka poora analysis:
  1. Screenshot + layout measurement (screenshot_capture)
  2. HTML signals (technology_detector)
  3. (Optional) AI vision (ai_vision)
  4. Final score (scorer)
  5. Retry + http/https fallback (FAILED sites kam karne ke liye)
"""

import asyncio

import config
import screenshot_capture
import technology_detector
import ai_vision
import scorer


def _candidate_urls(url: str) -> list:
    """url + uska alternate scheme (https<->http). Kai purani sites sirf
    ek hi scheme par chalti hain."""
    alt = url.replace("https://", "http://", 1) if url.startswith("https://") \
        else url.replace("http://", "https://", 1)
    return [url, alt] if alt != url else [url]


async def analyze_website(browser, url: str, screenshot_dir: str) -> dict:
    last_error = None
    first_error = None      # asli wajah: pehli koshish ki (http/https fallback ki nahi)
    candidates = _candidate_urls(url)

    for attempt in range(1, config.RETRY_COUNT + 2):
        for candidate in candidates:
            capture = await screenshot_capture.capture_site(browser, candidate, screenshot_dir)

            if capture["status"] == "SUCCESS":
                return await _score_capture(url, capture)
            if capture["status"] == "PARTIAL" and capture.get("html"):
                return await _score_capture(url, capture, partial=True)
            if capture["status"] == "BLOCKED":
                last_error = capture.get("error")
                blocked = True
                continue
            last_error = capture.get("error")
            if first_error is None:
                first_error = last_error

        if attempt <= config.RETRY_COUNT:
            await asyncio.sleep(config.RETRY_DELAY_SECONDS)

    status = "BLOCKED" if (last_error and ("403" in str(last_error) or "429" in str(last_error)
                                          or "401" in str(last_error))) else "FAILED"
    return _failure(url, status, first_error or last_error)


def _layout_note(layout: dict, final_url: str) -> str:
    if not layout:
        return ""
    vw = layout.get("vw")
    return (
        f"screen width {vw}px; content column {layout.get('content_width_px')}px wide "
        f"({round(layout.get('content_ratio', 1) * 100)}% of the screen); "
        f"empty space left {layout.get('left_margin_px')}px / right {layout.get('right_margin_px')}px; "
        f"full-width coloured sections: {layout.get('full_bleed_bands')}; "
        f"median body text size {layout.get('median_font_px')}px; "
        f"justified text share {round(layout.get('justified_ratio', 0) * 100)}%; "
        f"image-only menu links: {layout.get('image_only_links')}; "
        f"opens on {'http (Not secure)' if final_url.startswith('http://') else 'https'}."
    )


async def _score_capture(url: str, capture: dict, partial: bool = False) -> dict:
    tech = technology_detector.detect_technology_signals(capture["html"])
    layout = capture.get("layout_metrics")
    final_url = capture.get("final_url", url)

    ai_result = None
    if config.USE_AI_VISION and capture.get("desktop_screenshot_path") and capture.get("mobile_screenshot_path"):
        ai_result = ai_vision.analyze_screenshots(
            capture["desktop_screenshot_path"],
            capture["mobile_screenshot_path"],
            _layout_note(layout, final_url),
        )

    score_data = scorer.compute_final_score(
        tech=tech,
        ai_result=ai_result,
        overflow_ratio=capture.get("mobile_overflow_ratio"),
        load_time_ms=capture.get("load_time_ms"),
        resource_count=capture.get("resource_count"),
        total_bytes=capture.get("total_bytes"),
        had_screenshots=bool(capture.get("desktop_screenshot_path")) or config.SKIP_SCREENSHOTS,
        layout=layout,
        final_url=final_url,
    )

    result = {
        "url": url,
        "final_url": final_url,
        "status": "PARTIAL" if partial else "SUCCESS",
        "desktop_screenshot": capture.get("desktop_screenshot_path"),
        "mobile_screenshot": capture.get("mobile_screenshot_path"),
    }
    result.update(score_data)
    return result


def _failure(url: str, status: str, error) -> dict:
    return {
        "url": url, "final_url": url, "status": status,
        "error": str(error) if error else "Unknown error",
        "outdated_score": None, "classification": None, "lead_priority": None,
        "confidence": 0.0, "top_reasons": [],
    }
