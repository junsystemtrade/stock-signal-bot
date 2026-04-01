“””
backtest.py  ── main.py と完全に同じシグナルロジックを使用
“””

import yfinance as yf
import pandas as pd
import numpy as np

# =============================

# 共通指標計算（main.py と共通）

# =============================

def add_indicators(df):
“”“ストキャスティクス・移動平均・MACDを追加”””
low_14  = df[‘Low’].rolling(14).min()
high_14 = df[‘High’].rolling(14).max()
df[‘STOCHk’] = 100 * ((df[‘Close’] - low_14) / (high_14 - low_14).replace(0, 1))
df[‘STOCHd’] = df[‘STOCHk’].rolling(3).mean()

```
df['MA50']  = df['Close'].rolling(50).mean()
df['MA200'] = df['Close'].rolling(200).mean()

ema12 = df['Close'].ewm(span=12, adjust=False).mean()
ema26 = df['Close'].ewm(span=26, adjust=False).mean()
df['MACD']        = ema12 - ema26
df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

return df
```

# =============================

# シグナル関数（main.py と同一）

# =============================

def jmia_signal(df):
“””
JMIA：逆張り特化
① %K・%D ともに10以下
② ゴールデンクロス
③ ボラティリティ > 5%
④ MACD がシグナル上抜け
“””
oversold = (df[‘STOCHk’] <= 10) & (df[‘STOCHd’] <= 10)
cross_up = (df[‘STOCHk’].shift(1) < df[‘STOCHd’].shift(1)) & (df[‘STOCHk’] > df[‘STOCHd’])
vol_ok   = ((df[‘High’] - df[‘Low’]) / df[‘Close’].replace(0, 1)) > 0.05
macd_up  = (df[‘MACD’].shift(1) < df[‘MACD_signal’].shift(1)) & (df[‘MACD’] > df[‘MACD_signal’])
return oversold & cross_up & vol_ok & macd_up

def nu_signal(df):
“””
NU：トレンド押し目
① MA50 > MA200 かつ 終値 > MA50
② %K が20〜30ゾーン
③ ゴールデンクロス
④ MACD > シグナル線
“””
trend_ok = (df[‘MA50’] > df[‘MA200’]) & (df[‘Close’] > df[‘MA50’])
pullback = (df[‘STOCHk’] <= 30) & (df[‘STOCHk’] > 20)
cross_up = (df[‘STOCHk’].shift(1) < df[‘STOCHd’].shift(1)) & (df[‘STOCHk’] > df[‘STOCHd’])
macd_ok  = df[‘MACD’] > df[‘MACD_signal’]
return trend_ok & pullback & cross_up & macd_ok

# =============================

# バックテスト本体

# =============================

def backtest(symbol, signal_func, stop_loss, stoch_exit=None, trend_exit=False,
cooldown_days=7, years=5):
“””
Parameters
–––––
symbol       : ティッカー
signal_func  : シグナル関数
stop_loss    : 損切りライン（例 -0.07 = -7%）
stoch_exit   : %K がこの値以上で利確（JMIA用）
trend_exit   : True なら終値 < MA50 で撤退（NU用）
cooldown_days: シグナル後の冷却期間（営業日）
years        : バックテスト期間（年）
“””
df = yf.download(symbol, period=f”{years}y”, progress=False)
if isinstance(df.columns, pd.MultiIndex):
df.columns = df.columns.get_level_values(0)
df = add_indicators(df)
df[‘signal’] = signal_func(df)

```
position      = None
last_exit_idx = -cooldown_days  # 冷却期間管理
trades        = []

for i in range(1, len(df)):
    today     = df.iloc[i]
    yesterday = df.iloc[i - 1]

    # --- エントリー ---
    cooldown_ok = (i - last_exit_idx) >= cooldown_days
    if position is None and yesterday['signal'] and cooldown_ok:
        position = {
            'entry_idx':   i,
            'entry_date':  today.name,
            'entry_price': float(today['Open']),
        }

    # --- エグジット ---
    if position:
        pnl = (float(today['Close']) - position['entry_price']) / position['entry_price']
        exit_flag   = False
        exit_reason = ''

        if pnl <= stop_loss:
            exit_flag, exit_reason = True, 'stop_loss'

        if stoch_exit and float(yesterday['STOCHk']) >= stoch_exit:
            exit_flag, exit_reason = True, 'stoch_profit'

        if trend_exit and float(today['Close']) < float(today['MA50']):
            exit_flag, exit_reason = True, 'trend_break'

        if exit_flag:
            trades.append({
                'Symbol':      symbol,
                'Entry_Date':  position['entry_date'],
                'Exit_Date':   today.name,
                'Entry_Price': position['entry_price'],
                'Exit_Price':  float(today['Close']),
                'PnL_%':       round(pnl * 100, 2),
                'Hold_Days':   (today.name - position['entry_date']).days,
                'Exit_Reason': exit_reason,
            })
            position      = None
            last_exit_idx = i

return pd.DataFrame(trades)
```

# =============================

# 結果集計

# =============================

def summarize(df, symbol=’’):
if df.empty:
print(f”  トレードなし”)
return

```
wins        = df[df['PnL_%'] > 0]
losses      = df[df['PnL_%'] <= 0]
win_rate    = len(wins) / len(df) * 100
avg_win     = wins['PnL_%'].mean()   if not wins.empty   else 0
avg_loss    = losses['PnL_%'].mean() if not losses.empty else 0
profit_factor = abs(wins['PnL_%'].sum() / losses['PnL_%'].sum()) if not losses.empty else float('inf')

print(f"  トレード数      : {len(df)}")
print(f"  勝率           : {win_rate:.1f}%")
print(f"  平均損益       : {df['PnL_%'].mean():.2f}%")
print(f"  平均利益       : {avg_win:.2f}%  |  平均損失: {avg_loss:.2f}%")
print(f"  プロフィットF  : {profit_factor:.2f}")
print(f"  最大損失       : {df['PnL_%'].min():.2f}%")
print(f"  累計リターン   : {df['PnL_%'].sum():.2f}%")
print(f"  平均保有日数   : {df['Hold_Days'].mean():.1f}日")

print("\n  エグジット内訳:")
for reason, cnt in df['Exit_Reason'].value_counts().items():
    avg_pnl = df[df['Exit_Reason'] == reason]['PnL_%'].mean()
    print(f"    {reason:15s} : {cnt}回  平均PnL {avg_pnl:.2f}%")

print("\n  直近10トレード:")
cols = ['Entry_Date', 'Exit_Date', 'Entry_Price', 'Exit_Price', 'PnL_%', 'Exit_Reason']
print(df.tail(10)[cols].to_string(index=False))
```

# =============================

# 実行

# =============================

if **name** == “**main**”:
print(”=” * 50)
print(”  JMIA バックテスト（逆張り特化・5年）”)
print(”=” * 50)
jmia_trades = backtest(
symbol        = ‘JMIA’,
signal_func   = jmia_signal,
stop_loss     = -0.07,
stoch_exit    = 80,
cooldown_days = 7,
years         = 5,
)
summarize(jmia_trades, ‘JMIA’)

```
print()
print("=" * 50)
print("  NU バックテスト（トレンド押し目・5年）")
print("=" * 50)
nu_trades = backtest(
    symbol        = 'NU',
    signal_func   = nu_signal,
    stop_loss     = -0.05,
    trend_exit    = True,
    cooldown_days = 7,
    years         = 5,
)
summarize(nu_trades, 'NU')
```
