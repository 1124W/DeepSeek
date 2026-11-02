import time
import ccxt
import datetime
import pandas as pd
import sys
import os
import re
from datetime import timedelta
from openai import OpenAI
import httpx
from concurrent.futures import ThreadPoolExecutor
import warnings

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "在此填入你的 DeepSeek API Key")
TARGET_COINS = ['BTC/USDT', 'ETH/USDT']
DATA_FILENAME = "strategy_data.csv"
LESSON_LOG = "ai_lessons.txt"  # 自学习存储文件

# 香港服务器直连，无需代理
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    http_client=httpx.Client(timeout=60.0),
)

# 币安合约交易所（直连，无代理）
exchange = ccxt.binanceusdm({
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {'defaultType': 'future', 'adjustForTimeDifference': True}
})
exchange.urls['api']['public'] = 'https://fapi.binance.com'


# --- 0. 核心修复：CSV 结构标准化 ---

def normalize_csv():
    """检测并修复 CSV 结构，确保 12 列标准结构"""
    if not os.path.exists(DATA_FILENAME):
        return
    try:
        df = pd.read_csv(DATA_FILENAME, on_bad_lines='skip', engine='python')
        if df.empty:
            return

        changed = False
        if 'conf_10m' not in df.columns:
            df['conf_10m'] = df['confidence'] if 'confidence' in df.columns else 60
            changed = True
        if 'conf_30m' not in df.columns:
            df['conf_30m'] = df['confidence'] if 'confidence' in df.columns else 60
            changed = True
        if 'pred_30m' not in df.columns:
            df['pred_30m'] = "N/A"
            changed = True
        if 'backtest_30m' not in df.columns:
            df['backtest_30m'] = "⏳ 待回测"
            changed = True
        if 'backtest_10m' not in df.columns:
            df['backtest_10m'] = df['backtest'] if 'backtest' in df.columns else "⏳ 待回测"
            changed = True

        if changed:
            standard_cols = [
                'time', 'symbol', 'price', 'rsi', 'macd',
                'pred_10m', 'pred_30m', 'conf_10m', 'conf_30m',
                'strategy', 'backtest_10m', 'backtest_30m'
            ]
            for col in standard_cols:
                if col not in df.columns:
                    df[col] = None
            df = df[standard_cols]
            df.to_csv(DATA_FILENAME, index=False, encoding='utf-8-sig')
            print("📊 [系统] 数据库结构已标准化。")
    except Exception as e:
        print(f"⚠️ CSV 修复失败: {e}")


# --- 1. 指标计算升级：引入 EMA 偏离度 (Bias) ---

def calculate_indicators(ohlcv_data):
    """计算 RSI, MACD 以及 价格对 EMA20 的偏离度"""
    if not ohlcv_data or len(ohlcv_data) < 30:
        return None
    df = pd.DataFrame(ohlcv_data, columns=['time', 'open', 'high', 'low', 'close', 'volume'])

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss)))

    # MACD
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp12 - exp26

    # EMA Bias (判断是否超跌：价格偏离均线过远必有反弹)
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['bias'] = ((df['close'] - df['ema20']) / df['ema20']) * 100

    latest = df.iloc[-1]
    return {
        'price': latest['close'],
        'rsi': round(latest['rsi'], 2),
        'macd': round(latest['macd'], 2),
        'bias': round(latest['bias'], 2)
    }


