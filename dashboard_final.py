import pandas as pd
from flask import Flask, jsonify, request
import os
import datetime
from datetime import timedelta
import re

# --- 应用配置 ---
app = Flask(__name__)

# --- 文件路径定义 ---
CSV_FILE = "strategy_data.csv"

# --- 辅助函数 ---

def get_file_mod_time(filepath):
    if not os.path.exists(filepath): return "文件不存在"
    try:
        mod_timestamp = os.path.getmtime(filepath)
        return datetime.datetime.fromtimestamp(mod_timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except Exception: return "无法读取时间"

def calc_single_rate(df, symbol, col_name):
    """
    计算单个币种在指定列的胜率 (适配 ✅ 和 ❌)
    """
    if col_name not in df.columns or df.empty:
        return {"rate": "0.0%", "win": 0, "total": 0, "color": "#7f8c8d"}

    target_df = df[df['symbol'].str.contains(symbol, case=False, na=False)]
    series = target_df[col_name].astype(str)
    valid_trades = series[series.str.contains(r'✅|❌', na=False)]
    total_count = len(valid_trades)

    if total_count == 0:
        return {"rate": "0.0%", "win": 0, "total": 0, "color": "#7f8c8d"}

    win_count = valid_trades.str.contains(r'✅', na=False).sum()
    win_rate = (win_count / total_count) * 100
    color = "#2ecc71" if win_rate >= 50 else "#e74c3c"

    return {
        "rate": f"{win_rate:.1f}%",
        "win": int(win_count),
        "total": int(total_count),
        "color": color
    }


def build_equity_curves(df_full, init_bal=1000, bet_amt=10, payout_rt=0.95,
                        period="10m", symbols=None, mode="separate"):
    """
    计算累计权益(Equity)曲线。

    参数:
      df_full  : 原始 CSV DataFrame（需先经过 time 列解析 / 按时间升序排序）
      init_bal : 初始本金 (USDT)
      bet_amt  : 每笔投注金额 (USDT)
      payout_rt: 赔率倍率 (盈: bet*payout_rt; 亏: bet)
      period   : "10m" 或 "30m"
      symbols  : ["BTC","ETH"] 或 None
      mode     : "separate"(分别绘制 BTC/ETH 两条) 或 "combined"(合并累计一条 "组合净值")
    返回:
      {"labels": [...], "series": [{"name","data":[...],...}], "summary": {...}}
    """
    try:
        col = f"backtest_{period}"
        if col not in df_full.columns:
            return {"labels": [], "series": [], "summary": {}}

        # 只看已完成回测(✅/❌)的行，且按时间升序
        df = df_full.copy()
        if not pd.api.types.is_datetime64_any_dtype(df['time']):
            if pd.api.types.is_numeric_dtype(df['time']):
                df['time'] = pd.to_datetime(df['time'], unit='ms', errors='coerce')
            else:
                df['time'] = pd.to_datetime(df['time'], errors='coerce')
        df = df.dropna(subset=['time'])
        df = df.sort_values(by='time', ascending=True).reset_index(drop=True)
        df['_valid'] = df[col].astype(str).str.contains(r'✅|❌', na=False)
        df = df[df['_valid']].drop(columns=['_valid']).reset_index(drop=True)

        if symbols is None:
            symbols = ["BTC", "ETH"]

        def sift_rows(sym):
            return df[df['symbol'].str.contains(sym, case=False, na=False)].reset_index(drop=True)

        # 标签：时间字符串
        def labels_from_df(d):
            return [pd.Timestamp(t).strftime('%m-%d %H:%M') for t in d['time'].tolist()]

        def calc_curve(sym_rows):
            """给定某个子序列,计算 (pnl, equity) 序列"""
            if sym_rows.empty:
                return [], [], 0, 0, 0
            wins = sym_rows[col].astype(str).str.contains(r'✅', na=False)
            pnl = [bet_amt * payout_rt if w else -bet_amt for w in wins.tolist()]
            equity = [init_bal]
            for v in pnl:
                equity.append(equity[-1] + v)
            # 画在"交易发生后"，点的数量 = 交易笔数
            eq_points = equity[1:]
            total_pnl = eq_points[-1] - init_bal if eq_points else 0
            win_cnt = int(wins.sum())
            total_cnt = len(wins)
            return eq_points, pnl, total_pnl, win_cnt, total_cnt

        def max_drawdown(equity_series):
            if not equity_series:
                return 0.0
            peak = -float('inf')
            dd_max = 0.0
            for v in equity_series:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0.0
                if dd > dd_max:
                    dd_max = dd
            return dd_max

        # 调色板(同一周期/币种颜色稳定)
        color_map = {
            ("BTC", "10m"): "#3498db",
            ("ETH", "10m"): "#9b59b6",
            ("BTC", "30m"): "#1abc9c",
            ("ETH", "30m"): "#e67e22",
            ("组合", "10m"): "#f1c40f",
            ("组合", "30m"): "#e74c3c",
        }

        series_out = []
        summary = {
            "init_bal": init_bal, "bet_amt": bet_amt, "payout_rt": payout_rt,
            "period": period,
        }

        if mode == "combined":
            # 合并：按时间升序把所有选中币种的盈亏事件流合并（按发生时间交错）
            rows_list = []
            for sym in symbols:
                sr = sift_rows(sym)
                if sr.empty:
                    continue
                wins = sr[col].astype(str).str.contains(r'✅', na=False).tolist()
                for i, (_, row) in enumerate(sr.iterrows()):
                    rows_list.append((row['time'], wins[i]))
            rows_list.sort(key=lambda x: x[0])
            if not rows_list:
                return {"labels": [], "series": [], "summary": summary}
            labels = [pd.Timestamp(t).strftime('%m-%d %H:%M') for t, _ in rows_list]
            pnl = [bet_amt * payout_rt if w else -bet_amt for _, w in rows_list]
            eq = [init_bal]
            for v in pnl:
                eq.append(eq[-1] + v)
            eq_points = eq[1:]
            total_pnl = eq_points[-1] - init_bal if eq_points else 0
            win_cnt = sum(1 for _, w in rows_list if w)
            total_cnt = len(rows_list)
            wr = (win_cnt / total_cnt * 100) if total_cnt > 0 else 0.0
            mdd = max_drawdown(eq_points)
            final_eq = eq_points[-1] if eq_points else init_bal
            series_out.append({
                "name": "组合净值",
                "color": color_map.get(("组合", period), "#3498db"),
                "data": eq_points,
            })
            summary["final_equity"] = round(final_eq, 2)
            summary["total_pnl"] = round(total_pnl, 2)
            summary["total_return_pct"] = round((total_pnl / init_bal) * 100, 2) if init_bal > 0 else 0.0
            summary["win_rate_pct"] = round(wr, 2)
            summary["win"] = win_cnt
            summary["total"] = total_cnt
            summary["max_drawdown_pct"] = round(mdd, 2)
            summary["labels"] = labels
            return {"labels": labels, "series": series_out, "summary": summary}

        # mode == "separate": 每个币种一条独立曲线，X 轴用"第几笔交易"(避免因为币种交易笔数不同、时间不同步导致 X 长度不一致)
        #   - 为了让多币种对比有意义，这里统一以"交易序号 1..N"作为 label；summary 里每币种单独给一份
        per_symbol_summary = {}
        all_lens = []
        for sym in symbols:
            sr = sift_rows(sym)
            eq_points, _, total_pnl, win_cnt, total_cnt = calc_curve(sr)
            all_lens.append(len(eq_points))
            mdd = max_drawdown(eq_points)
            final_eq = eq_points[-1] if eq_points else init_bal
            wr = (win_cnt / total_cnt * 100) if total_cnt > 0 else 0.0
            key = sym.upper()
            per_symbol_summary[key] = {
                "final_equity": round(final_eq, 2),
                "total_pnl": round(total_pnl, 2),
                "total_return_pct": round((total_pnl / init_bal) * 100, 2) if init_bal > 0 else 0.0,
                "win_rate_pct": round(wr, 2),
                "win": win_cnt,
                "total": total_cnt,
                "max_drawdown_pct": round(mdd, 2),
            }
            series_out.append({
                "name": f"{key} {period}",
                "color": color_map.get((key, period), "#3498db"),
                "data": eq_points,
            })

        # label = 交易序号(以最长那条为上限,不够的用空?不用,Chart.js 自动按数组长度截断/补齐)
        max_len = max(all_lens) if all_lens else 0
        labels = [f"#{i+1}" for i in range(max_len)]

        summary["per_symbol"] = per_symbol_summary
        summary["labels"] = labels
        return {"labels": labels, "series": series_out, "summary": summary}

    except Exception as e:
        return {"labels": [], "series": [], "summary": {"error": str(e)}}


def process_data():
    if not os.path.exists(CSV_FILE): return None, None, {}, "错误：找不到 strategy_data.csv 文件。"
    try:
        df_full = pd.read_csv(CSV_FILE, engine='python', encoding='utf-8-sig', on_bad_lines='skip')
        if df_full.empty: return pd.DataFrame(), None, {}, None

        df_full.columns = df_full.columns.str.strip()
        if 'backtest_10m' not in df_full.columns and 'backtest' in df_full.columns:
            df_full['backtest_10m'] = df_full['backtest']
        for col in ['backtest_10m', 'backtest_30m']:
            if col not in df_full.columns:
                df_full[col] = "⏳ 待回测"

        stats = {
            "btc_10m": calc_single_rate(df_full, "BTC", "backtest_10m"),
            "btc_30m": calc_single_rate(df_full, "BTC", "backtest_30m"),
            "eth_10m": calc_single_rate(df_full, "ETH", "backtest_10m"),
            "eth_30m": calc_single_rate(df_full, "ETH", "backtest_30m"),
        }

        # 解析 time 供后面显示/收益曲线复用
        try:
            if pd.api.types.is_numeric_dtype(df_full['time']):
                 df_full['time'] = pd.to_datetime(df_full['time'], unit='ms')
            else:
                 df_full['time'] = pd.to_datetime(df_full['time'], errors='coerce')
        except:
            pass

        # 表格展示：倒序
        if pd.api.types.is_datetime64_any_dtype(df_full['time']):
            df_display = df_full.sort_values(by='time', ascending=False).copy()
            df_display['time'] = df_display['time'].dt.strftime('%Y-%m-%d %H:%M')
        else:
            df_display = df_full.iloc[::-1].copy()

        target_cols = ["time", "symbol", "price", "pred_10m", "backtest_10m", "pred_30m", "backtest_30m"]
        final_cols = [c for c in target_cols if c in df_display.columns]
        result_df = df_display[final_cols]

        # 传原始(已解析 time)的 df_full 用于收益曲线计算
        return result_df, df_full, stats, None

    except Exception as e:
        return pd.DataFrame(), None, {}, f"数据处理出错: {str(e)}"


def _parse_bool(s, default=False):
    if s is None: return default
    return str(s).lower() in ("1","true","yes","on")

# --- Flask 路由 ---

@app.route('/api/data')
def api_data():
    df, df_equity_src, stats, error = process_data()

    # 解析曲线参数
    try:
        init_bal = float(request.args.get('init_bal', 1000))
    except Exception:
        init_bal = 1000
    try:
        bet_amt = float(request.args.get('bet_amt', 10))
    except Exception:
        bet_amt = 10
    try:
        payout_rt = float(request.args.get('payout_rt', 0.95))
    except Exception:
        payout_rt = 0.95
    period = request.args.get('period', '10m')
    if period not in ("10m", "30m"):
        period = "10m"

    symbols_arg = request.args.get('symbols')
    if symbols_arg:
        symbols = [s.strip() for s in symbols_arg.split(',') if s.strip()]
    else:
        symbols = ["BTC", "ETH"]

    mode = request.args.get('mode', 'separate')
    if mode not in ("separate", "combined"):
        mode = "separate"

    equity = {"labels": [], "series": [], "summary": {}}
    if df_equity_src is not None and error is None:
        equity = build_equity_curves(
            df_equity_src, init_bal=init_bal, bet_amt=bet_amt, payout_rt=payout_rt,
            period=period, symbols=symbols, mode=mode
        )

    if error and df.empty:
        return jsonify(error=error, stats=stats, columns=[], equity=equity)

    records = df.fillna('').astype(str).to_dict(orient='records')
    columns = list(df.columns)
    mod_time = get_file_mod_time(CSV_FILE)

    return jsonify(
        records=records, columns=columns, mod_time=mod_time,
        stats=stats, total=len(records), equity=equity
    )


# --- 前端模板 (普通字符串，大括号无需转义) ---
HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>策略监控面板</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js"></script>
    <style>
        :root {
            --bg-body: #f4f7fa; --bg-card: #ffffff;
            --text-main: #2c3e50; --text-sub: #7f8c8d;
            --border: #e1e4e8; --accent: #3498db;
            --success-bg: #e6fffa; --success-text: #276749; --success-border: #b2f5ea;
            --danger-bg: #fff5f5; --danger-text: #c53030; --danger-border: #fed7d7;
            --wait-bg: #edf2f7; --wait-text: #4a5568;
            --grid-color: rgba(44,62,80,0.08);
        }
        html[data-theme='dark'] {
            --bg-body: #121212; --bg-card: #1e1e1e;
            --text-main: #e0e0e0; --text-sub: #a0a0a0;
            --border: #333;
            --success-bg: #1c3329; --success-text: #68d391; --success-border: #276749;
            --danger-bg: #3b181a; --danger-text: #fc8181; --danger-border: #742a2a;
            --wait-bg: #2d3748; --wait-text: #cbd5e0;
            --grid-color: rgba(255,255,255,0.08);
        }
        body { font-family: 'Inter', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; padding: 20px; }
        .container { max-width: 1300px; margin: 0 auto; }

        /* 顶部栏 */
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 20px; font-weight: 700; color: var(--text-main); display: flex; align-items: center; gap: 10px; }
        .btn { background: var(--bg-card); border: 1px solid var(--border); color: var(--text-main); padding: 6px 12px; border-radius: 6px; cursor: pointer; transition: 0.2s; }
        .btn:hover { border-color: var(--accent); color: var(--accent); }

        /* 胜率统计卡片 */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .stat-card { background: var(--bg-card); padding: 20px; border-radius: 10px; border: 1px solid var(--border); box-shadow: 0 2px 5px rgba(0,0,0,0.02); display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
        .stat-title { font-size: 12px; font-weight: 600; color: var(--text-sub); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }
        .stat-value { font-size: 32px; font-weight: 800; line-height: 1.2; }
        .stat-sub { font-size: 12px; color: var(--text-sub); margin-top: 5px; background: var(--wait-bg); padding: 2px 8px; border-radius: 10px; }

        /* 权益曲线卡片 */
        .chart-card { background: var(--bg-card); padding: 20px; border-radius: 10px; border: 1px solid var(--border); margin-bottom: 25px; }
        .chart-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 10px; }
        .chart-card-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .chart-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .chart-controls label { font-size: 12px; color: var(--text-sub); display: inline-flex; align-items: center; gap: 4px; background: var(--wait-bg); padding: 4px 10px; border-radius: 6px; cursor: pointer; user-select: none; }
        .chart-controls input[type="checkbox"], .chart-controls input[type="radio"] { cursor: pointer; accent-color: var(--accent); }
        .chart-controls .input-num { width: 84px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-body); color: var(--text-main); font-size: 13px; }
        .chart-controls select { padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-body); color: var(--text-main); font-size: 13px; }

        /* 权益指标条 */
        .equity-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
        .eq-metric { background: var(--bg-body); padding: 10px 12px; border-radius: 8px; }
        .eq-metric-label { font-size: 11px; color: var(--text-sub); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
        .eq-metric-value { font-size: 16px; font-weight: 700; }
        .eq-per-sub { font-size: 11px; color: var(--text-sub); margin-top: 2px; }

        .chart-wrapper { position: relative; width: 100%; height: 360px; }

        /* 主体内容区 */
        .main-content { background: var(--bg-card); border-radius: 10px; border: 1px solid var(--border); overflow: hidden; }

        /* 工具栏 */
        .toolbar { padding: 15px; border-bottom: 1px solid var(--border); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: var(--bg-card); }
        .search-input { padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-body); color: var(--text-main); outline: none; }

        /* 表格样式 */
        .table-wrapper { overflow-x: auto; max-height: 70vh; }
        .styled-table { width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }
        .styled-table th { background: var(--bg-body); color: var(--text-sub); font-weight: 600; padding: 12px 15px; text-align: left; position: sticky; top: 0; z-index: 10; border-bottom: 1px solid var(--border); }
        .styled-table td { padding: 10px 15px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        .styled-table tr:hover td { background: var(--wait-bg); }

        /* 结果标签 Badge */
        .badge { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 6px; font-weight: 600; font-size: 12px; border: 1px solid transparent; white-space: nowrap; }
        .badge-win { background: var(--success-bg); color: var(--success-text); border-color: var(--success-border); }
        .badge-loss { background: var(--danger-bg); color: var(--danger-text); border-color: var(--danger-border); }
        .badge-wait { background: var(--wait-bg); color: var(--wait-text); }

        /* 分页 */
        .pagination { display: flex; justify-content: center; align-items: center; gap: 6px; padding: 15px; flex-wrap: wrap; border-top: 1px solid var(--border); }
        .page-btn { min-width: 36px; height: 36px; padding: 0 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-card); color: var(--text-main); cursor: pointer; font-size: 13px; }
        .page-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
        .page-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
        .page-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .page-ellipsis { padding: 0 4px; color: var(--text-sub); user-select: none; }
        .page-info { font-size: 12px; color: var(--text-sub); margin-left: 8px; }

        .footer { padding: 15px; text-align: center; font-size: 12px; color: var(--text-sub); border-top: 1px solid var(--border); }
        .empty-tip { padding: 40px; text-align: center; color: var(--text-sub); }
    </style>
