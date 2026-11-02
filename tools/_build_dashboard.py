# -*- coding: utf-8 -*-
"""构建离线审计看板：读取 strategy_data.csv -> 生成 backtest_audit.html（数据内嵌）"""
import csv, json, calendar, datetime, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "strategy_data.csv")
TPL = os.path.join(BASE, "_template.html")
OUT = os.path.join(BASE, "回测审计台.html")

def bt_code(s):
    s = (s or "").strip()
    if s.startswith("✅"): return 1
    if s.startswith("❌"): return 0
    return -1

def main():
    rows, syms = [], []
    for r in csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")):
        try:
            t = datetime.datetime.strptime(r["time"].strip(), "%Y-%m-%d %H:%M:%S")
            ts = calendar.timegm(t.timetuple())
            sym = r["symbol"].strip()
            if sym not in syms: syms.append(sym)
            rows.append([
                ts, syms.index(sym), round(float(r["price"]), 4),
                1 if "看涨" in r["pred_10m"] else 0,
                1 if "看涨" in (r["pred_30m"] or "") else 0,
                int(float(r["conf_10m"] or 60)), int(float(r["conf_30m"] or 60)),
                bt_code(r["backtest_10m"]), bt_code(r["backtest_30m"]),
                (r["strategy"] or "")[:60],
                round(float(r["rsi"]), 1) if (r["rsi"] or "").strip() else None,
                round(float(r["macd"]), 2) if (r["macd"] or "").strip() else None,
            ])
        except Exception:
            continue
    rows.sort(key=lambda x: x[0])

    meta = {
        "syms": syms,
        "source": "strategy_data.csv（服务器 /opt/deepseek）",
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(rows),
    }
    tpl = open(TPL, encoding="utf-8").read()
    html = tpl.replace("/*__ROWS__*/[]", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("/*__META__*/{}", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(OUT) / 1024 / 1024
    print(f"行数: {len(rows)}  币种: {syms}")
    print(f"输出: {OUT}")
    print(f"大小: {size:.2f} MB")
    t0 = datetime.datetime.utcfromtimestamp(rows[0][0]); t1 = datetime.datetime.utcfromtimestamp(rows[-1][0])
    print(f"区间(UTC=数据本地时间): {t0} ~ {t1}")

if __name__ == "__main__":
    main()
