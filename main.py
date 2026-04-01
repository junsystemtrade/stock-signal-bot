import os
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook
import pytz
import time
import holidays

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

# タイムゾーン
JST = pytz.timezone('Asia/Tokyo')
US_EAST = pytz.timezone('US/Eastern')

# 米国祝日
US_HOLIDAYS = holidays.US()

# =============================
# 共通指標計算
# =============================

def add_indicators(df):
    low_14 = df['Low'].rolling(14).min()
    high_14 = df['High'].rolling(14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14).replace(0, 1))
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']        = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df


def jmia_signal(df):
    oversold = (df['STOCHk'] <= 10) & (df['STOCHd'] <= 10)
    cross_up = (df['STOCHk'].shift(1) < df['STOCHd'].shift(1)) & (df['STOCHk'] > df['STOCHd'])
    vol_ok   = ((df['High'] - df['Low']) / df['Close'].replace(0, 1)) > 0.05
    macd_up  = (df['MACD'].shift(1) < df['MACD_signal'].shift(1)) & (df['MACD'] > df['MACD_signal'])
    return oversold & cross_up & vol_ok & macd_up


def nu_signal(df):
    trend_ok = (df['MA50'] > df['MA200']) & (df['Close'] > df['MA50'])
    pullback = (df['STOCHk'] <= 30) & (df['STOCHk'] > 20)
    cross_up = (df['STOCHk'].shift(1) < df['STOCHd'].shift(1)) & (df['STOCHk'] > df['STOCHd'])
    macd_ok  = df['MACD'] > df['MACD_signal']
    return trend_ok & pullback & cross_up & macd_ok


SIGNAL_CONFIG = {
    'JMIA': {
        'func':       jmia_signal,
        'stop_loss':  -0.07,
        'stoch_exit': 80,
        'trend_exit': False,
    },
    'NU': {
        'func':       nu_signal,
        'stop_loss':  -0.05,
        'stoch_exit': None,
        'trend_exit': True,
    },
}

# =============================
# データ取得
# =============================

