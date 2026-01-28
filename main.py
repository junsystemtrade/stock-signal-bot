import os
import datetime
import pandas as pd
from discord import SyncWebhook

# --- 冒頭に追加 ---
# 環境変数を直接取得（グローバルで定義されていることを想定）
WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

def main():
    # 1. 起動ログ（GitHub Actionsのログに必ず出る）
    print("--- Execution Started ---")
    print(f"Webhook URL configured: {bool(WEBHOOK_URL)}")
    
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    # 既存のデータ読み込み処理
    if os.path.exists(CSV_FILE):
        trade_log = pd.read_csv(CSV_FILE)
        # ...（中略：既存のBuy_Price等のクレンジング）...
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    symbol_status = []

    # 2. 銘柄ループの進捗ログ
    print(f"Processing {len(SYMBOLS)} symbols...")

    for symbol in SYMBOLS:
        df = get_stock_data(symbol)
        current_price = 0
        
        if df is None or df.empty:
            print(f"⚠️ {symbol}: No data found")
            symbol_status.append(f"【{symbol}】\n⚠️ データ取得失敗")
            continue

        # ...（中略：シグナル判定ロジック）...
        # ※ここでもしエラーが起きても止まらないよう、必要に応じてtry-exceptを入れる
        
        symbol_status.append(f"【{symbol}】\n保有数: {num_shares}株\n損益: {profit_str}")

    # CSV保存
    trade_log.to_csv(CSV_FILE, index=False)

    # 3. メッセージの組み立て（中身がなくても送る）
    msg = f"📅 **定期報告: {today_jt.strftime('%Y-%m-%d %H:%M')}**\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ 新規シグナルなし"
    msg += f"\n\n📊 **現在の保有状況**\n"
    msg += "\n\n".join(symbol_status) if symbol_status else "銘柄データが処理されませんでした。"

    # 4. 送信処理（ここが重要）
    if WEBHOOK_URL:
        print("Attempting to send Discord notification...")
        try:
            webhook = SyncWebhook.from_url(WEBHOOK_URL)
            webhook.send(msg)
            print("✅ Discord notification sent successfully!")
        except Exception as e:
            print(f"❌ Discord Send Error: {e}")
    else:
        # ここが表示される場合、GitHub Secretsの設定が反映されていません
        print("❌ CRITICAL: DISCORD_WEBHOOK_URL is empty. Check GitHub Secrets.")

    print("--- Execution Finished ---")
    
# --- 以下の2行を必ず追加してください ---
if __name__ == "__main__":
    main()
