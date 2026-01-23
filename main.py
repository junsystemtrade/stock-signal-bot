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
    try:
        # yfinanceの最新仕様に対応した取得方法
        ticker = yf.Ticker(symbol)
        # まずは直近1ヶ月分を確実に取得
        df = ticker.history(period="1mo")
        
        if df.empty:
            # 失敗した場合は1年分で再試行
            df = ticker.history(period="1y")

        if df.empty:
            if os.path.exists(filename):
                return pd.read_csv(filename, index_col=0, parse_dates=True)
            return pd.DataFrame()

        # 列名を平坦化（yfの仕様変更対策）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        # 保存（既存データがある場合はマージ）
        if os.path.exists(filename):
            df_old = pd.read_csv(filename, index_col=0, parse_dates=True)
            df = pd.concat([df_old, df])
            df = df[~df.index.duplicated(keep='last')]
        
        df.to_csv(filename)
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()

def calculate_signals(df):
    # ストキャスティクス計算
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
        # 連続リクエストによるブロックを避けるため少し待機
        time.sleep(1)
        df = get_stock_data(symbol)
        
        current_price = 0
        if not df.empty:
            # NaNを排除した最新の行を取得
            valid_df = df.dropna(subset=['Close'])
            if not valid_df.empty:
                last_row = valid_df.iloc[-1:]
                current_price = float(last_row['Close'].iloc[0])
                last_date_str = last_row.index[0].strftime('%Y-%m-%d')

                if len(valid_df) >= 14:
                    valid_df = calculate_signals(valid_df.copy())
                    sig_row = valid_df.iloc[-1:]
                    
                    # 1. 前日のシグナルを保有中に更新
                    mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
                    if mask.any():
                        trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                        trade_log.loc[mask, 'Status'] = 'holding'

                    # 2. 新規買いシグナル判定
                    if bool(sig_row['buy_signal'].iloc[0]):
                        exists = trade_log[(trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)].any().any()
                        if not exists:
                            new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0}
                            trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                            notifications.append(f"🚨 **買いシグナル発生**: {symbol}")

        # 3. 保有状況の計算
        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        current_value = current_price * num_shares
        
        profit_str = "$0.00"
        if num_shares > 0:
            buy_prices = pd.to_numeric(holdings['Buy_Price'], errors='coerce').fillna(0)
            cost_basis = buy_prices.sum()
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
            history_text = "\n".join([f"・{r['Date']} : {r['Symbol']}を${float(r['Buy_Price']):.2f}で購入" for _, r in weekly_trades.iterrows()])
            msg += history_text
        else:
            msg += "今週の購入履歴はありません。"

    if DISCORD_WEBHOOK_URL:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        webhook.send(msg)

if __name__ == "__main__":
    main()
