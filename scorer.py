"""
scorer.py  (v3)
================
Final OUTDATED_SCORE (0-100) banata hai.

CORE IDEA
---------
1. MEASURED LAYOUT (browser mein naap kar): content kitna chauda hai,
   dono taraf kitni khali jagah hai, text kitna chhota hai, menu
   image-based hai ya nahi. Ye HTML guess nahi, asli rendered page ka
   measurement hai.
2. AI VISION (agar API key set hai): screenshots dekh kar visual judgment.
3. STRONG-SIGNAL AGREEMENT: jab kai INDEPENDENT purane-design signals
   ek saath milte hain (boxed + purana copyright + "best viewed in" +
   http + image-menu ...) to score ek minimum level se neeche nahi
   jaata. Ek akela signal kabhi site ko outdated nahi banata.
"""

import datetime
import config


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


# ----------------------------------------------------------------------
# MEASURED BOX-STYLE (0-10) -- browser mein naape gaye layout se
# ----------------------------------------------------------------------
def measure_box_style(layout: dict, tech: dict):
    """Returns (score 0-10, reasons list, centered/boxed flag)."""
    if not layout:
        return None, [], False

    vw = layout.get("vw") or 1
    ratio = layout.get("content_ratio", 1.0)
    left = layout.get("left_margin_px", 0)
    right = layout.get("right_margin_px", 0)
    lm, rm = left / vw, right / vw
    width = layout.get("content_width_px", 0)

    score = 0
    reasons = []

    if ratio <= config.BOX_MAX_CONTENT_RATIO:
        bands = layout.get("full_bleed_bands", 0)
        centered = abs(left - right) <= 0.04 * vw
        if centered and lm >= config.BOX_MIN_SIDE_MARGIN_RATIO:
            score = 6
            reasons.append(
                f"Fixed-width centered box: content only {width}px wide "
                f"({round(ratio*100)}% of a {vw}px screen), ~{left}px empty on each side")
        elif rm >= 0.15 and lm < config.BOX_MIN_SIDE_MARGIN_RATIO:
            score = 5
            reasons.append(
                f"Fixed-width layout stuck to the left ({width}px wide), "
                f"{right}px blank on the right")
        elif lm >= config.BOX_MIN_SIDE_MARGIN_RATIO or rm >= config.BOX_MIN_SIDE_MARGIN_RATIO:
            score = 3
            reasons.append(f"Off-center fixed-width content ({width}px wide)")

        if score > 0:
            if bands >= 2:
                # Full-width coloured/image sections = modern "container inside
                # full-bleed sections" pattern, NOT an old boxed page.
                score = min(score, 2)
                reasons = [f"Centered {width}px container, but page has {bands} full-width sections (modern pattern)"]
            elif bands == 1:
                score = min(score, 4)
            else:
                score += 2
                reasons.append("No full-width sections (everything sits inside one box)")

            if score >= 4 and layout.get("bordered_containers", 0) >= 1:
                score += 1
                reasons.append("Thin border drawn around the main content box")
            if score >= 4 and (tech.get("big_table_count", 0) >= 1 or layout.get("layout_table_count", 0) >= 2):
                score += 1

    return _clip(score, 0, config.BOX_STYLE_MAX), reasons, score >= config.BOX_STYLE_TRUE_THRESHOLD


