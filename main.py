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
    try:
        # 1年分のデータを取得（増分取得は、yfinanceの仕様変更に強いため一旦シンプルに全取得）
        df = yf.download(symbol, period='1y')
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()

def calculate_signals(df):
    # ストキャスティクス自前計算 (K=14, D=3)
    # yfinanceのデータ構造が多重（MultiIndex）になる場合があるため、平坦化
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    
    # %K
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
    # %D (3日移動平均)
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    
    # 25%以下の判定 (Seriesをスカラーに変換するため .iloc[-1] で後ほど判定)
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
        if df.empty or len(df) < 14:
            continue
        
        df = calculate_signals(df)
        
        # 確実に最新1行のみを取得
        last_row = df.tail(1).iloc[0]
        current_price = float(last_row['Close'])
        
        # 1. 前日のsignalをholdingに更新
        mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask.any():
            trade_log.loc[mask, 'Buy_Price'] = float(last_row['Open'])
            trade_log.loc[mask, 'Status'] = 'holding'

        # 2. 新規買いシグナル判定 (.item() または直接参照で曖昧さを回避)
        if bool(last_row['buy_signal']):
            # すでに今日同じ銘柄でシグナルが出ていないかチェック
            today_str = last_row.name.strftime('%Y-%m-%d')
            exists = trade_log[(trade_log['Date'] == today_str) & (trade_log['Symbol'] == symbol)].any().any()
            
            if not exists:
                new_row = {'Date': today_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0}
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

    # データを保存
    trade_log.to_csv(CSV_FILE, index=False)

    # 通知メッセージ
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
