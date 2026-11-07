"""
ai_vision.py
============
OPTIONAL module. Agar ANTHROPIC_API_KEY environment variable set hai,
to ye Claude ko desktop + mobile screenshots dikha kar ek real visual
judgment maangta hai -- exactly jaisa spec ke categories A, F, G, aur
BOX_STYLE chahte hain.

Agar API key nahi hai, to website_analyzer.py isse skip kar dega aur
scorer.py heuristic fallback use karega (kam confidence ke saath).

Setup: apna Anthropic API key environment variable mein set karein:
   Windows (cmd):     set ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
   Windows (powershell): $env:ANTHROPIC_API_KEY="sk-ant-xxxxxxxx"
"""

import base64
import json
import re

import config

try:
    import anthropic
    _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.USE_AI_VISION else None
except ImportError:
    _client = None


PROMPT = """You are an expert web designer evaluating a company website's visual design for a website-redesign sales lead qualification tool. Your client sells redesigns to corporate, manufacturing, chemical, pharmaceutical and industrial B2B companies in India, so most sites you see are from that sector.

You are shown two screenshots of the same homepage: DESKTOP (1920x1080, top of the page, exactly what a visitor sees first) and MOBILE (390px wide).

Judge ONLY visual appearance and design quality, not the underlying technology. A simple, clean, minimalist site is NOT outdated.

REFERENCE EXAMPLES -- real sites the client has confirmed as OUTDATED (these must score visual_ui_score >= 22 and box_style_score >= 7 when a new site resembles them):
 1. A narrow ~875px page in the middle of a 1920px screen with ~515px of empty white on each side; glossy beveled gradient header bar, glossy pill-shaped menu bar, glossy "Contact us" button.
 2. A ~1250px orange/yellow header image with swoosh and glossy orb bullets stuck to the LEFT edge, ~670px of blank white on the right, a "Flash Player no longer supported" placeholder, "Copyright 2004" and "Best viewed in 1024 x 768 resolution" in the footer.
 3. A ~975px box with a thin black border, grey header/footer strips, small justified text with uneven word gaps, "Not secure" (http), "Site designed by <agency>" in the footer.
 4. A ~975px bordered box, dark-blue gradient menu bar, boxed outlined text-buttons, an embedded video thumbnail, tiny dense underlined links, "Copyright 2004".
 5. A white strip in the middle of a dark-navy page background, tiny letter-spaced small-caps menu, dotted pink dividers, justified text, "This plugin is not supported", "Copyright 2004", "Website design by <agency>".
 6. A saturated flat green box (~1520px) with a slider and dot pagination, full-justified large text, "DESIGN BY <agency>" footer, side white margins.
 7. A 1990s page: menu made of image buttons and clip-art (3D globe, hexagon molecules, WordArt-style logo), horizontal rules between rows, address with FAX number, "Not secure", "Designed, hosted and promoted by <agency>".
 8. A ~1250px centered white box with decorative artwork spilling into the side margins, a tiny pipe-separated text menu ("CORPORATE | PHARMACEUTICAL ACTIVES | ..."), a slider with arrow buttons -- moderately dated, same "boxed column" family.

Typical outdated signals in this sector: fixed-width content column with empty margins on both sides (or glued to one side); glossy/beveled buttons and gradient bars; image-based or clip-art menus; justified small text; dense text walls; low-quality or stretched photos; thin borders around content boxes; orb/swoosh/WordArt styling; Flash or plugin placeholders; old copyright years; "best viewed in" notes; agency credit lines; on mobile the same desktop layout simply shrunk down.

What "modern" looks like (do NOT penalize): full-width sections, generous whitespace, one clear hero with a strong headline and large clean image, consistent sans-serif type with clear hierarchy, a mobile hamburger/stacked menu, card or alternating-section layouts, flat or outlined CTA buttons.

Be decisive and consistent: if the site belongs to the same family as the reference examples above, say so with high scores; if it is clearly modern, give low scores.

Score the following, strictly as JSON, nothing else:

{
  "visual_ui_score": <0-30, higher = looks MORE outdated/old-fashioned>,
  "modern_design_quality_score": <0-15, higher = looks MORE outdated (i.e. LACKS modern design quality)>,
  "overall_design_judgment_score": <0-5, higher = looks MORE outdated as an overall gut-check>,
  "box_style_score": <0-10, higher = more of an old boxed/bordered/table-like corporate layout>,
  "mobile_visually_broken": <true/false -- does the mobile screenshot look broken, overflowing, or like a squeezed desktop site>,
  "estimated_design_era": "<e.g. '2005-2010', '2010-2016', '2018-2022', '2023-2026'>",
  "top_reasons": ["<short reason 1>", "<short reason 2>", "<short reason 3>", "up to 6 reasons"]
}

Respond with ONLY the JSON object, no other text, no markdown fences.
"""


def _image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_screenshots(desktop_path: str, mobile_path: str, layout_note: str = "") -> dict:
    """
    Returns a dict matching the PROMPT's JSON schema, or None if AI
    vision is unavailable or the call fails.
    """
    if _client is None or not desktop_path or not mobile_path:
        return None

    try:
        desktop_b64 = _image_to_base64(desktop_path)
        mobile_b64 = _image_to_base64(mobile_path)

        response = _client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "DESKTOP screenshot:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": desktop_b64}},
                        {"type": "text", "text": "MOBILE (390px) screenshot:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": mobile_b64}},
                        {"type": "text", "text": ("MEASURED FACTS from the real rendered page (trust these numbers): " + layout_note + "\n\n" if layout_note else "") + PROMPT},
                    ],
                }
            ],
        )

        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        raw_text = re.sub(r"^```json|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
        data = json.loads(raw_text)
        return data

    except Exception as e:
        print(f"    [AI Vision] Skipped due to error: {e}")
        return None
