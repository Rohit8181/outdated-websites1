"""
main.py
=======
Entry point. Ye:
  1. input CSV se URLs padhta hai (default: input_urls.csv, ya command
     line par diya gaya koi bhi file)
  2. URLs normalize + deduplicate karta hai
  3. Usi input file ke naam par based results check karta hai --
     jo URLs already process ho chuki hain unhe SKIP kar deta hai
     (RESUME support, sirf usi input file ke liye)
  4. Playwright browser launch karta hai
  5. asyncio.Semaphore se controlled concurrency ke saath saari websites
     process karta hai
  6. Har result ko turant results.csv + full_results.json mein save
     karta hai (taaki beech mein rukne par kaam waste na ho)
  7. Errors ko errors.csv mein log karta hai
  8. Aakhir mein hot_leads.csv banata hai (score >= 65, sorted)

IMPORTANT -- HAR NAYE CSV KE LIYE ALAG OUTPUT FILES:
  Output file names input CSV ke naam se derive hote hain, isliye
  agar aap alag-alag batch CSVs use karte hain, unke results kabhi
  mix nahi honge:

      python main.py                     -> input_urls.csv use hoga
                                             -> input_urls_results.csv, etc.
      python main.py batch2_pharma.csv   -> batch2_pharma_results.csv, etc.

RUN KARNE KA TAREEKA:
    python main.py
    python main.py my_batch.csv

RESUME (agar beech mein ruk gaya, wahi input file ke saath dobara chalayein):
    python main.py my_batch.csv
    -- ye already-done URLs skip kar dega aur baaki jaari rakhega.
"""

import asyncio
import csv
import json
import os
import sys
import time

from playwright.async_api import async_playwright

import argparse

import config
import urls_io
import website_analyzer

DEADLINE = None   # --max-minutes: iske baad nayi site shuru nahi hogi


RESULT_FIELDS = [
    "url", "final_url", "status", "outdated_score", "classification",
    "lead_priority", "visual_ui_score", "mobile_score", "content_ux_score",
    "technology_score", "performance_score", "modern_design_quality_score",
    "overall_design_judgment_score", "box_style_score", "box_style",
    "mobile_responsive", "estimated_design_era",
    "content_width_px", "content_width_pct_of_screen", "side_margins_px",
    "strong_signals_count", "strong_signals", "top_reasons",
    "confidence", "desktop_screenshot", "mobile_screenshot", "error",
]


def normalize_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def load_input_urls(input_csv: str) -> list:
    if not os.path.exists(input_csv):
        print(f"ERROR: '{input_csv}' nahi mili. Pehle isse banayein "
              f"(column name '{config.INPUT_URL_COLUMN}').")
        sys.exit(1)

    urls = []
    seen = set()
    for raw in urls_io.read_urls(input_csv):      # CSV ya Excel, header kuch bhi ho
        url = normalize_url(raw)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def load_skip_urls(path: str, retry_errors: bool) -> set:
    """Purane (merged) results me jo sites ho chuki hain unhe dobara mat lo."""
    if not path or not os.path.exists(path):
        return set()
    skip = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            u = row.get("url")
            if not u:
                continue
            if row.get("status") in ("FAILED", "BLOCKED") and retry_errors:
                continue
            skip.add(u)
    return skip


