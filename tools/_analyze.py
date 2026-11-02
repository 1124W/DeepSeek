# -*- coding: utf-8 -*-
"""严格回测：用 T+10min / T+30min 的 CSV 行价格还原真实涨跌，并与 main.py 记录结果对比"""
import csv, datetime, collections, statistics

PATH = r"D:\code\lianghua_20261030\strategy_data.csv"
TOL = 2.5  # 分钟，匹配容差

rows = []
for r in csv.DictReader(open(PATH, encoding="utf-8-sig")):
    try:
        t = datetime.datetime.strptime(r["time"].strip(), "%Y-%m-%d %H:%M:%S")
        rows.append({
            "t": t, "sym": r["symbol"], "price": float(r["price"]),
            "p10": r["pred_10m"], "p30": r["pred_30m"],
            "c10": int(float(r["conf_10m"] or 60)), "c30": int(float(r["conf_30m"] or 60)),
            "bt10": r["backtest_10m"], "bt30": r["backtest_30m"],
        })
    except Exception:
        pass

rows.sort(key=lambda x: (x["sym"], x["t"]))

# 每个币种的价格索引（同时间戳去重，取最后一条）
idx = collections.defaultdict(dict)
for r in rows:
    idx[r["sym"]][r["t"]] = r["price"]
for s in idx:
    idx[s] = sorted(idx[s].items())

def price_at(sym, target, tol_min=TOL):
    arr = idx[sym]
    lo, hi = 0, len(arr) - 1
    # 二分找最近
    import bisect
    ts = [a[0] for a in arr] if len(arr) < 3 else None
    # 简单二分
    best = None
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid][0] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    for j in (lo - 1, lo, lo + 1):
        if 0 <= j < len(arr):
            gap = abs((arr[j][0] - target).total_seconds()) / 60
            if gap <= tol_min and (best is None or gap < best[0]):
                best = (gap, arr[j][1])
    return best[1] if best else None

def rec_result(bt):
    if bt.startswith("✅"): return "win"
    if bt.startswith("❌"): return "loss"
    return None

stats = {"A": collections.Counter(), "B": collections.Counter(), "C": collections.Counter()}
sumret = {"10": 0.0, "30": 0.0}
daily = collections.defaultdict(lambda: {"a": [0, 0], "c": [0, 0]})
by_sym = collections.defaultdict(lambda: {"a": [0, 0], "c": [0, 0]})
by_dir = collections.defaultdict(lambda: {"a": [0, 0]})
by_conf = collections.defaultdict(lambda: [0, 0])
n_nodata = 0

for r in rows:
    for horizon, key_p, key_c, key_bt, thr in (("10", "p10", "c10", "bt10", 0.02), ("30", "p30", "c30", "bt30", 0.04)):
        target = r["t"] + datetime.timedelta(minutes=int(horizon))
        p2 = price_at(r["sym"], target)
        if p2 is None:
            n_nodata += 1
            continue
        ret = (p2 - r["price"]) / r["price"] * 100  # 百分比
        real = "看涨" if ret > 0 else ("看跌" if ret < 0 else "平")
        pred = r[key_p]
        # A 严格
        a_win = (pred == real)
        stats["A"][f"{horizon}_win" if a_win else f"{horizon}_loss"] += 1
        # B 原版规则 + 正确价格
        b_win = a_win or abs(ret) < thr
        stats["B"][f"{horizon}_win" if b_win else f"{horizon}_loss"] += 1
        # C main 实际记录
        c = rec_result(r[key_bt])
        if c:
            stats["C"][f"{horizon}_win" if c == "win" else f"{horizon}_loss"] += 1
        if horizon == "10":
            sumret["10"] += ret if pred == "看涨" else -ret
            d = r["t"].strftime("%Y-%m-%d")
            daily[d]["a"][0] += 1 if a_win else 0
            daily[d]["a"][1] += 1
            if c:
                daily[d]["c"][0] += 1 if c == "win" else 0
                daily[d]["c"][1] += 1
            by_sym[r["sym"]]["a"][0] += 1 if a_win else 0
            by_sym[r["sym"]]["a"][1] += 1
            if c:
                by_sym[r["sym"]]["c"][0] += 1 if c == "win" else 0
                by_sym[r["sym"]]["c"][1] += 1
            by_dir[pred]["a"][0] += 1 if a_win else 0
            by_dir[pred]["a"][1] += 1
            b = "50-55" if r["c10"] < 55 else "55-60" if r["c10"] < 60 else "60-65" if r["c10"] < 65 else "65-70" if r["c10"] < 70 else "70-75" if r["c10"] < 75 else "75+"
            by_conf[b][0] += 1 if a_win else 0
            by_conf[b][1] += 1
        else:
            sumret["30"] += ret if pred == "看涨" else -ret

def fmt(k, hor):
    w, l = stats[k].get(f"{hor}_win", 0), stats[k].get(f"{hor}_loss", 0)
    n = w + l
    return f"{100*w/n:.1f}% (n={n})" if n else "n/a"

print("=== 胜率对比 ===")
print("A 严格(point-in-time, 无平局豁免): 10m", fmt("A", "10"), " 30m", fmt("A", "30"))
print("B 原版规则+正确价格        : 10m", fmt("B", "10"), " 30m", fmt("B", "30"))
print("C main.py 实际记录        : 10m", fmt("C", "10"), " 30m", fmt("C", "30"))
print("匹配不到价格的行次数:", n_nodata)
print("\n累计收益(按预测方向, 无手续费): 10m %.1f%%  30m %.1f%%" % (sumret["10"], sumret["30"]))
print("\n分币种(严格10m):")
for s, v in by_sym.items():
    print(f"  {s}: {100*v['a'][0]/v['a'][1]:.1f}% (n={v['a'][1]}) | main记录 {100*v['c'][0]/max(v['c'][1],1):.1f}%")
print("\n分方向(严格10m):")
for s, v in by_dir.items():
    print(f"  {s}: {100*v['a'][0]/v['a'][1]:.1f}% (n={v['a'][1]})")
print("\n分信心(严格10m):")
for s in ["50-55", "55-60", "60-65", "65-70", "70-75", "75+"]:
    v = by_conf.get(s)
    if v: print(f"  {s}: {100*v[0]/v[1]:.1f}% (n={v[1]})")
