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

# (市場名稱, 網址)。重大訊息：上市（證交所）、上櫃（櫃買中心）。
SOURCES = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"),
    ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"),
]
# 綜合損益表（一般業）：涵蓋較早公告、但重大訊息已查不到的公司；沒有公告時間。
INCOME_SOURCES = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"),
    ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"),
]
OUT = Path(__file__).resolve().parent / "data" / "earnings.json"
TW = timezone(timedelta(hours=8))
PERIOD = re.compile(
    r"(\d{2,3})/(\d{1,2})/(\d{1,2})\s*/?\s*[~～\-－–—]\s*(\d{2,3})/(\d{1,2})/(\d{1,2})"
)


def fetch(url):
    req = urllib.request.Request(
        url, headers={"accept": "application/json", "user-agent": "tw-earnings/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        hdr = {k: r.headers.get(k) for k in ("Last-Modified", "ETag", "Date", "Age") if r.headers.get(k)}
        return json.loads(r.read().decode("utf-8-sig")), hdr


def g(rec, *keys):
    """上市與上櫃欄位名稱不同（公司代號／SecuritiesCompanyCode），依序取第一個有值的。"""
    for k in keys:
        if rec.get(k) not in (None, ""):
            return rec[k]
    return ""


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
    t = re.sub(r"新台幣|新臺幣|NT\$|\$|仟元|千元|元|,|\s", "", text or "")
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
    code = str(g(rec, "公司代號", "SecuritiesCompanyCode")).strip()
    subject = " ".join(str(g(rec, "主旨 ", "主旨")).split())  # 上市的欄位名稱尾端有空格
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
        "name": str(g(rec, "公司名稱", "CompanyName")).strip(),
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


def parse_income(rec, market):
    """損益表資料集（累計數字，沒有公告時間）。"""
    code = str(g(rec, "公司代號", "SecuritiesCompanyCode")).strip()
    try:
        year, quarter = int(g(rec, "年度", "Year")) + 1911, int(g(rec, "季別", "Season"))
    except ValueError:
        return None, f"{code} 年度或季別異常"
    rev = num(str(rec.get("營業收入", "")))
    if rev is None or quarter not in (1, 2, 3, 4):
        return None, f"{code} 沒有營業收入數字"
    # 統一用「營業毛利（毛損）」：財報公告與 XQ 歷史都是這一種，趨勢才接得起來。
    # 「淨額」另存成 gp_net 備查（官方營益分析用的是淨額，少數公司會差 0.2 個百分點以內）。
    gp = num(str(g(rec, "營業毛利（毛損）", "營業毛利（毛損）淨額")))
    gp_net = num(str(g(rec, "營業毛利（毛損）淨額")))
    return {
        "id": f"{code}|{year}|{quarter}",
        "code": code,
        "name": str(g(rec, "公司名稱", "CompanyName")).strip(),
        "market": market,
        "announced": None,
        "year": year,
        "quarter": quarter,
        "kind": "",
        "subject": "",
        "rev": rev,
        "gp": gp,
        "gp_net": gp_net,
        "op": num(str(rec.get("營業利益（損失）", ""))),
    }, None


def load_seed():
    """history.json 裡「最新期是 Q2」的公司附有 Q1、Q2 金額（仟元），用來推算第 3 季單季。"""
    p = OUT.parent / "history.json"
    if not p.exists():
        return {}
    comp = json.loads(p.read_text(encoding="utf-8")).get("companies", {})
    return {c: v["s"] for c, v in comp.items() if v.get("s")}


def add_single_quarter(records, seed=None):
    """單季 = 本季累計 - 上季累計。上季累計來自我們存的紀錄，或 history.json 的種子（標 approx）。"""
    seed = load_seed() if seed is None else seed
    by = {(r["code"], r["year"], r["quarter"]): r for r in records.values()}
    for r in records.values():
        cur = {"rev": r["rev"], "gp": r["gp"], "op": r["op"]}
        if r["quarter"] == 1:
            r["q"] = cur
            continue
        prev = by.get((r["code"], r["year"], r["quarter"] - 1))
        if not prev:
            sd = seed.get(r["code"])
            r["q"] = None
            if sd and sd["y"] == r["year"] and sd["lq"] == r["quarter"] - 1:
                cum_prev = {k: sum(sd[k]) for k in ("rev", "gp", "op")}  # Q1+Q2 金額（百萬換算，精度較低）
                r["q"] = {k: None if cur[k] is None else cur[k] - cum_prev[k] for k in cur}
                r["q"]["approx"] = True
            continue
        r["q"] = {
            k: (None if cur[k] is None or prev[k] is None else cur[k] - prev[k])
            for k in cur
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="改讀本機上市重大訊息 JSON 檔")
    ap.add_argument("--income-file", help="改讀本機上市損益表 JSON 檔")
    ap.add_argument("--tpex-file", help="改讀本機上櫃重大訊息 JSON 檔")
    ap.add_argument("--tpex-income-file", help="改讀本機上櫃損益表 JSON 檔")
    args = ap.parse_args()

    metas = []  # 記錄每個來源的更新線索，用來推測官方何時更新

    def note(label, market, rows, hdr):
        newest = lambda k: max((r[k] for r in rows if r.get(k)), default=None)
        metas.append({
            "source": f"{market}{label}", "rows": len(rows), "headers": hdr,
            "generated": roc_date(newest("出表日期") or newest("Date") or ""),   # 出表日期：官方產出這份資料的日期
            "latest_publish": roc_date(newest("發言日期") or ""),  # 資料裡最新的發言日期
        })

    def load(local, sources, label):
        got = []
        if any(local.values()):  # 測試用：只讀指定的本機檔
            for market, path in local.items():
                if path:
                    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
                    note(label, market, rows, {})
                    got.append((market, rows))
            return got
        for market, url in sources:
            try:
                rows, hdr = fetch(url)
                note(label, market, rows, hdr)
                got.append((market, rows))
            except Exception as e:  # 單一來源失敗不影響其他來源
                print(f"[警告] {market} 抓取失敗：{e}", file=sys.stderr)
        return got

    raw = load({"上市": args.file, "上櫃": args.tpex_file}, SOURCES, "重大訊息")
    income = load({"上市": args.income_file, "上櫃": args.tpex_income_file}, INCOME_SOURCES, "損益表")
    if not raw and not income:
        print("[錯誤] 沒有任何資料來源成功，不更新檔案。", file=sys.stderr)
        sys.exit(1)

    records, old_log = {}, []
    if OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        old_log = prev.get("log", [])
        for r in prev.get("records", []):
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
            if old is None or data["announced"] >= (old["announced"] or ""):  # 更正公告以最新為準
                new += old is None
                records[data["id"]] = data

    for market, rows in income:  # 損益表只補「重大訊息沒有的」，有公告時間的資料優先
        for rec in rows:
            data, err = parse_income(rec, market)
            if err:
                skipped += 1
                print(f"[略過] {err}", file=sys.stderr)
            if not data:
                continue
            old = records.get(data["id"])
            if old is None or old["announced"] is None:
                new += old is None
                records[data["id"]] = data
            elif data["gp"] is not None:
                old["gp"] = data["gp"]  # 以損益表的正式數字為準
                old["gp_net"] = data["gp_net"]

    add_single_quarter(records)
    out = {
        "updated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M"),
        "log": (old_log + [{"run_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M"), "sources": metas}])[-60:],
        "records": sorted(records.values(), key=lambda r: r["announced"] or "", reverse=True),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：新增 {new} 筆，略過 {skipped} 筆，累計 {len(records)} 筆。")


if __name__ == "__main__":
    main()
