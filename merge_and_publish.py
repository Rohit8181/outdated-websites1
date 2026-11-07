#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_and_publish.py
====================
Saare computers (shards) ka result jodta hai aur final files banata hai.

HOT LEAD ka rule:
   outdated score >= min_score (default 65, jaisa aapke command-prompt tool me)
   [AND mobile-friendly NAHI, agar --not-mobile-only diya]
   [AND box style, agar --box-only diya]
   (Sheet me "Mobile friendly" column hai, to aap Yes/No se khud bhi chhant sakti ho.)

Banne wali files:
   final_all_results.csv        ab tak check hui saari sites (resume ke liye)
   final_hot_leads.csv          is list ki hot leads (Sheet me jaati hain)
   final_hot_leads_all_time.csv ab tak ki saari hot leads
   final_not_hot.csv            check hui par lead nahi (Sheet se hatane ke liye)
   final_errors.csv             jo sites khuli nahi / rok di gayi (wajah ke saath)
"""
import argparse, csv, glob, os, sys
import urls_io

FIELDS = ["url", "final_url", "status", "outdated_score", "classification", "lead_priority",
          "visual_ui_score", "mobile_score", "content_ux_score", "technology_score", "performance_score",
          "modern_design_quality_score", "overall_design_judgment_score", "box_style_score", "box_style",
          "mobile_responsive", "estimated_design_era", "content_width_px", "content_width_pct_of_screen",
          "side_margins_px", "strong_signals_count", "strong_signals", "top_reasons", "confidence", "error"]
HOT_FIELDS = ["url", "outdated_score", "classification", "lead_priority", "box_style", "box_style_score",
              "mobile_responsive", "content_width_px", "content_width_pct_of_screen", "side_margins_px",
              "strong_signals", "estimated_design_era", "top_reasons", "confidence"]
GOOD = ("SUCCESS", "PARTIAL")


def is_hot(row, min_score, box_only, not_mobile_only=False):
    if row.get("status") not in GOOD:
        return False
    try:
        score = int(float(row.get("outdated_score")))
    except (TypeError, ValueError):
        return False
    if score < min_score:
        return False
    if not_mobile_only and str(row.get("mobile_responsive")).strip().lower() == "true":
        return False                                   # sirf "mobile-friendly nahi" wali list chahiye to
    if box_only and str(row.get("box_style")).strip().lower() != "true":
        return False
    return True


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", default="shards", help="folder jisme shard-*/shard_results.csv hain")
    ap.add_argument("--prev", default="final_all_results.csv")
    ap.add_argument("--list", help="is run ki websites file (hot_leads is list tak limit hoti hai)")
    ap.add_argument("--min-score", type=int, default=65)
    ap.add_argument("--box-only", action="store_true")
    ap.add_argument("--not-mobile-only", action="store_true", help="mobile-friendly sites ko lead se hata do")
    ap.add_argument("--prefix", default="final_")
    a = ap.parse_args()

    merged = {r["url"]: r for r in read_csv(a.prev) if r.get("url")}
    new_rows = 0
    for p in sorted(glob.glob(os.path.join(a.shards, "**", "*results.csv"), recursive=True)):
        for r in read_csv(p):
            u = r.get("url")
            if not u:
                continue
            old = merged.get(u)
            if old and old.get("status") in GOOD and r.get("status") not in GOOD:
                continue                                # purana achha result na bigade
            merged[u] = r
            new_rows += 1

    allrows = list(merged.values())
    write_csv(a.prefix + "all_results.csv", FIELDS, allrows)

    hot_all = sorted((r for r in allrows if is_hot(r, a.min_score, a.box_only, a.not_mobile_only)),
                     key=lambda r: -int(float(r["outdated_score"])))
    write_csv(a.prefix + "hot_leads_all_time.csv", HOT_FIELDS, hot_all)
    not_hot = [{"url": r["url"]} for r in allrows if r.get("status") in GOOD and not is_hot(r, a.min_score, a.box_only, a.not_mobile_only)]
    write_csv(a.prefix + "not_hot.csv", ["url"], not_hot)

    scope = None
    if a.list and os.path.exists(a.list):
        scope = {urls_io.normalize_url(u) for u in urls_io.read_urls(a.list)}
    hot = [r for r in hot_all if scope is None or r["url"] in scope]
    write_csv(a.prefix + "hot_leads.csv", HOT_FIELDS, hot)
    errs = [{"url": r["url"], "status": r.get("status"), "reason": r.get("error", "")}
            for r in allrows if r.get("status") not in GOOD and (scope is None or r["url"] in scope)]
    write_csv(a.prefix + "errors.csv", ["url", "status", "reason"], errs)

    in_scope = [r for r in allrows if scope is None or r["url"] in scope]
    ok = [r for r in in_scope if r.get("status") in GOOD]
    mob_yes = sum(1 for r in ok if str(r.get("mobile_responsive")).lower() == "true")
    print(f"Naye results jode: {new_rows} | is list ki sites: {len(scope) if scope is not None else '-'} | "
          f"check ho chuki: {len(ok)} | khuli nahi/rok di gayi: {len(errs)}")
    hot_mob = sum(1 for r in hot if str(r.get("mobile_responsive")).lower() == "true")
    print(f"HOT LEADS (score >= {a.min_score}{', mobile-friendly nahi' if a.not_mobile_only else ''}{', box style' if a.box_only else ''}): {len(hot)}"
          f"  (inme se mobile-friendly: {hot_mob}) | ab tak ki kul hot leads: {len(hot_all)}")


if __name__ == "__main__":
    main()
