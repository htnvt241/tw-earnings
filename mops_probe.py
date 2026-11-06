#!/usr/bin/env python3
"""MOPS 連線測試：只發少量請求，回報 GitHub 的機器連得到公開資訊觀測站嗎、拿到什麼內容。
結果會寫在該次執行頁面的 Summary（摘要）裡，不會修改任何資料。只用標準函式庫。"""
import html
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
POST_GUESS = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "TYPEK": "all"}
TESTS = [
    ("對照組：證交所 OpenAPI（確認這台機器能上網）", "GET",
     "https://openapi.twse.com.tw/v1/opendata/t187ap04_L", None),
    ("A：你提供的網址（新站頁面）", "GET",
     "https://mopsov.twse.com.tw/mops/web/t05sr01_1", None),
    ("B：舊站頁面", "GET",
     "https://mops.twse.com.tw/mops/web/t05sr01_1", None),
    ("C：新站查詢（參數是推測的）", "POST",
     "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1", POST_GUESS),
    ("D：舊站查詢（參數是推測的）", "POST",
     "https://mops.twse.com.tw/mops/web/ajax_t05sr01_1", POST_GUESS),
]
WARN_WORDS = ["Access Denied", "Forbidden", "Too Many", "過於頻繁", "請稍後", "Cloudflare", "查詢過量"]


def decode(raw):
    return raw.decode("utf-8", errors="replace")


def run(method, url, data):
    hdr = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9",
           "Accept": "text/html,application/json,*/*"}
    body = None
    if data:
        body = urllib.parse.urlencode(data).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
        hdr["Referer"] = url.rsplit("/", 1)[0] + "/t05sr01_1"
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(3_000_000)
            return {"status": r.status, "final_url": r.geturl(), "type": r.headers.get("Content-Type"),
                    "server": r.headers.get("Server"), "bytes": len(raw),
                    "sec": round(time.time() - t0, 1), "text": decode(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read(20000)
        return {"status": e.code, "final_url": url, "type": e.headers.get("Content-Type"),
                "server": e.headers.get("Server"), "bytes": len(raw),
                "sec": round(time.time() - t0, 1), "text": decode(raw)}
    except Exception as e:  # 逾時、連線被拒、憑證問題等
        return {"error": f"{type(e).__name__}: {e}", "sec": round(time.time() - t0, 1), "text": ""}


def analyze(text):
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text)))
    dates = sorted(set(re.findall(r"\b1\d{2}/\d{2}/\d{2}\b", plain)))
    return {
        "date_cells": len(re.findall(r"\b1\d{2}/\d{2}/\d{2}\b", plain)),
        "newest_dates": dates[-3:],
        "finance_mentions": len(re.findall("財務報告|財務報表", plain)),
        "warning_words": [w for w in WARN_WORDS if w.lower() in plain.lower()],
        "preview": plain[:160],
    }


def main():
    out = ["## MOPS 連線測試結果",
           f"執行時間（UTC）：{datetime.now(timezone.utc):%Y-%m-%d %H:%M}", ""]
    for i, (name, method, url, data) in enumerate(TESTS):
        if i:
            time.sleep(3)  # 請求之間留間隔，避免對對方造成負擔
        res = run(method, url, data)
        out.append(f"### {name}")
        out.append(f"- 請求：{method} {url}")
        if "error" in res:
            out.append(f"- 結果：失敗，{res['error']}（耗時 {res['sec']} 秒）")
        else:
            a = analyze(res["text"])
            out += [
                f"- 狀態碼：{res['status']}，大小 {res['bytes']} bytes，耗時 {res['sec']} 秒",
                f"- 內容類型：{res['type']}，伺服器：{res['server']}",
                f"- 轉址後網址：{res['final_url']}",
                f"- 內容中的日期格子數：{a['date_cells']}，最新日期：{', '.join(a['newest_dates']) or '無'}",
                f"- 提到「財務報告／財務報表」的次數：{a['finance_mentions']}",
                f"- 疑似阻擋字眼：{', '.join(a['warning_words']) or '無'}",
                f"- 內容開頭：{a['preview']}",
            ]
        out.append("")
    text = "\n".join(out)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
