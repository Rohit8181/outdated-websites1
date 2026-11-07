"""
config.py
=========
Saari settings, thresholds aur weights ek jagah. Agar aapko concurrency,
timeout, ya scoring thresholds change karne hain, sab yahin milega.
"""

import os

# --------------------------------------------------------------------
# FILE PATHS
# --------------------------------------------------------------------
# Default input file (agar command line par koi file naam nahi diya)
INPUT_CSV = "input_urls.csv"
INPUT_URL_COLUMN = "url"

LOG_FILE = "app.log"


def derive_output_paths(input_csv: str, prefix: str = None) -> dict:
    """
    Har input CSV file ke liye ALAG output files banata hai, based on
    us input file ke naam par. Isse agar aap naya CSV (naya batch)
    use karte hain, to purana result mix nahi hota -- har batch ki
    apni alag results/errors/hot_leads/screenshots files/folder
    ban jaati hain.

    Example:
        input_urls.csv       -> input_urls_results.csv, input_urls_hot_leads.csv, ...
        batch2_pharma.csv    -> batch2_pharma_results.csv, batch2_pharma_hot_leads.csv, ...
    """
    base = prefix or os.path.splitext(os.path.basename(input_csv))[0]
    return {
        "results_csv": f"{base}_results.csv",
        "full_results_json": f"{base}_full_results.json",
        "errors_csv": f"{base}_errors.csv",
        "hot_leads_csv": f"{base}_hot_leads.csv",
        "screenshot_dir": f"{base}_screenshots",
    }

# --------------------------------------------------------------------
# CONCURRENCY / NETWORK
# --------------------------------------------------------------------
CONCURRENCY = int(os.environ.get("CONCURRENCY", 5))              # Kitni websites parallel mein process hongi
NAVIGATION_TIMEOUT_MS = int(os.environ.get("NAV_TIMEOUT_MS", 30000))  # per page load
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", 2))              # Temporary failure par kitni baar retry karega
RETRY_DELAY_SECONDS = int(os.environ.get("RETRY_DELAY", 3))
# GitHub par screenshots ki zarurat nahi (AI vision band hai) -> time bachta hai
SKIP_SCREENSHOTS = os.environ.get("SKIP_SCREENSHOTS", "") == "1"

# 1920x1080 = jo screen aap khud dekhte hain. Boxed/fixed-width layout
# (dono taraf khali jagah) sirf wide screen par hi nazar aata hai.
DESKTOP_VIEWPORT = {"width": 1920, "height": 1080}
MOBILE_VIEWPORT = {"width": 390, "height": 844}

USER_AGENT_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
USER_AGENT_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# --------------------------------------------------------------------
# AI VISION (OPTIONAL) -- Claude ka use screenshots dekh kar visual
# judgment dene ke liye. Agar ANTHROPIC_API_KEY environment variable
# set nahi hai, to script automatically heuristic fallback use karega.
# --------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
USE_AI_VISION = bool(ANTHROPIC_API_KEY)
ANTHROPIC_MODEL = "claude-sonnet-5"

# --------------------------------------------------------------------
# SCORING WEIGHTS (spec ke exactly mutabik)
# --------------------------------------------------------------------
MAX_VISUAL_UI = 30
MAX_MOBILE = 15
MAX_CONTENT_UX = 15
MAX_TECHNOLOGY = 10
MAX_PERFORMANCE = 10
MAX_MODERN_DESIGN = 15
MAX_HUMAN_JUDGMENT = 5
MAX_TOTAL = (MAX_VISUAL_UI + MAX_MOBILE + MAX_CONTENT_UX + MAX_TECHNOLOGY +
             MAX_PERFORMANCE + MAX_MODERN_DESIGN + MAX_HUMAN_JUDGMENT)  # = 100

BOX_STYLE_MAX = 10
BOX_STYLE_TRUE_THRESHOLD = 6

# --------------------------------------------------------------------
# CLASSIFICATION THRESHOLDS
# --------------------------------------------------------------------
CLASSIFICATION_BANDS = [
    (75, 100, "STRONGLY OUTDATED"),
    (55, 74, "OUTDATED"),
    (35, 54, "NEEDS IMPROVEMENT"),
    (0, 34, "MODERN"),
]

LEAD_PRIORITY_BANDS = [
    (75, 100, "HOT LEAD"),
    (65, 74, "HIGH PRIORITY"),
    (55, 64, "MEDIUM PRIORITY"),
    (35, 54, "LOW PRIORITY"),
    (0, 34, "IGNORE"),
]

HOT_LEAD_MIN_SCORE = 65  # hot_leads.csv mein isse upar wale aayenge

# --------------------------------------------------------------------
# CONFIDENCE
# --------------------------------------------------------------------
BASE_CONFIDENCE_WITH_AI_VISION = 0.90
BASE_CONFIDENCE_HEURISTIC_ONLY = 0.55
CONFIDENCE_PENALTY_PARTIAL = 0.25
CONFIDENCE_PENALTY_NO_SCREENSHOT = 0.20


# --------------------------------------------------------------------
# DATED-LAYOUT DETECTION THRESHOLDS (browser mein naap kar nikale gaye)
# --------------------------------------------------------------------
BOX_MAX_CONTENT_RATIO = 0.85      # content screen ke 85% se kam chauda ho to "box" mana jaye
BOX_MIN_SIDE_MARGIN_RATIO = 0.06  # har taraf kam se kam 6% khali jagah
TINY_FONT_PX = 13                 # body text is size ya usse chhota = purana look
OLD_COPYRIGHT_AGE_YEARS = 8       # itne saal purana copyright = strong signal

# Strong signals ki agreement ke hisaab se minimum score (floor).
# Jab kai INDEPENDENT signals ek saath purane design ki taraf ishara
# karein, to score is level se neeche nahi jayega.
AGREEMENT_FLOORS = [
    (5, 75),   # 5+ strong signals -> kam se kam 75 (HOT LEAD)
    (4, 66),   # 4 strong signals  -> kam se kam 66 (HIGH PRIORITY)
    (3, 56),   # 3 strong signals  -> kam se kam 56 (MEDIUM PRIORITY)
]
BOXED_AND_NOT_MOBILE_FLOOR = 60   # aapki original definition: box type + mobile friendly nahi