def calculate_fibonacci_levels(ohlcv_1h):
    if not ohlcv_1h or len(ohlcv_1h) < 20:
        return None
    df = pd.DataFrame(ohlcv_1h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    high, low = df['high'].max(), df['low'].min()
    diff = high - low
    return {
        "High (0.0)": round(high, 2),
        "Fib 0.236": round(high - 0.236 * diff, 2),
        "Fib 0.382": round(high - 0.382 * diff, 2),
        "Fib 0.500": round(high - 0.5 * diff, 2),
        "Fib 0.618": round(high - 0.618 * diff, 2),
        "Fib 0.786": round(high - 0.786 * diff, 2),
        "Low (1.0)": round(low, 2)
    }


# --- 2. 自学习反思引擎升级：检测连续预测偏见 ---

def get_learned_lessons():
    if not os.path.exists(LESSON_LOG):
        return "暂无历史教训。"
    try:
        with open(LESSON_LOG, "r", encoding="utf-8") as f:
            content = f.read().splitlines()
            return "\n".join(content[-30:])
    except:
        return "教训读取异常。"


def perform_self_reflection():
    """检测连续预测失败，防止 AI 陷入死循环看跌/看涨"""
    if not os.path.exists(DATA_FILENAME):
        return
    try:
        df = pd.read_csv(DATA_FILENAME, on_bad_lines='skip', engine='python')
        recent = df.tail(10)

        # 统计最近 10 条里，看跌失败和看涨失败的次数
        bear_fails = recent[(recent['pred_30m'] == '看跌') & (recent['backtest_30m'].str.contains('❌', na=False))]
        bull_fails = recent[(recent['pred_30m'] == '看涨') & (recent['backtest_30m'].str.contains('❌', na=False))]

        bias_warning = ""
        if len(bear_fails) >= 3:
            bias_warning = "【偏见警报】你最近连续看跌失败！市场可能已在强支撑位企稳。即便1h趋势向下，若Bias偏低且RSI超卖，严禁继续盲目空头预测！"
        elif len(bull_fails) >= 3:
            bias_warning = "【偏见警报】你最近连续看涨失败！说明上方阻力极强。严禁在指标高位时盲目看涨，必须寻找衰竭信号！"

        # 原有的 AI 自动反思逻辑
        failed_cases = recent[recent['backtest_10m'].str.contains('❌', na=False)].tail(2)
        if not failed_cases.empty:
            cases_str = failed_cases[['time', 'symbol', 'strategy', 'backtest_10m']].to_string()
            prompt = f"分析失败原因并给出一句核心警告：\n{cases_str}\n额外关注：{bias_warning}"
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            lesson = resp.choices[0].message.content

            with open(LESSON_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%m-%d %H:%M')}] {lesson}\n")
                if bias_warning: f.write(f"[{datetime.datetime.now().strftime('%m-%d %H:%M')}] {bias_warning}\n")
            print("🧠 AI 已完成自反思并检测了预测偏见。")
    except Exception as e:
        print(f"⚠️ 自学习环节出错: {e}")


# --- 3. 核心预测引擎：重构逻辑指令集 ---

def get_ai_analysis(coin, tech_5m, tech_1h, fib_levels, lessons):
    """集成 Bias 指标与反偏见逻辑的决策系统"""
    curr_price = tech_5m['price']
    fib_context = "\n".join([f"{k}: {v}" for k, v in fib_levels.items()])

    prompt = f"""
    [当前币种]: {coin} | 市价: {curr_price}
    [斐波那契位]:
    {fib_context}
    [技术指标]:
    1h级别: RSI {tech_1h['rsi']}, MACD {tech_1h['macd']}, 偏离度(Bias) {tech_1h['bias']}%
    5m级别: RSI {tech_5m['rsi']}, MACD {tech_5m['macd']}, 偏离度(Bias) {tech_5m['bias']}%
    [历史反思]:
    {lessons}

    [任务决策指令]:
    1. **趋势 vs 衰竭**: 虽然1h MACD为负说明大趋势跌，但如果 1h RSI < 32 且 Bias < -1.5%，说明空头动能已极度衰竭，存在“超跌反弹”需求。此时【禁止】盲目预测看跌！
    2. **关键支撑**: 如果价格处于 Low(1.0) 或 Fib 0.786 附近，不要因为 MACD 还没转正就机械看跌，需寻找 5m 底背离信号。
    3. **均值回归**: 偏离度(Bias)是核心。如果 Bias 绝对值 > 2%，价格大概率会向 EMA20 回归。
    4. **对抗偏见**: 检查之前的教训。如果你最近连续看跌失败，说明你的空头思维太重，请重新审视多头支撑。

    [输出格式要求]:
    [10min预测]:看涨/看跌, [10min信心]:xx%, [30min预测]:看涨/看跌, [30min信心]:xx%, [分析依据]:(必须说明是否观察到动能衰竭或均值回归，30字内)
    """

    try:
        resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
                                              temperature=0.3)
        txt = resp.choices[0].message.content
    except Exception as e:
        txt = "[10min预测]:看跌, [10min信心]:50%, [30min预测]:看跌, [30min信心]:50%, [分析依据]:API异常"

    # 正则提取
    def extract_val(reg, default):
        res = re.search(reg, txt)
        return res.group(1) if res else default

    p10 = extract_val(r"10min预测\]:?\s*(看涨|看跌)", "看跌")
    p30 = extract_val(r"30min预测\]:?\s*(看涨|看跌)", "看跌")
    c10 = int(extract_val(r"10min信心\]:?\s*(\d+)", "60"))
    c30 = int(extract_val(r"30min信心\]:?\s*(\d+)", "60"))
    logic = extract_val(r"分析依据\]:?\s*(.*)", "综合判断").strip()

    return {
        'coin': coin, 'price': curr_price, 'p10': p10, 'p30': p30,
        'c10': c10, 'c30': c30, 'logic': logic, 'tech': tech_5m
    }


# --- 4. 辅助函数与回测逻辑 ---

def get_ohlcv_data(symbol, timeframe='5m', limit=100):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        print(f"❌ {symbol} K线获取失败: {e}")
        return None


def update_backtest_results(current_prices):
    if not os.path.exists(DATA_FILENAME):
        return
    try:
        df = pd.read_csv(DATA_FILENAME, on_bad_lines='skip', engine='python')
        if df.empty: return
        changed = False
        now = datetime.datetime.now()
        for idx, row in df.iterrows():
            coin = row['symbol']
            if coin not in current_prices: continue
            curr_p, old_p = float(current_prices[coin]), float(row['price'])
            try:
                row_time = datetime.datetime.strptime(str(row['time']), '%Y-%m-%d %H:%M:%S')
            except:
                continue
            diff = (now - row_time).total_seconds() / 60

            # 10min 回测 (波动 0.02% 为阈值)
            if "⏳" in str(row['backtest_10m']) and diff >= 9:
                real = "看涨" if curr_p > old_p else "看跌"
                win = (row['pred_10m'] == real) or (abs(curr_p - old_p) / old_p < 0.0002)
                df.at[idx, 'backtest_10m'] = f"{'✅' if win else '❌'} {row['pred_10m']} vs {real}"
                changed = True
            # 30min 回测
            if "⏳" in str(row.get('backtest_30m', '⏳')) and diff >= 28:
                real30 = "看涨" if curr_p > old_p else "看跌"
                win30 = (row['pred_30m'] == real30) or (abs(curr_p - old_p) / old_p * 100 < 0.04)
                df.at[idx, 'backtest_30m'] = f"{'✅' if win30 else '❌'} {row['pred_30m']} vs {real30}"
                changed = True
        if changed:
            df.to_csv(DATA_FILENAME, index=False, encoding='utf-8-sig')
    except Exception as e:
        print(f"⚠️ 更新回测出错: {e}")


# --- 5. 主循环控制 ---

def process_one_round():
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n🚀 [{now_str}] 智能自学习策略扫描开始...")

    normalize_csv()

    # A. 获取实时价格并更新回测
    current_prices = {}
    for coin in TARGET_COINS:
        try:
            ticker = exchange.fetch_ticker(coin)
            current_prices[coin] = ticker['last']
        except:
            continue

    if current_prices:
        update_backtest_results(current_prices)
        perform_self_reflection()

    # B. 获取历史经验教训
    lessons = get_learned_lessons()

    # C. 多线程执行预测
    def task(coin):
        try:
            o5 = get_ohlcv_data(coin, '5m', 100)
            o1 = get_ohlcv_data(coin, '1h', 100)
            if o5 and o1:
                fib = calculate_fibonacci_levels(o1)
                i5, i1 = calculate_indicators(o5), calculate_indicators(o1)
                return get_ai_analysis(coin, i5, i1, fib, lessons)
        except Exception as e:
            print(f"❌ {coin} 分析错误: {e}")
        return None

    with ThreadPoolExecutor(max_workers=2) as exe:
        results = [r for r in [exe.submit(task, c).result() for c in TARGET_COINS] if r]

    # D. 写入 CSV 数据库
    for res in results:
        new_row = {
            'time': now_str,
            'symbol': res['coin'],
            'price': res['price'],
            'rsi': res['tech']['rsi'],
            'macd': res['tech']['macd'],
            'pred_10m': res['p10'],
            'pred_30m': res['p30'],
            'conf_10m': res['c10'],
            'conf_30m': res['c30'],
            'strategy': res['logic'],
            'backtest_10m': "⏳ 待回测",
            'backtest_30m': "⏳ 待回测"
        }
        pd.DataFrame([new_row]).to_csv(DATA_FILENAME, mode='a', header=not os.path.exists(DATA_FILENAME), index=False,
                                       encoding='utf-8-sig')
        print(f"   💾 {res['coin']} 决策完成 (Bias: {res['tech']['bias']}% | 30m:{res['p30']})")


if __name__ == "__main__":
    # 禁用 stdout / stderr 缓冲区，确保日志能实时写入 nohup.out / systemd journal
    sys.stdout.reconfigure(line_buffering=True)
    try:
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    print("=== QinliFriend 顶级自进化交易系统启动 (香港直连模式) ===")
    process_one_round()
    while True:
        now = datetime.datetime.now()
        wait_sec = (10 - (now.minute % 10)) * 60 - now.second
        if wait_sec <= 0: wait_sec = 600

        for i in range(wait_sec, 0, -1):
            sys.stdout.write(f"\r⏳ 下次分析倒计时: {i // 60:02d}:{i % 60:02d} ")
            sys.stdout.flush()
            time.sleep(1)

        try:
            process_one_round()
        except Exception as e:
            print(f"主循环异常: {e}")
            time.sleep(10)
