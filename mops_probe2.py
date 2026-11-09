#!/usr/bin/env python3
"""MOPS 連線測試（第三版，覆蓋舊的 mops_probe2.py）：
測試「綜合損益彙總表」(t163sb04) 能不能一次查到「目前所有已公告第 3 季財報」的公司，
並檢查之前缺資料的幾家（6929、5529、7819、3150、2755）有沒有在裡面。
只發 3 個請求（1 個讀頁面、上市與上櫃各 1 個查詢），間隔 3 秒。不會修改任何資料。"""
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
PAGE = BASE + "/mops/web/t163sb04"
GUESS_AJAX = BASE + "/mops/web/ajax_t163sb04"
OUT = Path("probe_out")
TARGETS = ["6929", "5529", "7819", "3150", "2755"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http(method, url, pairs=None, referer=PAGE):
    hdr = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9", "Accept": "text/html,*/*"}
    body = None
    if pairs is not None:  # 與網頁的 ajax1 相同：最前面固定加 encodeURIComponent=1
        allp = [("encodeURIComponent", "1")] + list(pairs)
        body = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in allp).encode()
        hdr.update({"Content-Type": "application/x-www-form-urlencoded", "Referer": referer, "Origin": BASE})
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return {"status": r.status, "text": r.read(8_000_000).decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "text": e.read(20000).decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "text": ""}


def plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s))).strip()


def attr(tag, name):
    m = re.search(rf"""\b{name}\s*=\s*(?:'([^']*)'|"([^"]*)"|([^\s>]+))""", tag, re.I)
    return next((g for g in m.groups() if g is not None), "") if m else ""


def parse_forms(text):
    out = []
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.S | re.I):
        fields = []
        for im in re.finditer(r"<input\b([^>]*)>", m.group(2), re.I):
            n = attr(im.group(1), "name")
            if n:
                fields.append((n, attr(im.group(1), "value")))
        out.append({"name": attr(m.group(1), "name"), "action": attr(m.group(1), "action"), "fields": fields})
    return out


def parse_rows(text):
    rows = []
    for m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = [plain(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", m.group(1), re.S | re.I)]
        if cells:
            rows.append(cells)
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    out = ["## MOPS 連線測試（第三版：綜合損益彙總表）",
           f"執行時間（UTC）：{datetime.now(timezone.utc):%Y-%m-%d %H:%M}", ""]
    page = http("GET", PAGE)
    (OUT / "t163sb04_page.html").write_text(page["text"], encoding="utf-8")
    forms = parse_forms(page["text"])
    out.append(f"### 1. 讀取頁面\n- 狀態 {page.get('status')}{'，錯誤：' + page['error'] if 'error' in page else ''}，"
               f"大小 {len(page['text'])} 字元，找到 {len(forms)} 個表單")
    for f in forms[:6]:
        out.append(f"- 表單 `{f['name']}` → action `{f['action']}`，欄位：{', '.join(n + '=' + v for n, v in f['fields'])[:300]}")
    out.append("")

    form = next((f for f in forms if any(n in ("year", "season") for n, _ in f["fields"])
                 or "163sb04" in f["action"]), None)
    for typek in ("sii", "otc"):
        time.sleep(3)
        if form:
            pairs, seen = [], set()
            for n, v in form["fields"]:
                v = {"TYPEK": typek, "year": "115", "season": "03", "step": "1"}.get(n, v)
                pairs.append((n, v))
                seen.add(n)
            for n, v in (("TYPEK", typek), ("year", "115"), ("season", "03")):
                if n not in seen:
                    pairs.append((n, v))
            url = urllib.parse.urljoin(PAGE, form["action"]) if form["action"] else GUESS_AJAX
            how = f"用頁面表單 `{form['name']}` 的欄位"
        else:
            pairs = [("step", "1"), ("firstin", "1"), ("off", "1"), ("TYPEK", typek), ("year", "115"), ("season", "03")]
            url, how = GUESS_AJAX, "找不到表單，用推測的參數"
        r = http("POST", url, pairs)
        (OUT / f"t163sb04_{typek}.html").write_text(r["text"], encoding="utf-8")
        rows = parse_rows(r["text"])
        coded = [c for c in rows if c and re.fullmatch(r"\d{4,6}", c[0])]
        heads = next((c for c in rows if any("營業收入" in x for x in c)), [])
        out.append(f"### 2. 查詢 TYPEK={typek}（{how}）\n- POST {url}\n- 狀態 {r.get('status')}，大小 {len(r['text'])} 字元，"
                   f"表格列 {len(rows)}，公司列 {len(coded)}"
                   f"{'，錯誤：' + r['error'] if 'error' in r else ''}")
        if heads:
            out.append(f"- 表頭：{' | '.join(heads)[:300]}")
        if coded:
            out.append(f"- 範例列：{' | '.join(coded[0])[:300]}")
        else:
            out.append(f"- 內容開頭：{plain(r['text'])[:200]}")
        codes = {c[0]: c for c in coded}
        for t in TARGETS:
            if t in codes:
                out.append(f"- 目標 {t}：有 → {' | '.join(codes[t])[:200]}")
        out.append(f"- 目標公司找到：{[t for t in TARGETS if t in codes] or '無'}")
        out.append("")
    text = "\n".join(out)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
