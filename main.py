import os
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook
import pytz
import time
import holidays

# — 設定 —

SYMBOLS = [‘JMIA’, ‘NU’]
CSV_FILE = ‘trade_history.csv’
DISCORD_WEBHOOK_URL = os.getenv(‘DISCORD_WEBHOOK_URL’)

# タイムゾーン

JST = pytz.timezone(‘Asia/Tokyo’)
US_EAST = pytz.timezone(‘US/Eastern’)

# 米国祝日

US_HOLIDAYS = holidays.US()

# =============================

# 共通指標計算（backtest.pyと共通）

# =============================

def add_indicators(df):
“”“ストキャスティクス・移動平均・MACDを追加”””
# ストキャスティクス
low_14 = df[‘Low’].rolling(14).min()
high_14 = df[‘High’].rolling(14).max()
df[‘STOCHk’] = 100 * ((df[‘Close’] - low_14) / (high_14 - low_14).replace(0, 1))
df[‘STOCHd’] = df[‘STOCHk’].rolling(3).mean()

```
# 移動平均
df['MA50']  = df['Close'].rolling(50).mean()
df['MA200'] = df['Close'].rolling(200).mean()

# MACD
ema12 = df['Close'].ewm(span=12, adjust=False).mean()
ema26 = df['Close'].ewm(span=26, adjust=False).mean()
df['MACD']        = ema12 - ema26
df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

return df
```

def jmia_signal(df):
“””
JMIA：逆張り特化シグナル
① %K・%D がともに10以下（強い売られすぎ）
② %K が %D をゴールデンクロス（反転確認）
③ ボラティリティが一定以上（ヒゲが大きい局面のみ）
④ MACD がシグナル線を上抜け（モメンタム確認）
“””
oversold  = (df[‘STOCHk’] <= 10) & (df[‘STOCHd’] <= 10)
cross_up  = (df[‘STOCHk’].shift(1) < df[‘STOCHd’].shift(1)) & (df[‘STOCHk’] > df[‘STOCHd’])
vol_ok    = ((df[‘High’] - df[‘Low’]) / df[‘Close’].replace(0, 1)) > 0.05
macd_up   = (df[‘MACD’].shift(1) < df[‘MACD_signal’].shift(1)) & (df[‘MACD’] > df[‘MACD_signal’])

```
return oversold & cross_up & vol_ok & macd_up
```

def nu_signal(df):
“””
NU：トレンド押し目シグナル
① MA50 > MA200（上昇トレンド中）かつ 終値 > MA50（トレンド上位）
② %K が20〜30のゾーン（軽い押し目）
③ %K が %D をゴールデンクロス（反転確認）
④ MACD > シグナル線（モメンタム継続）
“””
trend_ok = (df[‘MA50’] > df[‘MA200’]) & (df[‘Close’] > df[‘MA50’])
pullback = (df[‘STOCHk’] <= 30) & (df[‘STOCHk’] > 20)
cross_up = (df[‘STOCHk’].shift(1) < df[‘STOCHd’].shift(1)) & (df[‘STOCHk’] > df[‘STOCHd’])
macd_ok  = df[‘MACD’] > df[‘MACD_signal’]

```
return trend_ok & pullback & cross_up & macd_ok
```

# シンボルごとのシグナル関数・エグジット設定

SIGNAL_CONFIG = {
‘JMIA’: {
‘func’:        jmia_signal,
‘stop_loss’:   -0.07,   # -7% 損切り
‘stoch_exit’:  80,      # %K が80超で利確
‘trend_exit’:  False,
},
‘NU’: {
‘func’:        nu_signal,
‘stop_loss’:   -0.05,   # -5% 損切り
‘stoch_exit’:  None,
‘trend_exit’:  True,    # MA50を終値が下回ったら撤退
},
}

# =============================

# データ取得

# =============================

def get_stock_data(symbol, date_today_us):
“”“株価データ取得（米国営業日判定）”””
if date_today_us.weekday() >= 5 or date_today_us in US_HOLIDAYS:
print(f”Skipping {symbol}, US market closed”)
return None

```
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
        print(f"Error for {symbol} (attempt {attempt+1}): {e}")
        time.sleep(2)

if os.path.exists(filename):
    try:
        return pd.read_csv(filename, index_col=0, parse_dates=True)
    except Exception:
        return pd.DataFrame()
return pd.DataFrame()
```

# =============================

# エグジット判定（保有ポジション）

# =============================

def check_exit(symbol, buy_price, current_row, df):
“”“損切り・利確・トレンド割れを判定”””
cfg = SIGNAL_CONFIG.get(symbol, {})
current_price = float(current_row[‘Close’])
pnl = (current_price - buy_price) / buy_price if buy_price > 0 else 0

```
# 損切り
if pnl <= cfg.get('stop_loss', -0.07):
    return True, f"🛑 損切り ({pnl*100:.1f}%)"

# %K 利確（JMIA）
stoch_exit = cfg.get('stoch_exit')
if stoch_exit and float(current_row.get('STOCHk', 0)) >= stoch_exit:
    return True, f"✅ 利確 ストキャス過熱 ({pnl*100:.1f}%)"

# トレンド割れ（NU）
if cfg.get('trend_exit') and 'MA50' in current_row.index:
    if current_price < float(current_row['MA50']):
        return True, f"📉 トレンド割れ撤退 ({pnl*100:.1f}%)"

return False, ""
```

# =============================

# メイン処理

# =============================

def main():
print(”— 🚀 Execution Started —”)

