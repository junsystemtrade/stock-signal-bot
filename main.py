import os
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
CACHE_FILE = 'stock_cache.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

def get_stock_data(symbol):
    if os.path.exists(CACHE_FILE):
        try:
            df_cache = pd.read_csv(CACHE_FILE, index_col=0, parse_dates=True)
            last_date = df_cache.index.max()
            new_data = yf.download(symbol, start=last_date + datetime.timedelta(days=1))
            if not new_data.empty:
                df = pd.concat([df_cache, new_data])
                df = df[~df.index.duplicated(keep='last')]
            else:
                df = df_cache
        except:
            df = yf.download(symbol, period='1y')
    else:
        df = yf.download(symbol, period='1y')
    
    df.to_csv(CACHE_FILE)
    return df

def calculate_signals(df):
    # ストキャスティクス自前計算 (K=14, D=3)
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    
    # %K
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
    # %D (3日移動平均)
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    
    # 25%以下の判定
    df['buy_signal'] = (df['STOCHk'] <= 25) | (df['STOCHd'] <= 25)
    return df

def main():
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    is_saturday = today_jt.weekday() == 5
    
    if os.path.exists(CSV_FILE):
        trade_log = pd.read_csv(CSV_FILE)
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    total_value = 0
    total_profit = 0
    holding_count = 0

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        if df.empty: continue
        
        df = calculate_signals(df)
        last_row = df.iloc[-1]
        current_price = last_row['Close']
        
        # 1. 前日のsignalをholdingに更新
        mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask.any():
            trade_log.loc[mask, 'Buy_Price'] = last_row['Open']
            trade_log.loc[mask, 'Status'] = 'holding'

        # 2. 新規買いシグナル判定
        if last_row['buy_signal']:
            new_row = {'Date': last_row.name.strftime('%Y-%m-%d'), 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0}
            trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
            notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 3. 評価額計算
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        if not holdings.empty:
            num_shares = len(holdings)
            holding_count += num_shares
            cost_basis = pd.to_numeric(holdings['Buy_Price']).sum()
            market_value = current_price * num_shares
            total_value += market_value
            total_profit += (market_value - cost_basis)

    trade_log.to_csv(CSV_FILE, index=False)

    msg = f"📅 **{today_jt.strftime('%Y-%m-%d')} トレード報告**\n"
    msg += "\n".join(notifications) if notifications else "シグナルなし"
    msg += f"\n\n📊 **現在の状況**\n保有数: {holding_count}株\n評価額: ${total_value:.2f}\n含み損益: ${total_profit:.2f}"
    
    if is_saturday:
        msg += "\n\n週報: 今週もお疲れ様でした。"

    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        webhook.send(msg)

if __name__ == "__main__":
    main()
