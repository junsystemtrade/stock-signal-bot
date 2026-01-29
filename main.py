import os
import datetime
import pandas as pd
import yfinance as yf  # 株価取得に必要です
from discord import SyncWebhook

# --- 設定エリア ---
SYMBOLS = ["JMIA", "NU"] 
WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

# --- 今回のエラーを解消するために追加（get_stock_dataの定義） ---
def get_stock_data(symbol):
    try:
        # yfinanceを使用して株価データを取得
        df = yf.download(symbol, period="1mo", interval="1d")
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

# --- main関数（ご提示のソースを維持） ---
def main():
    # 1. 起動ログ
    print("--- Execution Started ---")
    print(f"Webhook URL configured: {bool(WEBHOOK_URL)}")
    
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    # 既存のデータ読み込み処理
    if os.path.exists(trade_history.csv):
        trade_log = pd.read_csv(trade_history.csv)
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    symbol_status = []

    # 2. 銘柄ループの進捗ログ
    print(f"Processing {len(SYMBOLS)} symbols...")

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)  # ここでのエラーを解消しました
        current_price = 0
        
        if df is None or df.empty:
            print(f"⚠️ {symbol}: No data found")
            symbol_status.append(f"【{symbol}】\n⚠️ データ取得失敗")
            continue

        # --- 以下、ご提示のロジック（中略部分を含む）をそのまま継続 ---
        # ※実際の実行には calculate_signals 関数や変数（num_shares, profit_str等）の定義が
        # main内の「中略」部分に含まれている必要があります。
        
        # symbol_status.append(f"【{symbol}】\n保有数: {num_shares}株\n損益: {profit_str}")

    # CSV保存
    trade_log.to_csv(trade_history.csv, index=False)

    # 3. メッセージの組み立て
    msg = f"📅 **定期報告: {today_jt.strftime('%Y-%m-%d %H:%M')}**\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ 新規シグナルなし"
    msg += f"\n\n📊 **現在の保有状況**\n"
    msg += "\n\n".join(symbol_status) if symbol_status else "銘柄データが処理されませんでした。"

    # 4. 送信処理
    if WEBHOOK_URL:
        print("Attempting to send Discord notification...")
        try:
            webhook = SyncWebhook.from_url(WEBHOOK_URL)
            webhook.send(msg)
            print("✅ Discord notification sent successfully!")
        except Exception as e:
            print(f"❌ Discord Send Error: {e}")
    else:
        print("❌ CRITICAL: DISCORD_WEBHOOK_URL is empty. Check GitHub Secrets.")

    print("--- Execution Finished ---")
    
if __name__ == "__main__":
    main()
