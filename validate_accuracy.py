"""
validate_accuracy.py
====================
Tool ki accuracy khud naapne ke liye.

Kaise:
  1) calibration_labels.csv par tool chalayein (is file mein 'url' column hai,
     isliye ye seedha input ban jati hai):
         python main.py calibration_labels.csv --fresh
  2) Phir:
         python validate_accuracy.py calibration_labels_results.csv calibration_labels.csv

Ye batata hai kitni sites sahi pakdi aur kaunsi galat gayi.
Galat gayi sites ka naam mujhe bhej dein -- unke hisaab se rules tune kar denge.
Apni labels file mein aap aur sites (label = outdated / modern) jod sakte hain;
jitni zyada labeled sites, utna sahi accuracy number.
"""
import csv
import re
import sys

OUTDATED_CLASSES = {"OUTDATED", "STRONGLY OUTDATED"}


def norm(u: str) -> str:
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def main():
    if len(sys.argv) < 3:
        print("Use: python validate_accuracy.py <results.csv> <labels.csv> [min_score]")
        return
    results_path, labels_path = sys.argv[1], sys.argv[2]
    min_score = int(sys.argv[3]) if len(sys.argv) > 3 else 55

    labels = {}
    with open(labels_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            labels[norm(row["url"])] = row["label"].strip().lower()

    tp = fp = tn = fn = failed = 0
    misses = []
    with open(results_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = norm(row["url"])
            if key not in labels:
                continue
            truth = labels[key]
            if row.get("status") not in ("SUCCESS", "PARTIAL") or not row.get("outdated_score"):
                failed += 1
                misses.append((row["url"], truth, "FAILED/BLOCKED (analysis nahi ho paya)", ""))
                continue
            score = int(float(row["outdated_score"]))
            predicted_outdated = row["classification"] in OUTDATED_CLASSES or score >= min_score
            if truth == "outdated" and predicted_outdated:
                tp += 1
            elif truth == "outdated" and not predicted_outdated:
                fn += 1
                misses.append((row["url"], truth, f"MISSED (score {score}, {row['classification']})", row.get("strong_signals", "")))
            elif truth == "modern" and not predicted_outdated:
                tn += 1
            else:
                fp += 1
                misses.append((row["url"], truth, f"FALSE ALARM (score {score}, {row['classification']})", row.get("strong_signals", "")))

    judged = tp + fp + tn + fn
    print("=" * 60)
    print(f"Labeled sites judged : {judged}   (analysis fail: {failed})")
    print(f"Outdated sites pakdi : {tp} / {tp + fn}   (recall)")
    print(f"Modern sites galat   : {fp} / {fp + tn}   (false alarms)")
    if judged:
        print(f"Overall accuracy     : {round(100 * (tp + tn) / judged, 1)}%")
    print("=" * 60)
    if misses:
        print("\nJo sahi nahi aayi:")
        for url, truth, why, sig in misses:
            print(f"  - {url}  [asli: {truth}]  -> {why}")
            if sig:
                print(f"      signals mile: {sig}")
    else:
        print("\nSab labeled sites sahi aayi.")


if __name__ == "__main__":
    main()
