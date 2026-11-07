"""check_final_rule.py - labels (outdated/modern) par FINAL lead rule ki accuracy napta hai.
   Use: python check_final_rule.py <results.csv> <labels.csv> [min_score]"""
import csv, re, sys

def norm(u):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", u.strip().lower())).rstrip("/")

res, lab = sys.argv[1], sys.argv[2]
args = [a for a in sys.argv[3:] if not a.startswith("--")]
min_score = int(args[0]) if args else 65
not_mobile_only = "--not-mobile-only" in sys.argv
labels = {}
with open(lab, newline="", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        labels[norm(r["url"])] = (r["label"].strip().lower(), r.get("note", ""))
tp = fn = fp = tn = fail = 0
rows = []
with open(res, newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        k = norm(r["url"])
        if k not in labels:
            continue
        truth, note = labels[k]
        if r.get("status") not in ("SUCCESS", "PARTIAL"):
            fail += 1; rows.append((r["url"], truth, "khuli nahi / rok di gayi", r.get("error", ""))); continue
        score = int(float(r["outdated_score"] or 0))
        mob = str(r.get("mobile_responsive")).lower() == "true"
        hot = score >= min_score and not (not_mobile_only and mob)
        why = f"score {score}, mobile-friendly: {'Yes' if mob else 'No'}, box: {r.get('box_style')}"
        if truth == "outdated":
            if hot: tp += 1
            else: fn += 1; rows.append((r["url"], truth, "CHHOOTI (lead nahi bani) - " + why, r.get("strong_signals", "")))
        else:
            if hot: fp += 1; rows.append((r["url"], truth, "GALAT LEAD - " + why, r.get("strong_signals", "")))
            else: tn += 1
print("=" * 62)
print(f"FINAL RULE: score >= {min_score}" + (" aur mobile-friendly NAHI" if not_mobile_only else " (mobile-friendly bhi chalegi)"))
print(f"Purani sites jo lead bani : {tp} / {tp + fn}")
print(f"Modern sites jo galat lead: {fp} / {fp + tn}")
print(f"Khuli nahi / rok di gayi  : {fail}")
print("=" * 62)
for u, t, w, s in rows:
    print(f" - {u} [asli: {t}] -> {w}")
    if s: print(f"     signals: {s}")
