#!/usr/bin/env python3
"""從公開資訊觀測站「即時重大訊息」抓當天的財報公告，比 OpenAPI 早一天。

流程：
1. 抓當天完整清單（一個請求）。
2. 主旨含「財務報告／財務報表」的列，若還沒看過，就逐筆取詳細內容（每筆間隔幾秒）。
3. 詳細內容裡「符合條款」是第 31 款、市場是上市或上櫃，才算財報公告，並解析營業收入、毛利、營業利益。
4. 只新增 earnings.json 裡還沒有的季別；隔天 OpenAPI 有同一筆時，會由 fetch_earnings.py 用官方資料覆蓋。
遇到對方的阻擋訊息就立刻停止，下次再繼續。只用標準函式庫。
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_earnings as fe  # noqa: E402

BASE = "https://mopsov.twse.com.tw"
URL_PAGE = BASE + "/mops/web/t05sr01_1"
URL_AJAX = BASE + "/mops/web/ajax_t05sr01_1"
SEEN = fe.OUT.parent / "mops_seen.json"
MAX_DETAIL = 150   # 每次最多查幾筆詳細內容，其餘留到下一次
PAUSE = 2.5        # 每次詳細內容請求之間的間隔（秒）
KEEP_DAYS = 7      # 「看過」紀錄保留天數
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ROW = re.compile(r"<tr class='(?:even|odd)'>(.*?)</tr>", re.S)
FIN = re.compile("財務報告|財務報表|財報")
BLOCK_MARKS = ("因為安全性考量", "THIS PAGE CAN NOT BE ACCESSED", "Access Denied")
DETAIL_ORDER = ["TYPEK", "step", "skey", "hhc_co_name", "firstin", "COMPANY_ID",
                "COMPANY_NAME", "SPOKE_DATE", "SPOKE_TIME", "SEQ_NO"]


def http(method, url, fields=None):
    hdr = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9", "Accept": "text/html,*/*"}
    body = None
    if fields is not None:  # 與網頁的 ajax1 相同：最前面固定加 encodeURIComponent=1
        pairs = [("encodeURIComponent", "1")] + list(fields)
        body = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in pairs).encode()
        hdr.update({"Content-Type": "application/x-www-form-urlencoded",
                    "Referer": URL_PAGE, "Origin": BASE})
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return {"status": r.status, "text": r.read(4_000_000).decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "text": e.read(20000).decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "text": ""}


def plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text))).strip()


def parse_rows(text):
    rows = []
    for m in ROW.finditer(text):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
        if len(tds) < 5:
            continue
        c = [plain(t) for t in tds[:5]]
        rows.append({"code": c[0], "name": c[1], "date": c[2], "time": c[3], "subject": c[4],
                     "fields": dict(re.findall(r"\.(\w+)\.value='([^']*)'", m.group(1)))})
    return rows


def fetch_detail(row):
    f = row["fields"]
    vals = {"TYPEK": "all", "step": "1", "firstin": "true", "hhc_co_name": "", "COMPANY_NAME": "", **f}
    return http("POST", URL_AJAX, [(k, vals.get(k, "")) for k in DETAIL_ORDER])


def parse_detail(text):
    """回傳 (市場, 條款編號, 說明內文)。"""
    m = re.search(r"[\(（](上市|上櫃|興櫃|公開發行)公司[\)）]", text)
    c = re.search(r"符合條款.*?<td[^>]*>\s*(\d+)\s*</td>", text, re.S)
    d = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S)
    return (m.group(1) if m else ""), (c.group(1) if c else ""), (html.unescape(d.group(1)) if d else "")


def process(rows, get_detail, records, seen, limit=MAX_DETAIL, pause=PAUSE, sleep=time.sleep, retry_wait=8):
    st = {"candidates": 0, "seen": 0, "queried": 0, "added": 0, "not_earnings": 0,
          "duplicate": 0, "parse_fail": [], "errors": 0, "streak": 0, "blocked": False}
    cand = [r for r in rows if FIN.search(r["subject"]) and r["fields"].get("skey")]
    st["candidates"] = len(cand)
    todo = [r for r in cand if r["fields"]["skey"] not in seen]
    st["seen"] = len(cand) - len(todo)
    bad = lambda r: "error" in r or r.get("status") != 200 or not r.get("text")
    for row in todo[:limit]:
        res = get_detail(row)
        sleep(pause)
        if bad(res) and not any(b in res.get("text", "") for b in BLOCK_MARKS):
            sleep(retry_wait)  # 暫時性失敗（逾時等）先等一下再試一次
            res = get_detail(row)
            sleep(pause)
        st["queried"] += 1
        text = res.get("text", "")
        if any(b in text for b in BLOCK_MARKS):
            st["blocked"] = True
            break
        if bad(res):
            st["errors"] += 1
            st["streak"] += 1
            print(f"[錯誤] {row['code']} {row['time']} {res.get('error') or 'HTTP ' + str(res.get('status'))}")
            if st["streak"] >= 3:  # 連續 3 筆都失敗才先停，其餘留到下一次
                break
            continue
        st["streak"] = 0
        key = row["fields"]["skey"]
        seen[key] = row["fields"].get("SPOKE_DATE", "")
        market, clause, body = parse_detail(text)
        if clause != "31" or market not in ("上市", "上櫃"):
            st["not_earnings"] += 1
            continue
        rec = {"公司代號": row["code"], "公司名稱": row["name"],
               "發言日期": row["date"].replace("/", ""), "發言時間": row["time"].replace(":", ""),
               "主旨": row["subject"], "符合條款": "第31款", "說明": body}
        data, err = fe.parse(rec, market)
        if err:
            st["parse_fail"].append(err)
        elif data["id"] in records:
            st["duplicate"] += 1
        else:
            records[data["id"]] = data
            st["added"] += 1
    return st


def main():
    page = http("GET", URL_PAGE)
    if "error" in page or page.get("status") != 200 or any(b in page["text"] for b in BLOCK_MARKS):
        print(f"::warning::MOPS 清單抓取失敗：{page.get('error') or page.get('status')}，這次不更新。")
        return
    rows = parse_rows(page["text"])
    if not rows:
        print("::warning::MOPS 清單解析不到任何一列（頁面格式可能改了），這次不更新。")
        return

    records, log = {}, []
    if fe.OUT.exists():
        prev = json.loads(fe.OUT.read_text(encoding="utf-8"))
        log = prev.get("log", [])
        records = {r["id"]: r for r in prev.get("records", [])}
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}

    st = process(rows, fetch_detail, records, seen)
    cutoff = (datetime.now(fe.TW) - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    seen_new = {k: v for k, v in seen.items() if v >= cutoff}
    print(f"清單 {len(rows)} 列，財報候選 {st['candidates']}，先前已看過 {st['seen']}，"
          f"本次查詢 {st['queried']}，新增 {st['added']}，非財報公告 {st['not_earnings']}，"
          f"重複 {st['duplicate']}，解析失敗 {len(st['parse_fail'])}，錯誤 {st['errors']}")
    for e in st["parse_fail"]:
        print(f"[解析失敗] {e}")
    if st["blocked"]:
        print("::warning::MOPS 回傳阻擋訊息，已停止，下次執行再繼續。")

    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen_new, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if st["added"] or st["blocked"] or st["errors"] or st["parse_fail"]:
        now = datetime.now(fe.TW).strftime("%Y-%m-%d %H:%M")
        if st["added"]:
            fe.add_single_quarter(records)
        log.append({"run_at": now, "sources": [{
            "source": "MOPS即時重訊", "rows": len(rows), "added": st["added"],
            "queried": st["queried"], "blocked": st["blocked"], "errors": st["errors"]}]})
        out = {"updated_at": now, "log": log[-60:],
               "records": sorted(records.values(), key=lambda r: r["announced"] or "", reverse=True)}
        fe.OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
