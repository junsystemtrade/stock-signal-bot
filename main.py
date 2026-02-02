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

def get_stock_data(symbol, date_today_us):
    """株価データ取得（米国営業日判定）"""
    # 米国土日、祝日はスキップ
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
            print(f"Error for {symbol}: {e}")
            time.sleep(2)

    # 取得失敗時は既存CSV読み込み
    if os.path.exists(filename):
        try:
            return pd.read_csv(filename, index_col=0, parse_dates=True)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def calculate_signals(df):
    """ストキャスティクスによる買いシグナル判定"""
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14).replace(0,1))
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
    df['buy_signal'] = (df['STOCHk'] <= 25) | (df['STOCHd'] <= 25)
    return df

def main():
    print("--- 🚀 Execution Started ---")
    
    # --- 日付設定 ---
    now_jst = datetime.datetime.now(JST)
    today_jst = now_jst.date()
    now_us = now_jst.astimezone(US_EAST)
    today_us = now_us.date()

    # CSV読み込み
    if os.path.exists(CSV_FILE):
        try:
            trade_log = pd.read_csv(CSV_FILE)
            trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
            if 'Status' in trade_log.columns:
                trade_log['Status'] = trade_log['Status'].astype(str).str.strip()
        except Exception as e:
            print(f"CSV read error: {e}")
            trade_log = pd.DataFrame(columns=['Date','Symbol','Status','Buy_Price'])
    else:
        trade_log = pd.DataFrame(columns=['Date','Symbol','Status','Buy_Price'])

    notifications = []
    symbol_status = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol, today_us)

        if df is None or df.empty:
            symbol_status.append(f"【{symbol}】\n⚠️ 米国市場休場またはデータ取得失敗")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if len(valid_df) >= 14:
            valid_df = calculate_signals(valid_df)
            sig_row = valid_df.tail(1)
            last_date_str = sig_row.index[0].strftime('%Y-%m-%d')

            # 前日のシグナルを当日始値で約定
            mask = (trade_log['Symbol']==symbol) & (trade_log['Status']=='signal')
            if mask.any():
                trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                trade_log.loc[mask, 'Status'] = 'holding'

            # 新規シグナル判定
            if bool(sig_row['buy_signal'].iloc[0]):
                exists = trade_log[(trade_log['Date']==last_date_str) & (trade_log['Symbol']==symbol)].any().any()
                if not exists:
                    new_row = {'Date': last_date_str,'Symbol':symbol,'Status':'signal','Buy_Price':0.0}
                    trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                    notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 保有状況
        holdings = trade_log[(trade_log['Symbol']==symbol) & (trade_log['Status']=='holding')]
        num_shares = len(holdings)
        if not valid_df.empty:
            current_price = float(valid_df.tail(1)['Close'].iloc[0])
            current_value = current_price * num_shares
            cost_basis = holdings['Buy_Price'].sum()
            profit_str = f"${(current_value - cost_basis):+.2f}"
        else:
            current_value = cost_basis = 0
            profit_str = "$0.00"
        symbol_status.append(f"【{symbol}】\n保有数: {num_shares}株\n評価額: ${current_value:.2f}（損益: {profit_str}）")

    # CSV更新
    trade_log.to_csv(CSV_FILE, index=False)

    # 通知作成
    msg = f"📅 **{today_jst} トレード報告**\n\n📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ シグナルなし"
    msg += f"\n\n📊 **保有銘柄状況**\n" + "\n\n".join(symbol_status)

    # 週次レポート（土曜JST）
    if today_jst.weekday() == 5:
        monday = today_jst - datetime.timedelta(days=today_jst.weekday())
        friday = monday + datetime.timedelta(days=4)
        weekly_trades = trade_log[(trade_log['Date'] >= str(monday)) & (trade_log['Date'] <= str(friday)) & (trade_log['Status']=='holding')]
        msg += "\n\n📜 **【週報】米国市場（月〜金）の購入履歴**\n"
        if not weekly_trades.empty:
            weekly_trades = weekly_trades.sort_values('Date')
            msg += "\n".join([f"・{r['Date']} : {r['Symbol']} を ${float(r['Buy_Price']):.2f} で購入" for _, r in weekly_trades.iterrows()])
        else:
            msg += "今週（月〜金）の購入履歴はありません。"

    # 前営業日通知（日曜・祝日JST）
    if today_jst.weekday() == 6 or today_jst in holidays.Japan():
        msg += "\n\n📌 **前営業日データの通知**"

    # Discord送信
    if DISCORD_WEBHOOK_URL:
        SyncWebhook.from_url(DISCORD_WEBHOOK_URL).send(msg)

    print("--- ✅ Execution Finished ---")

if __name__ == "__main__":
    main()