# ----------------------------------------------------------------------
# VISUAL CATEGORIES (A=30, F=15, G=5) + BOX_STYLE
# ----------------------------------------------------------------------
def score_visual_categories(tech, ai_result, layout, final_url):
    measured_box, box_reasons, _ = measure_box_style(layout, tech)

    # ------------- AI VISION available -------------
    if ai_result:
        visual_ui = _clip(ai_result.get("visual_ui_score", 15), 0, config.MAX_VISUAL_UI)
        modern_design = _clip(ai_result.get("modern_design_quality_score", 7), 0, config.MAX_MODERN_DESIGN)
        human = _clip(ai_result.get("overall_design_judgment_score", 2), 0, config.MAX_HUMAN_JUDGMENT)
        ai_box = _clip(ai_result.get("box_style_score", 3), 0, config.BOX_STYLE_MAX)

        if measured_box is None:
            box_score = ai_box
        elif ai_box >= 7:
            box_score = max(ai_box, measured_box)
        else:
            # Naapa hua layout (fact) + AI ki raaye (judgment) ka mix
            box_score = round(0.6 * ai_box + 0.4 * measured_box)
            if measured_box >= 8:
                box_score = max(box_score, 7)

        era = ai_result.get("estimated_design_era", "Unknown")
        reasons = list(ai_result.get("top_reasons", [])) + box_reasons
        return visual_ui, modern_design, human, _clip(box_score, 0, config.BOX_STYLE_MAX), era, reasons

    # ------------- HEURISTIC FALLBACK (no AI vision) -------------
    penalty = 0
    reasons = list(box_reasons)

    if measured_box is not None:
        if measured_box >= config.BOX_STYLE_TRUE_THRESHOLD:
            penalty += 8
        elif measured_box >= 3:
            penalty += 4

    if layout:
        med = layout.get("median_font_px")
        if med and med <= config.TINY_FONT_PX:
            penalty += 5
            reasons.append(f"Very small body text (~{med:.0f}px)")
        elif med and med <= 14:
            penalty += 2
        if layout.get("justified_ratio", 0) >= 0.3:
            penalty += 4
            reasons.append("Justified (stretched) body text, old-style typography")
        if layout.get("image_only_links", 0) >= 3:
            penalty += 5
            reasons.append(f"Menu/links are images ({layout['image_only_links']} image-only links)")

    if tech.get("best_viewed_text"):
        penalty += 6
        reasons.append('"Best viewed in ... resolution/browser" note on the page')
    if tech.get("flash_remnants") or tech.get("plugin_text"):
        penalty += 6
        reasons.append("Flash / plugin content (no longer supported by browsers)")
    if tech.get("deprecated_tag_count", 0) > 0:
        penalty += 4
        reasons.append("Deprecated HTML tags (font/center/marquee)")
    if tech.get("big_table_count", 0) >= 1:
        penalty += 3
        reasons.append("Table-based page layout")
    if tech.get("old_builder_hint"):
        penalty += 2
        reasons.append("Old web-agency/builder signature found")
    if tech.get("old_bootstrap_hint"):
        penalty += 3
    if final_url.startswith("http://"):
        penalty += 3
        reasons.append("Site opens on http:// (browser shows 'Not secure')")

    penalty = _clip(penalty, 0, 30)
    visual_ui = _clip(round(penalty), 0, config.MAX_VISUAL_UI)
    modern_design = _clip(round(penalty * 0.5), 0, config.MAX_MODERN_DESIGN)
    human = _clip(round(penalty / 6), 0, config.MAX_HUMAN_JUDGMENT)

    box_score = measured_box if measured_box is not None else 0
    if measured_box is None and tech.get("has_fixed_width"):
        box_score = 5

    if penalty >= 20:
        era = "~2004-2012 (estimated)"
    elif penalty >= 12:
        era = "~2010-2016 (estimated)"
    elif penalty > 0:
        era = "~2014-2020 (estimated)"
    else:
        era = "~2020-2026 (estimated)"

    if not reasons:
        reasons = ["No strong outdated visual signals found (heuristic check only)"]
    return visual_ui, modern_design, human, _clip(box_score, 0, config.BOX_STYLE_MAX), era, reasons


# ----------------------------------------------------------------------
# B) MOBILE (15)
# ----------------------------------------------------------------------
def score_mobile(tech, overflow_ratio, ai_result=None):
    penalty = 0
    reasons = []

    if not tech.get("viewport_present"):
        penalty += 7
        reasons.append("No viewport meta tag (site not built for mobile)")
    elif not tech.get("viewport_device_width"):
        penalty += 4
        reasons.append("Viewport tag present but not device-width based")

    if overflow_ratio is not None:
        if overflow_ratio >= 1.5:
            penalty += 8
            reasons.append(f"Page does not reflow on mobile (content {overflow_ratio:.1f}x wider than screen)")
        elif overflow_ratio >= 1.15:
            penalty += 4
            reasons.append(f"Horizontal overflow on mobile ({overflow_ratio:.1f}x screen width)")

    if not tech.get("media_query_present"):
        penalty += 2
        reasons.append("No @media queries in inline styles (partial signal)")

    if ai_result and ai_result.get("mobile_visually_broken") and penalty < 8:
        penalty += 6
        reasons.append("AI vision: mobile view looks like a squeezed desktop layout")

    penalty = _clip(penalty, 0, config.MAX_MOBILE)
    mobile_responsive = penalty < 6
    if not reasons:
        reasons = ["Mobile layout looks fine"]
    return penalty, mobile_responsive, reasons