```
now_jst   = datetime.datetime.now(JST)
today_jst = now_jst.date()
now_us    = now_jst.astimezone(US_EAST)
today_us  = now_us.date()

# CSV読み込み
cols = ['Date', 'Symbol', 'Status', 'Buy_Price']
if os.path.exists(CSV_FILE):
    try:
        trade_log = pd.read_csv(CSV_FILE)
        trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0)
        trade_log['Status']    = trade_log['Status'].astype(str).str.strip()
    except Exception as e:
        print(f"CSV read error: {e}")
        trade_log = pd.DataFrame(columns=cols)
else:
    trade_log = pd.DataFrame(columns=cols)

notifications = []
symbol_status = []
exit_notifications = []

for symbol in SYMBOLS:
    df = get_stock_data(symbol, today_us)
    if df is None or df.empty:
        symbol_status.append(f"【{symbol}】\n⚠️ 米国市場休場またはデータ取得失敗")
        continue

    valid_df = df.dropna(subset=['Close']).copy()
    if len(valid_df) < 200:
        symbol_status.append(f"【{symbol}】\n⚠️ データ不足（{len(valid_df)}件）")
        continue

    # 指標計算
    valid_df = add_indicators(valid_df)
    sig_func = SIGNAL_CONFIG[symbol]['func']
    valid_df['buy_signal'] = sig_func(valid_df)

    last_row     = valid_df.tail(1).squeeze()
    last_date_str = valid_df.index[-1].strftime('%Y-%m-%d')
    current_price = float(last_row['Close'])

    # --- 前日のシグナルを当日始値で約定 ---
    mask_signal = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
    if mask_signal.any():
        trade_log.loc[mask_signal, 'Buy_Price'] = float(last_row['Open'])
        trade_log.loc[mask_signal, 'Status']    = 'holding'

    # --- 保有ポジションのエグジット確認 ---
    holdings_mask = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')
    if holdings_mask.any():
        for idx in trade_log[holdings_mask].index:
            buy_price = float(trade_log.at[idx, 'Buy_Price'])
            should_exit, reason = check_exit(symbol, buy_price, last_row, valid_df)
            if should_exit:
                trade_log.at[idx, 'Status'] = 'closed'
                exit_notifications.append(f"🔔 **{symbol}** エグジット: {reason}")

    # --- 新規シグナル判定 ---
    # 冷却期間チェック：直近5営業日以内にシグナル・保有・クローズがあればスキップ
    recent_cutoff = (valid_df.index[-1] - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
    recent_trades = trade_log[
        (trade_log['Symbol'] == symbol) &
        (trade_log['Date'] >= recent_cutoff)
    ]
    cooldown_active = not recent_trades.empty

    if bool(last_row['buy_signal']) and not cooldown_active:
        exists = trade_log[
            (trade_log['Date'] == last_date_str) & (trade_log['Symbol'] == symbol)
        ].any().any()
        if not exists:
            new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0}
            trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
            notifications.append(f"🚨 **買いシグナル発生**: {symbol}（{last_date_str}）")

    # --- 保有状況集計 ---
    holdings = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
    num_shares  = len(holdings)
    current_val = current_price * num_shares
    cost_basis  = holdings['Buy_Price'].sum()
    profit_str  = f"${(current_val - cost_basis):+.2f}"
    stoch_str   = f"%K={last_row['STOCHk']:.1f} %D={last_row['STOCHd']:.1f}"

    symbol_status.append(
        f"【{symbol}】\n"
        f"保有数: {num_shares}株\n"
        f"評価額: ${current_val:.2f}（損益: {profit_str}）\n"
        f"現在値: ${current_price:.2f} | {stoch_str}"
    )

# CSV更新
trade_log.to_csv(CSV_FILE, index=False)

# 通知作成
msg  = f"📅 **{today_jst} トレード報告**\n\n"

msg += "📢 **シグナル判定**\n"
msg += "\n".join(notifications) if notifications else "✅ シグナルなし"

if exit_notifications:
    msg += "\n\n🔔 **エグジット通知**\n" + "\n".join(exit_notifications)

msg += "\n\n📊 **保有銘柄状況**\n" + "\n\n".join(symbol_status)

# 週次レポート（土曜JST）
if today_jst.weekday() == 5:
    monday = today_jst - datetime.timedelta(days=today_jst.weekday())
    friday = monday + datetime.timedelta(days=4)
    weekly = trade_log[
        (trade_log['Date'] >= str(monday)) &
        (trade_log['Date'] <= str(friday)) &
        (trade_log['Status'].isin(['holding', 'closed']))
    ]
    msg += "\n\n📜 **【週報】今週の取引履歴**\n"
    if not weekly.empty:
        weekly = weekly.sort_values('Date')
        msg += "\n".join(
            [f"・{r['Date']} : {r['Symbol']} ${float(r['Buy_Price']):.2f} [{r['Status']}]"
             for _, r in weekly.iterrows()]
        )
    else:
        msg += "今週の取引はありません。"

# 前営業日通知（日曜・祝日JST）
if today_jst.weekday() == 6 or today_jst in holidays.Japan():
    msg += "\n\n📌 **前営業日データの通知**"

# Discord送信
if DISCORD_WEBHOOK_URL:
    SyncWebhook.from_url(DISCORD_WEBHOOK_URL).send(msg)
    print("Discord notification sent.")

print(msg)
print("--- ✅ Execution Finished ---")
```

if **name** == “**main**”:
main()
