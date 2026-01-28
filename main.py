def main():
    # --- 1. 環境変数のチェック (ログ出力) ---
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    print(f"DEBUG: Webhook URL detected: {bool(webhook_url)}")

    # 日本時間を取得
    today_jt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    if os.path.exists(CSV_FILE):
        trade_log = pd.read_csv(CSV_FILE)
        trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
        if 'Status' in trade_log.columns:
            trade_log['Status'] = trade_log['Status'].astype(str).str.strip()
    else:
        trade_log = pd.DataFrame(columns=['Date', 'Symbol', 'Status', 'Buy_Price'])
    
    notifications = []
    symbol_status = []

    print(f"DEBUG: Starting loop for {len(SYMBOLS)} symbols...")

    for symbol in SYMBOLS:
        print(f"DEBUG: Checking {symbol}...")
        df = get_stock_data(symbol)
        current_price = 0
        
        # --- 修正ポイント：データが空でも symbol_status には追加する ---
        if df is None or df.empty:
            print(f"DEBUG: No data for {symbol}")
            symbol_status.append(f"【{symbol}】\n⚠️ データ取得失敗（市場休業など）")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if not valid_df.empty:
            last_row = valid_df.tail(1)
            current_price = float(last_row['Close'].iloc[0])
            last_date_str = last_row.index[0].strftime('%Y-%m-%d')

            if len(valid_df) >= 14:
                valid_df = calculate_signals(valid_df)
                sig_row = valid_df.tail(1)
                
                mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
                if mask.any():
                    trade_log.loc[mask, 'Buy_Price'] = float(sig_row['Open'].iloc[0])
                    trade_log.loc[mask, 'Status'] = 'holding'

                if bool(sig_row['buy_signal'].iloc[0]):
                    exists = trade_log[(trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)].any().any()
                    if not exists:
                        new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0}
                        trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                        notifications.append(f"🚨 **買いシグナル発生**: {symbol} (判定日: {last_date_str})")

        holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares = len(holdings)
        current_value = current_price * num_shares
        cost_basis = holdings['Buy_Price'].sum()
        profit_str = f"${(current_value - cost_basis):+.2f}"
        
        symbol_status.append(f"【{symbol}】\n保有数: {num_shares}株\n評価額: ${current_value:.2f}（損益: {profit_str}）")

    # CSVを保存
    trade_log.to_csv(CSV_FILE, index=False)

    # --- 2. メッセージが空にならないようガード ---
    msg = f"📅 **報告日時: {today_jt.strftime('%Y-%m-%d %H:%M')}**\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ 新規シグナルなし"
    msg += f"\n\n📊 **現在の保有状況**\n" 
    msg += "\n\n".join(symbol_status) if symbol_status else "銘柄データなし"
    
    if today_jt.weekday() == 5: 
        msg += "\n\n📜 **【週報】今週の購入履歴**\n"
        one_week_ago = (today_jt - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        weekly = trade_log[(trade_log['Date'] >= one_week_ago) & (trade_log['Status'] == 'holding')]
        msg += "\n".join([f"・{r['Date']} : {r['Symbol']}を${float(r['Buy_Price']):.2f}で購入" for _, r in weekly.iterrows()]) if not weekly.empty else "なし"

    # --- 3. 送信処理のログ出力 ---
    if webhook_url:
        try:
            print("DEBUG: Attempting to send message to Discord...")
            SyncWebhook.from_url(webhook_url).send(msg)
            print("DEBUG: Send successful!")
        except Exception as e:
            print(f"❌ Discord通知エラー詳細: {e}")
    else:
        print("❌ Discord Webhook URL が設定されていないため、通知をスキップしました。")