# ----------------------------------------------------------------------
# C) CONTENT / UX (15)
# ----------------------------------------------------------------------
def score_content_ux(tech, final_url):
    penalty = 0
    reasons = []
    year = tech.get("copyright_year")
    if year:
        age = datetime.datetime.now().year - year
        if age >= config.OLD_COPYRIGHT_AGE_YEARS:
            penalty += 5
            reasons.append(f"Copyright year is {year} ({age} years old)")
        elif age >= 4:
            penalty += 2
            reasons.append(f"Copyright year is {year}")
    if tech.get("best_viewed_text"):
        penalty += 3
    if tech.get("fax_mentioned"):
        penalty += 2
        reasons.append("Fax number displayed")
    if tech.get("designed_by_credit"):
        penalty += 1
    if not tech.get("has_cta"):
        penalty += 3
        reasons.append("No clear call-to-action / enquiry flow")
    if tech.get("word_count", 0) > 1500:
        penalty += 2
        reasons.append("Excessive unstructured text on homepage")
    if final_url.startswith("http://"):
        penalty += 2
    penalty = _clip(penalty, 0, config.MAX_CONTENT_UX)
    if not reasons:
        reasons = ["Content presentation looks reasonably fresh"]
    return penalty, reasons


# ----------------------------------------------------------------------
# D) TECHNOLOGY (10)
# ----------------------------------------------------------------------
def score_technology(tech):
    penalty = 0
    reasons = []
    if tech.get("table_count", 0) >= 2:
        penalty += 3
        reasons.append("Multiple layout tables")
    if tech.get("inline_style_count", 0) > 30:
        penalty += 2
        reasons.append("Excessive inline CSS")
    if tech.get("deprecated_tag_count", 0) > 0:
        penalty += 2
    if tech.get("flash_remnants"):
        penalty += 3
        reasons.append("Flash / .swf remnants")
    if tech.get("old_bootstrap_hint"):
        penalty += 2
    if not tech.get("media_query_present") and not tech.get("viewport_present"):
        penalty += 2
    penalty = _clip(penalty, 0, config.MAX_TECHNOLOGY)
    if not reasons:
        reasons = ["No significantly outdated technology markers"]
    return penalty, reasons


# ----------------------------------------------------------------------
# E) PERFORMANCE (10)
# ----------------------------------------------------------------------
def score_performance(load_time_ms, resource_count, total_bytes):
    penalty = 0
    reasons = []
    if load_time_ms is not None:
        if load_time_ms > 8000:
            penalty += 5
            reasons.append(f"Slow page load ({load_time_ms/1000:.1f}s)")
        elif load_time_ms > 4000:
            penalty += 2
    if resource_count and resource_count > 150:
        penalty += 2
    if total_bytes and total_bytes > 8_000_000:
        penalty += 3
        reasons.append(f"Large page (~{total_bytes/1_000_000:.1f} MB)")
    penalty = _clip(penalty, 0, config.MAX_PERFORMANCE)
    if not reasons:
        reasons = ["Performance looks acceptable"]
    return penalty, reasons


# ----------------------------------------------------------------------
# STRONG-SIGNAL AGREEMENT
# ----------------------------------------------------------------------
def collect_strong_signals(tech, layout, box_score, mobile_score, final_url):
    """
    Alag-alag (independent) strong signals ki list. Har signal ya to
    maujood hai ya nahi -- koi andaza nahi.
    """
    signals = []
    if box_score >= config.BOX_STYLE_TRUE_THRESHOLD:
        signals.append("Boxed fixed-width layout")
    if mobile_score >= 10:
        signals.append("Not mobile friendly")
    year = tech.get("copyright_year")
    if year and (datetime.datetime.now().year - year) >= config.OLD_COPYRIGHT_AGE_YEARS:
        signals.append(f"Old copyright ({year})")
    if tech.get("best_viewed_text"):
        signals.append("'Best viewed in' resolution/browser note")
    if tech.get("flash_remnants") or tech.get("plugin_text"):
        signals.append("Flash/plugin content")
    if layout:
        if layout.get("image_only_links", 0) >= 3:
            signals.append("Image-based menu")
        med = layout.get("median_font_px")
        if med and med <= config.TINY_FONT_PX:
            signals.append("Tiny body text")
        if layout.get("justified_ratio", 0) >= 0.3:
            signals.append("Justified old-style text")
    if final_url.startswith("http://"):
        signals.append("No HTTPS (Not secure)")
    if tech.get("big_table_count", 0) >= 1 or tech.get("deprecated_tag_count", 0) > 0:
        signals.append("Table/deprecated-tag markup")
    return signals