def get_stock_data(symbol, date_today_us):
    if date_today_us.weekday() >= 5 or date_today_us in US_HOLIDAYS:
        print(f"Skipping {symbol}, US market closed")
        return None

    filename = f"{symbol}_history.csv"
    for attempt in range(3):
        try:
            df = yf.download(symbol, period='1y', progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.index = pd.to_datetime(df.index)
                df.to_csv(filename)
                return df
            time.sleep(2)
        except Exception as e:
            print(f"Error for {symbol} (attempt {attempt+1}): {e}")
            time.sleep(2)

    if os.path.exists(filename):
        try:
            return pd.read_csv(filename, index_col=0, parse_dates=True)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


# =============================
# エグジット判定
# =============================

def check_exit(symbol, buy_price, current_row):
    cfg = SIGNAL_CONFIG.get(symbol, {})
    current_price = float(current_row['Close'])
    pnl = (current_price - buy_price) / buy_price if buy_price > 0 else 0

    if pnl <= cfg.get('stop_loss', -0.07):
        return True, f"損切り ({pnl*100:.1f}%)"

    stoch_exit = cfg.get('stoch_exit')
    if stoch_exit and float(current_row.get('STOCHk', 0)) >= stoch_exit:
        return True, f"利確 ストキャス過熱 ({pnl*100:.1f}%)"

    if cfg.get('trend_exit') and 'MA50' in current_row.index:
        if current_price < float(current_row['MA50']):
            return True, f"トレンド割れ撤退 ({pnl*100:.1f}%)"

    return False, ""


# =============================
# メイン処理
# =============================

def main():
    print("--- Execution Started ---")

    now_jst   = datetime.datetime.now(JST)
    today_jst = now_jst.date()
    now_us    = now_jst.astimezone(US_EAST)
    today_us  = now_us.date()

    cols = ['Date', 'Symbol', 'Status', 'Buy_Price']
    if os.path.exists(CSV_FILE):
        try:
            trade_log = pd.read_csv(CSV_FILE)
            trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
            trade_log['Status']    = trade_log['Status'].astype(str).str.strip()
        except Exception as e:
            print(f"CSV read error: {e}")
            trade_log = pd.DataFrame(columns=cols)
    else:
        trade_log = pd.DataFrame(columns=cols)

    notifications      = []
    symbol_status      = []
    exit_notifications = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol, today_us)
        if df is None or df.empty:
            symbol_status.append(f"[{symbol}] market closed or data error")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if len(valid_df) < 200:
            symbol_status.append(f"[{symbol}] data insufficient ({len(valid_df)} rows)")
            continue

        valid_df = add_indicators(valid_df)
        valid_df['buy_signal'] = SIGNAL_CONFIG[symbol]['func'](valid_df)

        last_row      = valid_df.tail(1).squeeze()
        last_date_str = valid_df.index[-1].strftime('%Y-%m-%d')
        current_price = float(last_row['Close'])

        # 前日シグナルを始値で約定
        mask_signal = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask_signal.any():
            trade_log.loc[mask_signal, 'Buy_Price'] = float(last_row['Open'])
            trade_log.loc[mask_signal, 'Status']    = 'holding'

        # 保有ポジションのエグジット確認
        holdings_mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')
        if holdings_mask.any():
            for idx in trade_log[holdings_mask].index:
                buy_price = float(trade_log.at[idx, 'Buy_Price'])
                should_exit, reason = check_exit(symbol, buy_price, last_row)
                if should_exit:
                    trade_log.at[idx, 'Status'] = 'closed'
                    exit_notifications.append(f"{symbol} exit: {reason}")

        # 冷却期間チェック（直近7日）
        recent_cutoff = (valid_df.index[-1] - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        recent_trades = trade_log[
            (trade_log['Symbol'] == symbol) &
            (trade_log['Date'] >= recent_cutoff)
        ]
        cooldown_active = not recent_trades.empty

        # 新規シグナル判定
        if bool(last_row['buy_signal']) and not cooldown_active:
            exists = trade_log[
                (trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)
            ].any().any()
            if not exists:
                new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0}
                trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                notifications.append(f"BUY SIGNAL: {symbol} ({last_date_str})")

        # 保有状況集計
        holdings    = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares  = len(holdings)
        current_val = current_price * num_shares
        cost_basis  = holdings['Buy_Price'].sum()
        profit_str  = f"${(current_val - cost_basis):+.2f}"
        stoch_str   = f"%K={last_row['STOCHk']:.1f} %D={last_row['STOCHd']:.1f}"

        symbol_status.append(
            f"[{symbol}] shares={num_shares} value=${current_val:.2f} pnl={profit_str} price=${current_price:.2f} {stoch_str}"
        )

    trade_log.to_csv(CSV_FILE, index=False)

    msg = f"Trade Report {today_jst}\n\n"
    msg += "Signals:\n"
    msg += "\n".join(notifications) if notifications else "No signal"
    if exit_notifications:
        msg += "\nExits:\n" + "\n".join(exit_notifications)
    msg += "\n\nPositions:\n" + "\n".join(symbol_status)

    if today_jst.weekday() == 5:
        monday = today_jst - datetime.timedelta(days=today_jst.weekday())
        friday = monday + datetime.timedelta(days=4)
        weekly = trade_log[
            (trade_log['Date'] >= str(monday)) &
            (trade_log['Date'] <= str(friday)) &
            (trade_log['Status'].isin(['holding', 'closed']))
        ]
        msg += "\n\nWeekly Report:\n"
        if not weekly.empty:
            weekly = weekly.sort_values('Date')
            msg += "\n".join(
                [f"{r['Date']} {r['Symbol']} ${float(r['Buy_Price']):.2f} [{r['Status']}]"
                 for _, r in weekly.iterrows()]
            )
        else:
            msg += "No trades this week."

    if today_jst.weekday() == 6 or today_jst in holidays.Japan():
        msg += "\n\nPrev business day report."

    # Discord通知処理のインデントを修正
    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        # 2000文字制限のため分割して送信
        chunk_size = 1900
        for i in range(0, len(msg), chunk_size):
            webhook.send(msg[i:i+chunk_size])
        print("Discord notification sent.")

    print(msg)
    print("--- Execution Finished ---")


if __name__ == "__main__":
    main()
