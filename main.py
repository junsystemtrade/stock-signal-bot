import os
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook
import time

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

def get_stock_data(symbol):
    filename = f"{symbol}_history.csv"
    for attempt in range(3):
        try:
            # 最新の yfinance で取得
            df = yf.download(symbol, period='1y', progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.index = pd.to_datetime(df.index)
                df.to_csv(filename)
                return df
            time.sleep(2)
        except Exception as e:
            print(f"Error for {symbol}: {e}")
            time.sleep(2)

    # 失敗時は既存CSVを読み込む
    if os.path.exists(filename):
        try:
            return pd.read_csv(filename, index_col=0, parse_dates=True)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def calculate_signals(df):
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14).replace(0, 1))
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    df['buy_signal'] = (df['STOCHk'] <= 25) | (df['STOCHd'] <= 25)
    return df

def main():
    print("--- 🚀 Execution Started ---")
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    # トレードログ読み込み
    if os.path.exists(CSV_FILE):
        try:
            trade_log = pd.read_csv(CSV_FILE)
            trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
            if 'Status' in trade_log.columns:
                trade_log['Status'] = trade_log['Status'].astype(str).str.strip()
        except Exception as e:
            print(f"CSV read error: {e}")
            trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])

    notifications = []
    symbol_status = []

    for symbol in SYMBOLS:
        print(f"Checking {symbol}...")
        df = get_stock_data(symbol)
        if df.empty:
            symbol_status.append(f"【{symbol}】\n⚠️ データ取得失敗")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if not valid_df.empty:
            last_row = valid_df.tail(1)
            current_price = float(last_row['Close'].iloc[0])
            last_date_str = last_row.index[0].strftime('%Y-%m-%d')

            if len(valid_df) >= 14:
                valid_df = calculate_signals(valid_df)
                sig_row = valid_df.tail(1)

                # シグナル更新（前日のシグナルを当日始値で約定）
                mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
                if mask.any():
                    trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                    trade_log.loc[mask, 'Status'] = 'holding'

                # 新規シグナル判定
                if bool(sig_row['buy_signal'].iloc[0]):
                    exists = trade_log[(trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)].any().any()
                    if not exists:
                        new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0}
                        trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                        notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 集計
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        current_value = current_price * num_shares
        cost_basis = holdings['Buy_Price'].sum()
        profit_str = f"${(current_value - cost_basis):+.2f}"
        symbol_status.append(f"【{symbol}】\n保有数: {num_shares}株\n評価額: ${current_value:.2f}（損益: {profit_str}）")

    trade_log.to_csv(CSV_FILE, index=False)

    # 通知メッセージ
    msg = f"📅 **{today_jt.strftime('%Y-%m-%d')} トレード報告**\n\n📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ シグナルなし"
    msg += f"\n\n📊 **保有銘柄状況**\n" + "\n\n".join(symbol_status)

    if DISCORD_WEBHOOK_URL:
        SyncWebhook.from_url(DISCORD_WEBHOOK_URL).send(msg)
    print("--- ✅ Execution Finished ---")

if __name__ == "__main__":
    main()