def load_completed_urls(results_csv: str) -> set:
    if not os.path.exists(results_csv):
        return set()
    completed = set()
    with open(results_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            u = row.get("url")
            if u:
                completed.add(u)
    return completed


def ensure_results_csv_header(results_csv: str):
    if not os.path.exists(results_csv):
        with open(results_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            writer.writeheader()


def ensure_errors_csv_header(errors_csv: str):
    if not os.path.exists(errors_csv):
        with open(errors_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["url", "status", "error"])


def append_result_csv(result: dict, results_csv: str):
    row = {k: result.get(k) for k in RESULT_FIELDS}
    if isinstance(row.get("top_reasons"), list):
        row["top_reasons"] = " | ".join(row["top_reasons"])
    with open(results_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writerow(row)


def append_error_csv(result: dict, errors_csv: str):
    if result["status"] in ("FAILED", "BLOCKED"):
        with open(errors_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([result["url"], result["status"], result.get("error", "")])


def append_full_json(result: dict, full_results_json: str):
    with open(full_results_json + ".lines", "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def rebuild_full_json_array(full_results_json: str):
    lines_path = full_results_json + ".lines"
    if not os.path.exists(lines_path):
        return
    results = []
    with open(lines_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    with open(full_results_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def build_hot_leads_csv(results_csv: str, hot_leads_csv: str):
    if not os.path.exists(results_csv):
        return
    rows = []
    with open(results_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            score = row.get("outdated_score")
            if score not in (None, "", "None"):
                try:
                    score_val = int(float(score))
                except ValueError:
                    continue
                if score_val >= config.HOT_LEAD_MIN_SCORE:
                    row["_score_val"] = score_val
                    rows.append(row)

    rows.sort(key=lambda r: r["_score_val"], reverse=True)

    hot_fields = ["url", "outdated_score", "classification", "lead_priority",
                  "box_style", "box_style_score", "mobile_responsive",
                  "content_width_px", "content_width_pct_of_screen", "side_margins_px",
                  "strong_signals", "estimated_design_era", "top_reasons", "confidence"]

    with open(hot_leads_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=hot_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in hot_fields})

    print(f"\nHot leads file ready: {hot_leads_csv} ({len(rows)} websites, score >= {config.HOT_LEAD_MIN_SCORE})")


async def process_url(browser, url: str, semaphore: asyncio.Semaphore,
                       lock: asyncio.Lock, index: int, total: int,
                       paths: dict):
    async with semaphore:
        if DEADLINE and time.time() > DEADLINE:
            return                      # time khatam: ye site agle run me hogi (record nahi hoti)
        start = time.time()
        print(f"[{index}/{total}] Checking: {url}")
        try:
            result = await website_analyzer.analyze_website(browser, url, paths["screenshot_dir"])
        except Exception as e:
            result = {
                "url": url, "final_url": url, "status": "FAILED",
                "error": str(e), "outdated_score": None, "classification": None,
                "lead_priority": None, "visual_ui_score": None, "mobile_score": None,
                "content_ux_score": None, "technology_score": None,
                "performance_score": None, "modern_design_quality_score": None,
                "overall_design_judgment_score": None, "box_style_score": None,
                "box_style": None, "mobile_responsive": None,
                "estimated_design_era": None, "top_reasons": [], "confidence": 0.0,
            }

        elapsed = time.time() - start
        score_display = result.get("outdated_score")
        print(f"    -> {result['status']} | score={score_display} | {elapsed:.1f}s")

        async with lock:
            append_result_csv(result, paths["results_csv"])
            append_error_csv(result, paths["errors_csv"])
            append_full_json(result, paths["full_results_json"])


def clear_previous_output(paths: dict):
    """
    --fresh flag diye jaane par is input CSV se juri saari purani
    output files delete kar deta hai, taaki bilkul naye sirey se
    (bina kisi resume ke) processing shuru ho.
    """
    files_to_remove = [
        paths["results_csv"],
        paths["full_results_json"],
        paths["full_results_json"] + ".lines",
        paths["errors_csv"],
        paths["hot_leads_csv"],
    ]
    for fpath in files_to_remove:
        if os.path.exists(fpath):
            os.remove(fpath)
            print(f"  Deleted old file: {fpath}")

    screenshot_dir = paths["screenshot_dir"]
    if os.path.isdir(screenshot_dir):
        import shutil
        shutil.rmtree(screenshot_dir)
        print(f"  Deleted old screenshots folder: {screenshot_dir}/")


async def main_async():
    # Command line se input file aur flags padhna
    global DEADLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", default=config.INPUT_CSV)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--heuristic-only", action="store_true")
    ap.add_argument("--shard", help="i/N: list ka sirf i-wa hissa (N hisson me)")
    ap.add_argument("--skip-from", help="purani merged results CSV: jo sites ho chuki hain skip karo")
    ap.add_argument("--retry-errors", action="store_true", help="pehle FAILED/BLOCKED sites dobara try karo")
    ap.add_argument("--out", help="output files ka prefix (default: input file ka naam)")
    ap.add_argument("--max-minutes", type=float, help="itne minute baad nayi site shuru nahi hogi")
    cli = ap.parse_args()
    fresh_start = cli.fresh
    force_heuristic = cli.heuristic_only
    input_csv = cli.input
    paths = config.derive_output_paths(input_csv, cli.out)
    if cli.max_minutes:
        DEADLINE = time.time() + cli.max_minutes * 60

    # ---- AI VISION SAFETY CHECK ----
    # Bina AI vision ke, visual/outdated judgment sirf rough HTML
    # heuristics se aata hai -- bahut si genuinely outdated-looking
    # sites (purane fonts, images, spacing) isse miss ho jaati hain,
    # kyunki ye cheezein sirf screenshot DEKH kar pata chalti hain,
    # HTML code padh kar nahi. Isliye bina API key ke chalne se pehle
    # ek clear warning dete hain, taaki accidentally low-accuracy
    # result na mil jaye.
    if not config.USE_AI_VISION and not force_heuristic:
        print("=" * 70)
        print("RUKIYE: AI VISION (ANTHROPIC_API_KEY) SET NAHI HAI")
        print("=" * 70)
        print(
            "Bina AI vision ke ye tool sirf HTML/CSS ke technical red flags\n"
            "dhoondh sakta hai (missing viewport, tables, purane tags) --\n"
            "purane fonts, images, spacing, colors jaisi 'visual' outdated-ness\n"
            "ko ye NAHI pakad sakta, kyunki wo sirf screenshot dekh kar hi pata\n"
            "chalti hai. Is mode mein result ka ek bada hissa galat/incomplete\n"
            "ho sakta hai (jaisa aapne pehle dekha).\n\n"
            "Sahi (accurate) result ke liye:\n"
            "  1) Apni Anthropic API key set karein:\n"
            "       set ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx\n"
            "  2) Phir se chalayein: python main.py\n\n"
            "Agar phir bhi bina AI vision ke (kam accurate) chalana hai, to:\n"
            "       python main.py --heuristic-only\n"
        )
        return

    if force_heuristic:
        print("NOTE: --heuristic-only mode mein chal raha hai -- result "
              "kam accurate honge kyunki AI vision disabled hai.\n")

    if fresh_start:
        clear_previous_output(paths)
        print("--fresh diya gaya: purana result clear karke naye sirey se shuru kar raha hoon.\n")

    print(f"Input file: {input_csv}")
    print(f"Output files is run ke liye:")
    print(f"  - {paths['results_csv']}")
    print(f"  - {paths['full_results_json']}")
    print(f"  - {paths['errors_csv']}")
    print(f"  - {paths['hot_leads_csv']}")
    print(f"  - Screenshots: {paths['screenshot_dir']}/\n")

    ensure_results_csv_header(paths["results_csv"])
    ensure_errors_csv_header(paths["errors_csv"])

    all_urls = load_input_urls(input_csv)
    if cli.shard:
        si, sn = (int(x) for x in cli.shard.split("/"))
        all_urls = all_urls[si::sn]
        print(f"Shard {si + 1}/{sn}: is computer ke hisse me {len(all_urls)} sites")
    completed = load_completed_urls(paths["results_csv"])
    skip = load_skip_urls(cli.skip_from, cli.retry_errors)
    pending = [u for u in all_urls if u not in completed and u not in skip]
    if skip:
        print(f"Pehle se ho chuki (skip): {len([u for u in all_urls if u in skip])}")

    print(f"Total URLs: {len(all_urls)}")
    print(f"Already completed (resume): {len(completed)}")
    print(f"Pending: {len(pending)}\n")

    if not pending:
        print("Sab URLs already process ho chuki hain. Hot leads file bana raha hoon...")
        build_hot_leads_csv(paths["results_csv"], paths["hot_leads_csv"])
        rebuild_full_json_array(paths["full_results_json"])
        return

    semaphore = asyncio.Semaphore(config.CONCURRENCY)
    lock = asyncio.Lock()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            tasks = [
                process_url(browser, url, semaphore, lock, i + 1, len(pending), paths)
                for i, url in enumerate(pending)
            ]
            await asyncio.gather(*tasks)
        finally:
            await browser.close()

    build_hot_leads_csv(paths["results_csv"], paths["hot_leads_csv"])
    rebuild_full_json_array(paths["full_results_json"])
    print("\nDone! Saari files ready hain:")
    print(f"  - {paths['results_csv']}")
    print(f"  - {paths['full_results_json']}")
    print(f"  - {paths['errors_csv']}")
    print(f"  - {paths['hot_leads_csv']}")


if __name__ == "__main__":
    asyncio.run(main_async())
