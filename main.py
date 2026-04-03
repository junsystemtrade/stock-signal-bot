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
    'JMIA': {'func': jmia_signal},
    'NU':   {'func': nu_signal},
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

    notifications = []
    symbol_status = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol, today_us)
        if df is None or df.empty:
            symbol_status.append(f"【{symbol}】\n⚠️ 米国市場休場またはデータ取得失敗")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if len(valid_df) < 200:
            symbol_status.append(f"【{symbol}】\n⚠️ データ不足（{len(valid_df)}件）")
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
                notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 保有状況集計（holdingのみ、エグジットなし）
        holdings    = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares  = len(holdings)
        current_val = current_price * num_shares
        cost_basis  = holdings['Buy_Price'].sum()
        profit_str  = f"${(current_val - cost_basis):+.2f}"

        symbol_status.append(
            f"【{symbol}】\n保有数: {num_shares}株\n評価額: ${current_val:.2f}（損益: {profit_str}）"
        )

    trade_log.to_csv(CSV_FILE, index=False)

    # 通知作成
    msg = f"📅 **{today_jst} トレード報告**\n\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ シグナルなし"
    msg += "\n\n📊 **保有銘柄状況**\n" + "\n\n".join(symbol_status)

    # 週次レポート（土曜JST）
    if today_jst.weekday() == 5:
        monday = today_jst - datetime.timedelta(days=today_jst.weekday())
        friday = monday + datetime.timedelta(days=4)
        weekly = trade_log[
            (trade_log['Date'] >= str(monday)) &
            (trade_log['Date'] <= str(friday)) &
            (trade_log['Status'] == 'holding')
        ]
        msg += "\n\n📜 **【週報】今週の取引履歴**\n"
        if not weekly.empty:
            weekly = weekly.sort_values('Date')
            msg += "\n".join(
                [f"・{r['Date']} : {r['Symbol']} ${float(r['Buy_Price']):.2f}"
                 for _, r in weekly.iterrows()]
            )
        else:
            msg += "今週の取引はありません。"

    # 前営業日通知（日曜・祝日JST）
    if today_jst.weekday() == 6 or today_jst in holidays.Japan():
        msg += "\n\n📌 **前営業日データの通知**"

    # Discord送信（2000文字制限対応）
    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        chunk_size = 1900
        for i in range(0, len(msg), chunk_size):
            webhook.send(msg[i:i+chunk_size])
        print("Discord notification sent.")

    print(msg)
    print("--- Execution Finished ---")


if __name__ == "__main__":
    main()