</head>
<body>
    <div class="container">
        <div class="top-bar">
            <h1>📊 策略回测监控 <span style="font-size:12px; color:var(--text-sub); margin-left:10px; font-weight:400">DeepSeek Data Analysis</span></h1>
            <button class="btn" onclick="toggleTheme()">🌓 切换模式</button>
        </div>

        <!-- 统计区域 -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-title">BTC 10min 胜率</div>
                <div class="stat-value" id="btc-10m-val">--%</div>
                <div class="stat-sub" id="btc-10m-detail">加载中...</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">BTC 30min 胜率</div>
                <div class="stat-value" id="btc-30m-val">--%</div>
                <div class="stat-sub" id="btc-30m-detail">加载中...</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">ETH 10min 胜率</div>
                <div class="stat-value" id="eth-10m-val">--%</div>
                <div class="stat-sub" id="eth-10m-detail">加载中...</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">ETH 30min 胜率</div>
                <div class="stat-value" id="eth-30m-val">--%</div>
                <div class="stat-sub" id="eth-30m-detail">加载中...</div>
            </div>
        </div>

        <!-- 权益曲线 -->
        <div class="chart-card">
            <div class="chart-card-header">
                <div class="chart-card-title">📈 累计收益曲线 <span style="font-weight:400; font-size:12px; color:var(--text-sub)">(逐笔累计, ✅=+bet×payout, ❌=-bet)</span></div>
            </div>
            <div class="chart-controls">
                <label><input type="checkbox" id="sym-btc" checked> BTC</label>
                <label><input type="checkbox" id="sym-eth" checked> ETH</label>
                <span style="width:1px; height:20px; background:var(--border); margin: 0 4px;"></span>
                <label><input type="radio" name="period" value="10m" checked> 10min</label>
                <label><input type="radio" name="period" value="30m"> 30min</label>
                <span style="width:1px; height:20px; background:var(--border); margin: 0 4px;"></span>
                <label>
                    模式
                    <select id="eq-mode" style="margin-left:4px; padding:4px 6px; border-radius:4px; border:1px solid var(--border); background:var(--bg-body); color:var(--text-main); font-size:12px;">
                        <option value="separate">分币种</option>
                        <option value="combined">合并净值</option>
                    </select>
                </label>
                <span style="width:1px; height:20px; background:var(--border); margin: 0 4px;"></span>
                <label>本金 <input id="eq-init" type="number" class="input-num" value="1000" min="1" step="100"></label>
                <label>投注 <input id="eq-bet" type="number" class="input-num" value="10" min="1" step="1"></label>
                <label>赔率 <input id="eq-payout" type="number" class="input-num" value="0.95" min="0" step="0.05"></label>
            </div>

            <div id="equity-metrics" class="equity-metrics" style="margin-top: 14px;">
                <div class="eq-metric"><div class="eq-metric-label">最终净值</div><div class="eq-metric-value" id="m-final">—</div></div>
                <div class="eq-metric"><div class="eq-metric-label">总盈亏</div><div class="eq-metric-value" id="m-pnl">—</div></div>
                <div class="eq-metric"><div class="eq-metric-label">累计收益率</div><div class="eq-metric-value" id="m-roi">—</div></div>
                <div class="eq-metric"><div class="eq-metric-label">胜率</div><div class="eq-metric-value" id="m-wr">—</div></div>
                <div class="eq-metric"><div class="eq-metric-label">最大回撤</div><div class="eq-metric-value" id="m-mdd">—</div></div>
            </div>

            <div class="chart-wrapper">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="main-content">
            <div class="toolbar">
                <input type="text" id="search" class="search-input" placeholder="🔍 搜索币种..." style="width: 150px;">
                <select id="filter-res" class="search-input">
                    <option value="">全部状态</option>
                    <option value="✅">✅ 盈利</option>
                    <option value="❌">❌ 亏损</option>
                </select>
                <div style="margin-left:auto; font-size:12px; color:var(--text-sub)" id="record-info">共 0 条记录，每页 30 条</div>
            </div>

            <div id="table-container" class="table-wrapper">
                <div class="empty-tip">正在加载数据...</div>
            </div>

            <div id="pagination" class="pagination"></div>

            <div class="footer">
                文件最后更新: <span id="mod-time">__MOD_TIME__</span> | 页面自动刷新: 60秒
            </div>
        </div>
    </div>

    <script>
        // ===== 全局状态 =====
        const PAGE_SIZE = 30;
        let currentPage = 1;
        let allRecords = [];
        let allColumns = [];
        let equityChart = null;
        let lastEquityData = null;

        const COL_LABELS = {
            'time': '时间', 'symbol': '币种', 'price': '价格',
            'pred_10m': '10min预测', 'backtest_10m': '10min回测',
            'pred_30m': '30min预测', 'backtest_30m': '30min回测',
            'rsi': 'RSI', 'macd': 'MACD', 'conf_10m': '10min信心',
            'conf_30m': '30min信心', 'strategy': '策略依据'
        };

        // ===== 主题切换 =====
        function toggleTheme() {
            const html = document.documentElement;
            const current = html.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            html.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
            // 让图表重新渲染以跟随新主题颜色
            if (lastEquityData) renderEquityChart(lastEquityData);
        }
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);

        // ===== 统计数据更新 =====
        function updateStats(stats) {
            if (!stats) return;
            const keys = ['btc_10m', 'btc_30m', 'eth_10m', 'eth_30m'];
            keys.forEach(key => {
                const data = stats[key];
                if (data) {
                    const idBase = key.replace('_', '-');
                    const valElem = document.getElementById(idBase + '-val');
                    const detElem = document.getElementById(idBase + '-detail');
                    valElem.textContent = data.rate;
                    valElem.style.color = data.color;
                    detElem.textContent = `胜 ${data.win} / 总 ${data.total}`;
                }
            });
        }

        // ===== HTML 转义 =====
        function escapeHtml(s) {
            return String(s).replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            }[c]));
        }

        // ===== 构建表格 HTML =====
        function buildTableHTML(records, columns) {
            if (!records.length) return '<div class="empty-tip">暂无数据</div>';
            let html = '<table class="styled-table"><thead><tr>';
            columns.forEach(c => { html += `<th>${escapeHtml(COL_LABELS[c] || c)}</th>`; });
            html += '</tr></thead><tbody>';
            records.forEach(r => {
                html += '<tr>';
                columns.forEach(c => { html += `<td>${escapeHtml(r[c] != null ? r[c] : '')}</td>`; });
                html += '</tr>';
            });
            html += '</tbody></table>';
            return html;
        }

        // ===== 表格美化 =====
        function enhanceTable() {
            const table = document.querySelector('.styled-table');
            if (!table) return;
            const rows = table.querySelectorAll('tbody tr');
            rows.forEach(row => {
                Array.from(row.cells).forEach(cell => {
                    const text = cell.textContent.trim();
                    if (text.includes('✅')) {
                        cell.innerHTML = `<span class="badge badge-win">${text}</span>`;
                    } else if (text.includes('❌')) {
                        cell.innerHTML = `<span class="badge badge-loss">${text}</span>`;
                    } else if (text.includes('待') || text.includes('wait') || text === 'nan' || text === '') {
                        cell.innerHTML = `<span class="badge badge-wait">${text || '—'}</span>`;
                    }
                });
            });
        }

        // ===== 筛选 =====
        function getFiltered() {
            const s = document.getElementById('search').value.toLowerCase().trim();
            const f = document.getElementById('filter-res').value;
            return allRecords.filter(r => {
                const sym = String(r['symbol'] || '').toLowerCase();
                const symOk = !s || sym.includes(s);
                let resOk = true;
                if (f) {
                    const bt = String(r['backtest_10m'] || '') + ' ' + String(r['backtest_30m'] || '');
                    resOk = bt.includes(f);
                }
                return symOk && resOk;
            });
        }

        // ===== 分页导航 =====
        function renderPagination(cur, total) {
            const pag = document.getElementById('pagination');
            if (total <= 1) { pag.innerHTML = ''; return; }
            const delta = 2;
            const set = new Set([1, total, cur, cur - 1, cur - 2, cur + 1, cur + 2]);
            const pages = [...set].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
            let html = '';
            html += `<button class="page-btn" data-page="${cur - 1}" ${cur === 1 ? 'disabled' : ''}>‹ 上一页</button>`;
            let prev = 0;
            pages.forEach(p => {
                if (p - prev > 1) html += `<span class="page-ellipsis">…</span>`;
                html += `<button class="page-btn ${p === cur ? 'active' : ''}" data-page="${p}">${p}</button>`;
                prev = p;
            });
            html += `<button class="page-btn" data-page="${cur + 1}" ${cur === total ? 'disabled' : ''}>下一页 ›</button>`;
            html += `<span class="page-info">第 ${cur} / ${total} 页</span>`;
            pag.innerHTML = html;
            pag.querySelectorAll('.page-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    if (btn.disabled) return;
                    renderTable(parseInt(btn.dataset.page, 10));
                });
            });
        }

        // ===== 渲染当前页 =====
        function renderTable(page) {
            const filtered = getFiltered();
            const total = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
            if (page > total) page = total;
            if (page < 1) page = 1;
            currentPage = page;
            const start = (page - 1) * PAGE_SIZE;
            const pageRecords = filtered.slice(start, start + PAGE_SIZE);
            document.getElementById('table-container').innerHTML = buildTableHTML(pageRecords, allColumns);
            enhanceTable();
            renderPagination(currentPage, total);
            document.getElementById('record-info').textContent =
                `共 ${filtered.length} 条记录，每页 ${PAGE_SIZE} 条，第 ${currentPage}/${total} 页`;
        }

        // ===== 权益曲线 =====
        function getCurrentThemeColors() {
            const dark = document.documentElement.getAttribute('data-theme') === 'dark';
            if (dark) {
                return {
                    text: '#e0e0e0',
                    textSub: '#a0a0a0',
                    grid: 'rgba(255,255,255,0.08)',
                    initLine: 'rgba(255,255,255,0.25)',
                };
            }
            return {
                text: '#2c3e50',
                textSub: '#7f8c8d',
                grid: 'rgba(44,62,80,0.08)',
                initLine: 'rgba(44,62,80,0.2)',
            };
        }

        function formatNum(v, digits=2, prefix='') {
            if (v === undefined || v === null || isNaN(v)) return '—';
            return prefix + Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
        }
        function fmtPct(v) {
            if (v === undefined || v === null || isNaN(v)) return '—';
            const sign = v > 0 ? '+' : '';
            return sign + Number(v).toFixed(2) + '%';
        }

        function renderEquityMetrics(summary, initBal) {
            // 指标卡：合并模式显示一份；分币种模式显示全局所有币种合计？我们按"第一份 summary"显示：
            // 分币种模式下 per_symbol 是 {BTC,ETH}，合并模式下是顶层字段
            const per = summary.per_symbol;
            if (per) {
                // 分币种：把每个币种的指标用 <br>+<span class="eq-per-sub"> 子行显示
                let fin='', pnl='', roi='', wr='', mdd='';
                let first = true;
                for (const sym of Object.keys(per)) {
                    const s = per[sym];
                    const sep = first ? '' : '<br class="eq-per-sub">';
                    const roiColor = s.total_return_pct >= 0 ? 'var(--success-text)' : 'var(--danger-text)';
                    const mddColor = 'var(--danger-text)';
                    fin  += `${sep}<span class="eq-per-sub" style="color:${getCurrentThemeColors().textSub}">${sym}</span> <span>${formatNum(s.final_equity,2)}</span>`;
                    const pnlColor = s.total_pnl >= 0 ? 'var(--success-text)' : 'var(--danger-text)';
                    pnl  += `${sep}<span class="eq-per-sub" style="color:${getCurrentThemeColors().textSub}">${sym}</span> <span style="color:${pnlColor}">${s.total_pnl>=0?'+':''}${formatNum(s.total_pnl,2)}</span>`;
                    roi  += `${sep}<span class="eq-per-sub" style="color:${getCurrentThemeColors().textSub}">${sym}</span> <span style="color:${roiColor}">${fmtPct(s.total_return_pct)}</span>`;
                    wr   += `${sep}<span class="eq-per-sub" style="color:${getCurrentThemeColors().textSub}">${sym}</span> <span>${s.win_rate_pct.toFixed(2)}% <span style="color:${getCurrentThemeColors().textSub};font-weight:400">(${s.win}/${s.total})</span></span>`;
                    mdd  += `${sep}<span class="eq-per-sub" style="color:${getCurrentThemeColors().textSub}">${sym}</span> <span style="color:${mddColor}">-${s.max_drawdown_pct.toFixed(2)}%</span>`;
                    first = false;
                }
                document.getElementById('m-final').innerHTML = fin;
                document.getElementById('m-pnl').innerHTML = pnl;
                document.getElementById('m-roi').innerHTML = roi;
                document.getElementById('m-wr').innerHTML = wr;
                document.getElementById('m-mdd').innerHTML = mdd;
            } else {
                // 合并模式
                const s = summary;
                const fin = s.final_equity, pnl = s.total_pnl, roi = s.total_return_pct, wr = s.win_rate_pct, mdd = s.max_drawdown_pct;
                document.getElementById('m-final').innerHTML = formatNum(fin, 2);
                const pnlColor = (pnl || 0) >= 0 ? 'var(--success-text)' : 'var(--danger-text)';
                document.getElementById('m-pnl').innerHTML = `<span style="color:${pnlColor}">${(pnl||0)>=0?'+':''}${formatNum(pnl,2)}</span>`;
                const roiColor = (roi || 0) >= 0 ? 'var(--success-text)' : 'var(--danger-text)';
                document.getElementById('m-roi').innerHTML = `<span style="color:${roiColor}">${fmtPct(roi)}</span>`;
                document.getElementById('m-wr').innerHTML = `${(wr||0).toFixed(2)}% <span style="color:${getCurrentThemeColors().textSub};font-weight:400">(${s.win||0}/${s.total||0})</span>`;
                document.getElementById('m-mdd').innerHTML = `<span style="color:var(--danger-text)">-${(mdd||0).toFixed(2)}%</span>`;
            }
        }

        function renderEquityChart(equity) {
            lastEquityData = equity;
            const labels = (equity && equity.labels) || [];
            const series = (equity && equity.series) || [];
            const summary = (equity && equity.summary) || {};

            // 读取 init_bal 显示(如果 summary 没提供就取控件值)
            let initBal = summary.init_bal;
            if (initBal === undefined || initBal === null) {
                initBal = parseFloat(document.getElementById('eq-init').value) || 1000;
            }
            renderEquityMetrics(summary, initBal);

            const c = getCurrentThemeColors();
            const datasets = series.map(s => {
                return {
                    label: s.name,
                    data: s.data,
                    borderColor: s.color,
                    backgroundColor: (s.color + '1A'), // 10% 透明填充
                    fill: true,
                    tension: 0.25,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBorderWidth: 2,
                    pointHoverBackgroundColor: s.color,
                    pointHoverBorderColor: '#fff',
                };
            });

            const initLinePlugin = {
                id: 'initLine',
                afterDraw(chart, args, opts) {
                    const {ctx, chartArea, scales} = chart;
                    if (!scales || !scales.y || !chartArea) return;
                    const yVal = scales.y.getPixelForValue(initBal);
                    if (!yVal) return;
                    ctx.save();
                    ctx.beginPath();
                    ctx.setLineDash([5, 5]);
                    ctx.lineWidth = 1;
                    ctx.strokeStyle = c.initLine;
                    ctx.moveTo(chartArea.left, yVal);
                    ctx.lineTo(chartArea.right, yVal);
                    ctx.stroke();
                    ctx.restore();
                }
            };

            const cfg = {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: {
                            position: 'top',
                            align: 'end',
                            labels: {
                                color: c.text,
                                font: { size: 12, weight: '600' },
                                usePointStyle: true,
                                boxWidth: 10,
                                boxHeight: 10,
                                padding: 15,
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(0,0,0,0.8)',
                            titleColor: '#fff',
                            bodyColor: '#fff',
                            padding: 12,
                            cornerRadius: 6,
                            displayColors: true,
                            callbacks: {
                                label: function(ctx) {
                                    const v = ctx.parsed.y;
                                    const diff = v - initBal;
                                    const pct = (diff / initBal) * 100;
                                    const sign = diff >= 0 ? '+' : '';
                                    return `${ctx.dataset.label}: ${v.toFixed(2)}  (${sign}${diff.toFixed(2)} / ${sign}${pct.toFixed(2)}%)`;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            ticks: { color: c.textSub, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                            grid: { color: c.grid }
                        },
                        y: {
                            ticks: { color: c.textSub },
                            grid: { color: c.grid }
                        }
                    }
                },
                plugins: [initLinePlugin],
            };

            if (equityChart) {
                equityChart.data = cfg.data;
                equityChart.options = cfg.options;
                equityChart.config.plugins = [initLinePlugin];
                equityChart.update();
            } else {
                const ctx = document.getElementById('equityChart').getContext('2d');
                equityChart = new Chart(ctx, cfg);
            }
        }

        function buildEquityQueryParams() {
            const btcOn = document.getElementById('sym-btc').checked;
            const ethOn = document.getElementById('sym-eth').checked;
            const syms = [];
            if (btcOn) syms.push('BTC');
            if (ethOn) syms.push('ETH');
            if (!syms.length) syms.push('BTC'); // 至少一个，避免空
            const period = document.querySelector('input[name="period"]:checked').value;
            const mode = document.getElementById('eq-mode').value;
            const initBal = parseFloat(document.getElementById('eq-init').value) || 1000;
            const bet = parseFloat(document.getElementById('eq-bet').value) || 10;
            const payout = parseFloat(document.getElementById('eq-payout').value) || 0.95;
            return new URLSearchParams({
                symbols: syms.join(','),
                period: period,
                mode: mode,
                init_bal: String(initBal),
                bet_amt: String(bet),
                payout_rt: String(payout),
            }).toString();
        }

        // ===== 拉取数据 =====
        async function fetchData(rebuildEquity=true) {
            try {
                const q = buildEquityQueryParams();
                const res = await fetch('/api/data?' + q);
                const data = await res.json();
                document.getElementById('mod-time').textContent = data.mod_time || '';
                updateStats(data.stats);

                const container = document.getElementById('table-container');
                if (data.error) {
                    container.innerHTML = `<div class="empty-tip" style="color:#c53030">${data.error}</div>`;
                    document.getElementById('pagination').innerHTML = '';
                    document.getElementById('record-info').textContent = '共 0 条记录';
                    if (rebuildEquity) renderEquityChart(data.equity || {labels:[],series:[],summary:{}});
                    return;
                }
                allRecords = data.records || [];
                allColumns = data.columns || [];
                if (rebuildEquity) renderEquityChart(data.equity || {labels:[],series:[],summary:{}});
                renderTable(currentPage);
            } catch (e) {
                console.error('Fetch Error:', e);
            }
        }

        // ===== 权益曲线相关控件:任何变动 → 仅重算曲线（不刷表格，避免分页/筛选状态丢失） =====
        function rebuildChartOnly() { fetchData(true); /* full fetch, equity 重算 */ }
        // 优化：把 fetchData 拆成表格刷新 / 权益刷新。为避免代码复杂化，这里我们用同样 fetchData，但保留筛选状态
        // 由于 fetchData 会在渲染表格时用 currentPage，筛选/搜索状态在 getFiltered() 里从 DOM 读，所以不会丢

        ['sym-btc','sym-eth'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => fetchData(true));
        });
        document.querySelectorAll('input[name="period"]').forEach(el => {
            el.addEventListener('change', () => fetchData(true));
        });
        document.getElementById('eq-mode').addEventListener('change', () => fetchData(true));
        ['eq-init','eq-bet','eq-payout'].forEach(id => {
            let t;
            document.getElementById(id).addEventListener('input', () => {
                clearTimeout(t);
                t = setTimeout(() => fetchData(true), 350);
            });
        });

        // ===== 表格相关 =====
        document.getElementById('search').addEventListener('input', () => renderTable(1));
        document.getElementById('filter-res').addEventListener('change', () => renderTable(1));

        // ===== 初始化 =====
        fetchData(true);
        setInterval(() => fetchData(true), 60000); // 1分钟轮询(含曲线)
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    mod_time = get_file_mod_time(CSV_FILE)
    return HTML_TPL.replace('__MOD_TIME__', mod_time)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
