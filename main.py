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
    df = pd.DataFrame()

    # 1. データの新規取得を試行
    for attempt in range(3):
        try:
            # 最新のyf.download仕様（progress表示なし）
            temp_df = yf.download(symbol, period='1y', progress=False)
            
            if not temp_df.empty:
                # 多重インデックス（MultiIndex）の解除・平坦化
                if isinstance(temp_df.columns, pd.MultiIndex):
                    temp_df.columns = temp_df.columns.get_level_values(0)
                
                temp_df.index = pd.to_datetime(temp_df.index)
                
                # CSVへ保存（蓄積）
                temp_df.to_csv(filename)
                return temp_df
            
            time.sleep(2)
        except Exception as e:
            print(f"Attempt {attempt+1} Error for {symbol}: {e}")
            time.sleep(2)

    # 2. 取得失敗時は既存のCSVファイルを読み込む
    if os.path.exists(filename):
        try:
            df_old = pd.read_csv(filename, index_col=0, parse_dates=True)
            return df_old
        except:
            return pd.DataFrame()
    
    return pd.DataFrame()

def calculate_signals(df):
    # ストキャスティクス（14, 3）の計算
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    
    # 0除算防止
    diff = high_14 - low_14
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / diff.replace(0, 1))
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    
    # 買いシグナル判定（25以下）
    df['buy_signal'] = (df['STOCHk'] <= 25) | (df['STOCHd'] <= 25)
    return df

def main():
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    # 1. トレードログ（保有状況）の読み込み
    if os.path.exists(CSV_FILE):
        try:
            trade_log = pd.read_csv(CSV_FILE)
            # 文字列クレンジングと数値変換
            if 'Status' in trade_log.columns:
                trade_log['Status'] = trade_log['Status'].astype(str).str.strip()
            trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
        except Exception as e:
            print(f"CSV read error: {e}")
            trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])

    notifications = []
    symbol_status = []

    # 2. 銘柄ごとの処理
    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        current_price = 0

        if not df.empty:
            valid_df = df.dropna(subset=['Close']).copy()
            if not valid_df.empty:
                last_row = valid_df.tail(1)
                current_price = float(last_row['Close'].iloc[0])
                last_date_str = last_row.index[0].strftime('%Y-%m-%d')

                # シグナル判定（14日分以上のデータが必要）
                if len(valid_df) >= 14:
                    valid_df = calculate_signals(valid_df)
                    sig_row = valid_df.tail(1)

                    # シグナル更新（'signal'状態のものを'holding'へ）
                    mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
                    if mask.any():
                        # 前日シグナルが出た銘柄の買値を当日始値で確定
                        trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                        trade_log.loc[mask, 'Status'] = 'holding'

                    # 新規買いシグナル発生チェック
                    if bool(sig_row['buy_signal'].iloc[0]):
                        exists = trade_log[(trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)].any().any()
                        if not exists:
                            new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0}
                            trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                            notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 3. 保有銘柄の損益集計
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        current_value = current_price * num_shares
        cost_basis = holdings['Buy_Price'].sum()
        profit = current_value - cost_basis
        
        symbol_status.append(
            f"【{symbol}】\n保有数: {num_shares}株\n評価額: ${current_value:.2f}（損益: ${profit:+.2f}）"
        )

    # 4. CSVの保存
    trade_log.to_csv(CSV_FILE, index=False)

    #