# ----------------------------------------------------------------------
# FINAL
# ----------------------------------------------------------------------
def compute_final_score(tech, ai_result, overflow_ratio, load_time_ms,
                        resource_count, total_bytes, had_screenshots,
                        layout=None, final_url=""):

    visual_ui, modern_design, human, box_score, era, visual_reasons = \
        score_visual_categories(tech, ai_result, layout, final_url)
    mobile_score, mobile_responsive, mobile_reasons = score_mobile(tech, overflow_ratio, ai_result)
    content_score, content_reasons = score_content_ux(tech, final_url)
    tech_score, tech_reasons = score_technology(tech)
    perf_score, perf_reasons = score_performance(load_time_ms, resource_count, total_bytes)

    outdated_score = (visual_ui + mobile_score + content_score + tech_score +
                      perf_score + modern_design + human)
    outdated_score = int(round(_clip(outdated_score, 0, config.MAX_TOTAL)))

    box_style = box_score >= config.BOX_STYLE_TRUE_THRESHOLD

    # ---- Agreement floors ----
    strong = collect_strong_signals(tech, layout, box_score, mobile_score, final_url)
    n = len(strong)
    floor_applied = None
    for min_n, floor in config.AGREEMENT_FLOORS:
        if n >= min_n:
            if outdated_score < floor:
                outdated_score = floor
                floor_applied = f"{n} independent outdated signals agree"
            break
    if box_style and mobile_score >= 10 and outdated_score < config.BOXED_AND_NOT_MOBILE_FLOOR:
        outdated_score = config.BOXED_AND_NOT_MOBILE_FLOOR
        floor_applied = "Boxed layout AND not mobile friendly"

    classification = "MODERN"
    for lo, hi, label in config.CLASSIFICATION_BANDS:
        if lo <= outdated_score <= hi:
            classification = label
            break
    lead_priority = "IGNORE"
    for lo, hi, label in config.LEAD_PRIORITY_BANDS:
        if lo <= outdated_score <= hi:
            lead_priority = label
            break

    # Mobile bilkul toota ho ya clearly boxed ho -> "MODERN" nahi kaha jayega
    if (mobile_score >= 10 or (box_score >= 8 and n >= 2)) and classification == "MODERN":
        classification = "NEEDS IMPROVEMENT"
        if lead_priority == "IGNORE":
            lead_priority = "LOW PRIORITY"

    # ---- Confidence ----
    confidence = (config.BASE_CONFIDENCE_WITH_AI_VISION if ai_result
                  else config.BASE_CONFIDENCE_HEURISTIC_ONLY)
    if layout:
        confidence += 0.05      # naapa hua layout = extra bharosa
    if not had_screenshots:
        confidence -= config.CONFIDENCE_PENALTY_NO_SCREENSHOT
    if not layout:
        confidence -= 0.10
    confidence = round(_clip(confidence, 0.05, 1.0), 2)

    all_reasons = []
    if floor_applied:
        all_reasons.append(f"[{floor_applied}]")
    all_reasons += visual_reasons + mobile_reasons + content_reasons + tech_reasons + perf_reasons
    # "fine" jaisi neutral lines hata do
    neutral = ("looks fine", "looks reasonably fresh", "No significantly", "looks acceptable",
               "No strong outdated")
    all_reasons = [r for r in all_reasons if not any(x in r for x in neutral)] or ["No significant outdated signals found"]

    content_pct = round(layout["content_ratio"] * 100) if layout else None
    return {
        "outdated_score": outdated_score,
        "classification": classification,
        "lead_priority": lead_priority,
        "visual_ui_score": visual_ui,
        "mobile_score": mobile_score,
        "content_ux_score": content_score,
        "technology_score": tech_score,
        "performance_score": perf_score,
        "modern_design_quality_score": modern_design,
        "overall_design_judgment_score": human,
        "box_style_score": box_score,
        "box_style": box_style,
        "mobile_responsive": mobile_responsive,
        "estimated_design_era": era,
        "content_width_px": layout.get("content_width_px") if layout else None,
        "content_width_pct_of_screen": content_pct,
        "side_margins_px": f"{layout['left_margin_px']}/{layout['right_margin_px']}" if layout else None,
        "strong_signals_count": n,
        "strong_signals": " | ".join(strong),
        "top_reasons": all_reasons[:8],
        "confidence": confidence,
    }
