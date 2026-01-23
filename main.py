import os
import datetime
import pandas as pd
import yfinance as yf
import pandas_ta as ta
from discord import SyncWebhook

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
CACHE_FILE = 'stock_cache.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

def get_stock_data(symbol):
    # キャッシュ読み込み（増分取得）
    if os.path.exists(CACHE_FILE):
        df_cache = pd.read_csv(CACHE_FILE, index_col=0, parse_dates=True)
        # 過去データがある場合はそれ以降を取得
        last_date = df_cache.index.max()
        new_data = yf.download(symbol, start=last_date + datetime.timedelta(days=1))
        if not new_data.empty:
            df = pd.concat([df_cache, new_data])
            df = df[~df.index.duplicated(keep='last')]
        else:
            df = df_cache
    else:
        df = yf.download(symbol, period='1y')
    
    df.to_csv(CACHE_FILE)
    return df

def calculate_signals(df):
    # ストキャスティクス (K=14, D=3)
    stoch = ta.stoch(df['High'], df['Low'], df['Close'], k=14, d=3)
    df = pd.concat([df, stoch], axis=1)
    # 25%以下の判定
    df['buy_signal'] = (df['STOCHk_14_3_3'] <= 25) | (df['STOCHd_14_3_3'] <= 25)
    return df

def main():
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    is_saturday = today_jt.weekday() == 5
    
    trade_log = pd.read_csv(CSV_FILE) if os.path.exists(CSV_FILE) else pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    total_value = 0
    total_profit = 0
    holding_count = 0

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        df = calculate_signals(df)
        
        last_row = df.iloc[-1]
        current_price = last_row['Close']
        
        # 1. 前日のsignalをholdingに更新（寄付き価格をセット）
        mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask.any():
            # シグナル翌日のOpen(つまり今回のデータの中の最新Open)を取得
            trade_log.loc[mask, 'Buy_Price'] = last_row['Open']
            trade_log.loc[mask, 'Status'] = 'holding'

        # 2. 新規買いシグナル判定
        if last_row['buy_signal']:
            new_row = {'Date': last_row.name.strftime('%Y-%m-%d'), 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0}
            trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
            notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 3. 評価額計算
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        if num_shares > 0:
            holding_count += num_shares
            cost_basis = holdings['Buy_Price'].sum()
            market_value = current_price * num_shares
            total_value += market_value
            total_profit += (market_value - cost_basis)

    trade_log.to_csv(CSV_FILE, index=False)

    # Discord通知作成
    msg = f"📅 **{today_jt.strftime('%Y-%m-%d')} トレード報告**\n"
    msg += "\n".join(notifications) if notifications else "シグナルなし"
    msg += f"\n\n📊 **現在の状況**\n保有数: {holding_count}株\n評価額: ${total_value:.2f}\n含み損益: ${total_profit:.2f}"
    
    if is_saturday:
        msg += "\n\n週報: 今週もお疲れ様でした。履歴はGitHubを確認してください。"

    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        webhook.send(msg)

if __name__ == "__main__":
    main()
