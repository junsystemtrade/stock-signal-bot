import os
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

def get_stock_data(symbol):
    filename = f"{symbol}_history.csv"
    try:
        df = pd.DataFrame()
        if os.path.exists(filename):
            # 日付パースを明示的に指定して読み込み
            df_old = pd.read_csv(filename, index_col=0)
            df_old.index = pd.to_datetime(df_old.index)
            
            last_date = df_old.index.max()
            # yfinanceの引数エラー回避のため最小限の引数で実行
            new_data = yf.download(symbol, start=last_date + datetime.timedelta(days=1))
            
            if not new_data.empty:
                df = pd.concat([df_old, new_data])
                df = df[~df.index.duplicated(keep='last')]
            else:
                df = df_old
        else:
            df = yf.download(symbol, period='1y')
        
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            df.to_csv(filename)
            
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()

def calculate_signals(df):
    # 計算前にソートを確認
    df = df.sort_index()
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
    df['STOCHd'] = df['STOCHk'].rolling(window=3).mean()
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
    symbol_status = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        if df.empty or len(df) < 14:
            symbol_status.append(f"【{symbol}】\n保有数: 0株\n評価額: $0.00（損益: $0.00）")
            continue
        
        df = calculate_signals(df)
        last_row = df.iloc[-1]
        # indexがDatetimeIndexであることを保証してフォーマット
        last_date_str = last_row.name.strftime('%Y-%m-%d')
        current_price = float(last_row['Close'])
        
        # 1. 前日のsignalをholdingに更新
        mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask.any():
            trade_log.loc[mask, 'Buy_Price'] = float(last_row['Open'])
            trade_log.loc[mask, 'Status'] = 'holding'

        # 2. 新規買いシグナル判定
        if bool(last_row['buy_signal']):
            exists = trade_log[(trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)].any().any()
            if not exists:
                new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0}
                trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 3. 銘柄別保有状況の計算
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        current_value = current_price * num_shares
        
        profit_str = "$0.00"
        if num_shares > 0:
            cost_basis = pd.to_numeric(holdings['Buy_Price']).sum()
            profit = current_value - cost_basis
            profit_str = f"${profit:+.2f}"
        
        status_text = (
            f"【{symbol}】\n"
            f"保有数: {num_shares}株\n"
            f"評価額: ${current_value:.2f}（損益: {profit_str}）"
        )
        symbol_status.append(status_text)

    trade_log.to_csv(CSV_FILE, index=False)

    msg = f"📅 **{today_jt.strftime('%Y-%m-%d')} トレード報告**\n\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ シグナルなし"
    msg += "\n\n📊 **保有銘柄状況**\n"
    msg += "\n\n".join(symbol_status)
    
    if is_saturday:
        msg += "\n\n📜 **【週報】今週の購入履歴**\n"
        one_week_ago = (today_jt - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        weekly_trades = trade_log[(trade_log['Date'] >= one_week_ago) & (trade_log['Status'] == 'holding')]
        
        if not weekly_trades.empty:
            history_text = ""
            for _, row in weekly_trades.iterrows():
                buy_p = float(row['Buy_Price'])
                history_text += f"・{row['Date']} : {row['Symbol']}を${buy_p:.2f}で購入\n"
            msg += history_text
        else:
            msg += "今週の購入履歴はありません。"

    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        webhook.send(msg)

if __name__ == "__main__":
    main()
