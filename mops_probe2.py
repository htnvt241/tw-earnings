#!/usr/bin/env python3
"""MOPS 連線測試（第二版）：用從 mops2.js 讀到的真實參數，測試
1) 分市場（上市／上櫃）的清單查詢  2) 幾筆財報公告的詳細內容查詢。
總共只發 6 個請求，每個之間隔 3 秒。原始回應存在 probe_out/，摘要寫在 Summary。
不會修改任何資料。只用標準函式庫。"""
import html
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://mopsov.twse.com.tw"
URL_PAGE = BASE + "/mops/web/t05sr01_1"
URL_AJAX = BASE + "/mops/web/ajax_t05sr01_1"
OUT = Path("probe_out")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ROW = re.compile(r"<tr class='(?:even|odd)'>(.*?)</tr>", re.S)
FIN = re.compile("財務報告|財務報表")
DETAIL_ORDER = ["TYPEK", "step", "skey", "hhc_co_name", "firstin", "COMPANY_ID",
                "COMPANY_NAME", "SPOKE_DATE", "SPOKE_TIME", "SEQ_NO"]


def http(method, url, fields=None):
    """fields 會照順序送出，並在最前面加上 encodeURIComponent=1（跟網頁的 ajax1 一樣）。"""
    hdr = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9", "Accept": "text/html,*/*"}
    body = None
    if fields is not None:
        pairs = [("encodeURIComponent", "1")] + list(fields)
        body = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in pairs).encode()
        hdr.update({"Content-Type": "application/x-www-form-urlencoded",
                    "Referer": URL_PAGE, "Origin": BASE})
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(4_000_000)
            return {"status": r.status, "text": raw.decode("utf-8", errors="replace"),
                    "sec": round(time.time() - t0, 1)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "text": e.read(20000).decode("utf-8", errors="replace"),
                "sec": round(time.time() - t0, 1)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "text": "", "sec": round(time.time() - t0, 1)}


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


def detail_fields(row):
    f = row["fields"]
    vals = {"TYPEK": "all", "step": "1", "firstin": "true", "hhc_co_name": "", "COMPANY_NAME": "", **f}
    return [(k, vals.get(k, "")) for k in DETAIL_ORDER]


def save(name, text):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")


def main():
    out = ["## MOPS 連線測試（第二版）",
           f"執行時間（UTC）：{datetime.now(timezone.utc):%Y-%m-%d %H:%M}", ""]
    out_add = out.append

    # 1) 全體清單（頁面本身）
    page = http("GET", URL_PAGE)
    save("list_all_page.html", page["text"])
    allrows = parse_rows(page["text"])
    out_add(f"### 1. 全體清單（GET 頁面）\n- 狀態 {page.get('status')}，解析到 {len(allrows)} 列"
            f"{'，錯誤：' + page['error'] if 'error' in page else ''}")

    # 2) 分市場清單
    codes = {}
    for key in ("sii", "otc"):
        time.sleep(3)
        r = http("POST", URL_AJAX, [("TYPEK", key), ("step", "0")])
        save(f"list_{key}.html", r["text"])
        rows = parse_rows(r["text"])
        codes[key] = {x["code"] for x in rows}
        out_add(f"### 2. 查詢 TYPEK={key}\n- 狀態 {r.get('status')}，{len(rows)} 列，耗時 {r['sec']} 秒"
                + (f"\n- 內容開頭：{plain(r['text'])[:150]}" if not rows else ""))
    if allrows:
        allc = {x["code"] for x in allrows}
        out_add(f"- 全體清單 {len(allc)} 家：屬上市 {len(allc & codes['sii'])}、屬上櫃 {len(allc & codes['otc'])}、"
                f"都不屬於 {len(allc - codes['sii'] - codes['otc'])}（興櫃或公開發行）")
    out_add("")

    # 3) 詳細內容：挑 3 筆財報公告（最新 2 筆 + 中間 1 筆）
    cand = [x for x in allrows if FIN.search(x["subject"]) and x["fields"]]
    picks = []
    if cand:
        picks = [cand[0], cand[min(1, len(cand) - 1)], cand[len(cand) // 2]]
    seen = set()
    for row in picks:
        if row["fields"].get("skey") in seen:
            continue
        seen.add(row["fields"].get("skey"))
        time.sleep(3)
        r = http("POST", URL_AJAX, detail_fields(row))
        save(f"detail_{row['code']}.html", r["text"])
        p = plain(r["text"])
        i = p.find("營業收入")
        mkt = "上市" if row["code"] in codes.get("sii", ()) else ("上櫃" if row["code"] in codes.get("otc", ()) else "其他")
        out_add(f"### 3. 詳細內容：{row['code']} {row['name']}（{mkt}）{row['date']} {row['time']}\n"
                f"- 主旨：{row['subject']}\n- 狀態 {r.get('status')}，大小 {len(r['text'])} 字元，耗時 {r['sec']} 秒"
                f"{'，錯誤：' + r['error'] if 'error' in r else ''}\n"
                f"- 內容含「營業收入」：{'是' if i >= 0 else '否'}\n"
                f"- 節錄：{p[max(0, i - 120):i + 320] if i >= 0 else p[:250]}")
        out_add("")

    text = "\n".join(out)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
