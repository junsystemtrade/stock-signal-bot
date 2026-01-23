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
            # 取得方法を最も標準的なものに
            df = yf.download(symbol, period='1y', progress=False)
            
            if not df.empty:
                # 列名が多重構造（MultiIndex）なら平坦化
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                # インデックスを日付型にして保存
                df.index = pd.to_datetime(df.index)
                df.to_csv(filename)
                return df
            time.sleep(2)
        except Exception as e:
            print(f"Error for {symbol}: {e}")
            time.sleep(2)

    # 失敗時は既存ファイルを読むが、ヘッダーエラー対策としてtry-except
    if os.path.exists(filename):
        try:
            df_old = pd.read_csv(filename, index_col=0, parse_dates=True)
            return df_old
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def calculate_signals(df):
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    df['buy_signal'] = (df['STOCHk'] <= 25) | (df['STOCHd'] <= 25)
    return df

def main():
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    if os.path.exists(CSV_FILE):
        trade_log = pd.read_csv(CSV_FILE)
        # 数値変換とStatusの空白削除
        trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
        if 'Status' in trade_log.columns:
            trade_log['Status'] = trade_log['Status'].astype(str).str.strip()
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    symbol_status = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        current_price = 0
        
        if not df.empty:
            valid_df = df.dropna(subset=['Close']).copy()
            if not valid_df.empty:
                last_row = valid_df.tail(1)
                current_price = float(last_row['Close'].iloc[0])
                last_date_str = last_row.index[0].strftime('%Y-%m-%d')

                if len(valid_df) >= 14:
                    valid_df = calculate_signals(valid_df)
                    sig_row = valid_df.tail(1)
                    
                    # シグナル更新
                    mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
                    if mask.any():
                        trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                        trade_log.loc[mask, 'Status'] = 'holding'

                    # 新規シグナル
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

    # 通知作成
    msg = f"📅 **{today_jt.strftime('%Y-%m-%d')} トレード報告**\n\n📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ シグナルなし"
    msg += f"\n\n📊 **保有銘柄状況**\n" + "\n\n".join(symbol_status)
    
    if today_jt.weekday() == 5: # 土曜日（週報）
        msg += "\n\n📜 **【週報】今週の購入履歴**\n"
        one_week_ago = (today_jt - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        weekly = trade_log[(trade_log['Date'] >= one_week_ago) & (trade_log['Status'] == 'holding')]
        msg += "\n".join([f"・{r['Date']} : {r['Symbol']}を${float(r['Buy_Price']):.2f}で購入" for _, r in weekly.iterrows()]) if not weekly.empty else "なし"

    if DISCORD_WEBHOOK_URL:
        SyncWebhook.from_url(DISCORD_WEBHOOK_URL).send(msg)

if __name__ == "__main__":
    main()
