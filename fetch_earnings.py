#!/usr/bin/env python3
"""抓取證交所 OpenAPI 的重大訊息，篩出「第31款」財報公告，
解析營業收入、營業毛利、營業利益，累積存到 data/earnings.json。

用法：
    python fetch_earnings.py                 # 從 OpenAPI 抓取
    python fetch_earnings.py --file x.json   # 改讀本機檔案（用來測試或補資料）
只用標準函式庫，不需要 pip install。
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# (市場名稱, 網址)。上櫃的端點尚未確認，確認後加在這裡即可。
SOURCES = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"),
]
OUT = Path(__file__).resolve().parent / "data" / "earnings.json"
TW = timezone(timedelta(hours=8))
PERIOD = re.compile(
    r"(\d{2,3})/(\d{1,2})/(\d{1,2})\s*[~～\-－–—]\s*(\d{2,3})/(\d{1,2})/(\d{1,2})"
)


def fetch(url):
    req = urllib.request.Request(
        url, headers={"accept": "application/json", "user-agent": "tw-earnings/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def roc_date(s):
    """'1151105' -> '2026-11-05'"""
    s = str(s).strip()
    if len(s) < 6 or not s.isdigit():
        return None
    return f"{int(s[:-4]) + 1911:04d}-{s[-4:-2]}-{s[-2:]}"


def clock(s):
    """'65311' -> '06:53:11'（時間不補零，要自己補）"""
    s = str(s).strip().zfill(6)
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def num(text):
    """'(85,996)' / '-39,694' / '4993156' / '新台幣646,154仟元' -> 數字；不適用 -> None"""
    t = re.sub(r"新台幣|新臺幣|仟元|千元|元|,|\s", "", text or "")
    if not re.fullmatch(r"\(?-?\d+(?:\.\d+)?\)?", t):
        return None
    v = float(t.strip("()").lstrip("-"))
    v = -v if (t.startswith("(") or t.startswith("-")) else v
    return int(v) if v == int(v) else v


def item(body, n):
    m = re.search(rf"(?m)^\s*{n}\.[^\r\n:：]*[:：]\s*([^\r\n]*)", body)
    return num(m.group(1)) if m else None


def parse(rec, market):
    """回傳 (資料, 失敗原因)。非財報公告回傳 (None, None)。"""
    if str(rec.get("符合條款", "")).strip() != "第31款":
        return None, None
    code = str(rec.get("公司代號", "")).strip()
    subject = " ".join(str(rec.get("主旨 ", rec.get("主旨", ""))).split())  # 欄位名稱尾端有空格
    body = str(rec.get("說明", ""))
    pm = PERIOD.search(body)
    if not pm:
        return None, f"{code} 找不到報導期間"
    year, month = int(pm.group(4)) + 1911, int(pm.group(5))
    if month not in (3, 6, 9, 12):
        return None, f"{code} 報導期間異常：{pm.group(0)}"
    rev, gp, op = item(body, 4), item(body, 5), item(body, 6)
    if rev is None:
        return None, f"{code} 沒有營業收入數字（可能是金融業）"
    date = roc_date(rec.get("發言日期"))
    if not date:
        return None, f"{code} 發言日期異常"
    kind = "合併" if "合併" in subject else ("個別" if "個別" in subject else "")
    quarter = month // 3
    return {
        "id": f"{code}|{year}|{quarter}",
        "code": code,
        "name": str(rec.get("公司名稱", "")).strip(),
        "market": market,
        "announced": f"{date} {clock(rec.get('發言時間'))}",
        "year": year,
        "quarter": quarter,
        "kind": kind,
        "subject": subject,
        "rev": rev,
        "gp": gp,
        "op": op,
    }, None


def add_single_quarter(records):
    """單季 = 本季累計 - 上季累計。找不到上季資料就留 None。"""
    by = {(r["code"], r["year"], r["quarter"]): r for r in records.values()}
    for r in records.values():
        cur = {"rev": r["rev"], "gp": r["gp"], "op": r["op"]}
        if r["quarter"] == 1:
            r["q"] = cur
            continue
        prev = by.get((r["code"], r["year"], r["quarter"] - 1))
        if not prev:
            r["q"] = None
            continue
        r["q"] = {
            k: (None if cur[k] is None or prev[k] is None else cur[k] - prev[k])
            for k in cur
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="改讀本機 JSON 檔（視為上市公司資料）")
    args = ap.parse_args()

    raw = []
    if args.file:
        raw.append(("上市", json.loads(Path(args.file).read_text(encoding="utf-8-sig"))))
    else:
        for market, url in SOURCES:
            try:
                raw.append((market, fetch(url)))
            except Exception as e:  # 單一來源失敗不影響其他來源
                print(f"[警告] {market} 抓取失敗：{e}", file=sys.stderr)
    if not raw:
        print("[錯誤] 沒有任何資料來源成功，不更新檔案。", file=sys.stderr)
        sys.exit(1)

    records = {}
    if OUT.exists():
        for r in json.loads(OUT.read_text(encoding="utf-8")).get("records", []):
            records[r["id"]] = r

    new = skipped = 0
    for market, rows in raw:
        for rec in rows:
            data, err = parse(rec, market)
            if err:
                skipped += 1
                print(f"[略過] {err}", file=sys.stderr)
            if not data:
                continue
            old = records.get(data["id"])
            if old is None or data["announced"] >= old["announced"]:  # 更正公告以最新為準
                new += old is None
                records[data["id"]] = data

    add_single_quarter(records)
    out = {
        "updated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M"),
        "records": sorted(records.values(), key=lambda r: r["announced"], reverse=True),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：新增 {new} 筆，略過 {skipped} 筆，累計 {len(records)} 筆。")


if __name__ == "__main__":
    main()
