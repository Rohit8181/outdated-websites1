#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hot_leads.csv ko Google Sheet me update karta hai.
 - naya link  -> nayi row add
 - purana link (http/https/www/slash alag ho tab bhi) -> wahi row replace
 - aapke apne columns (Status, Notes...) agar L ya usse aage hain to untouched rehte hain."""
import csv, datetime as dt, json, os, re, sys

COLS = ["Website", "Priority", "Score", "Mobile friendly", "Content width (px)", "Design era",
        "Key signals", "Why outdated", "Confidence", "Last checked", "First found"]


def key(u):
    return re.sub(r"^(https?://)?(www\.)?", "", (u or "").strip().lower()).rstrip("/")


def read_leads(path, today):
    best = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            u = (r.get("url") or "").strip()
            if not u:
                continue
            row = [u, r.get("lead_priority", ""), r.get("outdated_score", ""),
                   "Yes" if str(r.get("mobile_responsive")).lower() == "true" else "No",
                   r.get("content_width_px", ""), (r.get("estimated_design_era") or "").replace(" (estimated)", ""),
                   r.get("strong_signals", ""), r.get("top_reasons", ""), r.get("confidence", ""), today]
            try:
                sc = float(r.get("outdated_score") or 0)
            except ValueError:
                sc = 0
            k = key(u)
            if k not in best or sc > best[k][0]:
                best[k] = (sc, row)
    return [v[1] for v in best.values()]


def upsert(ws, leads, today):
    col = ws.col_values(1)
    if not col or not col[0].strip():
        ws.batch_update([{"range": "A1:K1", "values": [COLS]}], value_input_option="RAW")
        col = [COLS[0]]
    index = {}
    for i, v in enumerate(col[1:], start=2):
        index.setdefault(key(v), i)              # sheet me duplicate ho to pehli row update hoti hai
    upd, new = [], []
    for row in leads:
        i = index.get(key(row[0]))
        if i:
            upd.append({"range": f"A{i}:J{i}", "values": [row]})      # K (First found) purani rehti hai
        else:
            new.append(row + [today])
    failed = 0
    for s in range(0, len(upd), 200):
        try:
            ws.batch_update(upd[s:s + 200], value_input_option="RAW")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  update chunk {s}-{s + 200} fail: {type(e).__name__} {str(e)[:200]}")
    need = len(col) + len(new) + 50                      # sheet ke rows pehle badha do (default me kam hote hain)
    if new and ws.row_count < need:
        ws.add_rows(need - ws.row_count + 100)
    for s in range(0, len(new), 200):
        try:
            ws.append_rows(new[s:s + 200], value_input_option="RAW", insert_data_option="INSERT_ROWS")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  append chunk {s}-{s + 200} fail: {type(e).__name__} {str(e)[:200]}")
    total = max(len(ws.col_values(1)) - 1, 0)
    print(f"  Sheet me ab total {total} website rows hain.")
    if failed:
        raise RuntimeError(f"{failed} chunk(s) fail hue - upar ka error dekho")
    return len(upd), len(new)


def prune(sh, ws, path, today):
    """Jo sites ab lead nahi rahi (naye rules se) unhe 'Removed' tab me bhejo (delete nahi, copy karke)."""
    import gspread
    if not path or not os.path.exists(path):
        return 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        drop = {key(r.get("url")) for r in csv.DictReader(fh) if r.get("url")}
    rows = ws.get_all_values()
    move = [(i, r) for i, r in enumerate(rows[1:], start=2) if r and r[0].strip() and key(r[0]) in drop]
    if not move:
        return 0
    try:
        rem = sh.worksheet("Removed")
    except gspread.exceptions.WorksheetNotFound:
        rem = sh.add_worksheet(title="Removed", rows=1000, cols=max(len(rows[0]) + 1, 12))
        rem.append_rows([rows[0] + ["Removed on"]], value_input_option="RAW")
    for s0 in range(0, len(move), 200):
        rem.append_rows([r + [""] * (len(rows[0]) - len(r)) + [today] for _, r in move[s0:s0 + 200]],
                        value_input_option="RAW", insert_data_option="INSERT_ROWS")
    reqs = [{"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": i - 1, "endIndex": i}}}
            for i, _ in sorted(move, reverse=True)]
    for s0 in range(0, len(reqs), 200):
        sh.batch_update({"requests": reqs[s0:s0 + 200]})
    return len(move)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "hot_leads.csv"
    sa, url = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("SHEET_URL")
    if not sa or not url:
        print("Google Sheet secrets set nahi hain - sheet update skip kiya.")
        return
    if not os.path.exists(path):
        print(f"{path} nahi mili - sheet update skip kiya.")
        return
    import gspread
    info = json.loads(sa)
    m = re.search(r"/d/([\w-]+)", url)
    sid = m.group(1) if m else url.strip()
    try:
        sh = gspread.service_account_from_dict(info).open_by_key(sid)
        ws = sh.worksheet(os.environ["SHEET_TAB"]) if os.environ.get("SHEET_TAB") else sh.sheet1
        today = dt.date.today().isoformat()
        leads = read_leads(path, today)
        u, n = upsert(ws, leads, today)
        print(f"Sheet updated: {u} purani rows replace, {n} nayi rows add (total {len(leads)} hot leads).")
        removed = prune(sh, ws, sys.argv[2] if len(sys.argv) > 2 else None, today)
        if removed:
            print(f"  {removed} rows ab lead nahi rahi - 'Removed' tab me bhej di gayi.")
    except Exception as e:  # noqa: BLE001
        print("Sheet update fail:", type(e).__name__, str(e)[:300])
        print(f"Check: Sheet ko is email ke saath Editor banake share kiya? -> {info.get('client_email')}")
        print("Aur Google Sheets API enable hai? Sheet asli Google Sheet hai (xlsx nahi)?")
        sys.exit(1)


if __name__ == "__main__":
    main()
