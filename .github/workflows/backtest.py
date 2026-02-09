import yfinance as yf
import pandas as pd
import numpy as np

# =========================
# 共通指標
# =========================

def add_stochastic(df):
    low_14 = df['Low'].rolling(14).min()
    high_14 = df['High'].rolling(14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) /
                           (high_14 - low_14).replace(0, 1))
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    return df


# =========================
# JMIA：逆張り特化
# =========================

def jmia_signal(df):
    oversold = (df['STOCHk'] <= 10) & (df['STOCHd'] <= 10)

    rebound = (
        (df['STOCHk'].shift(1) < df['STOCHd'].shift(1)) &
        (df['STOCHk'] > df['STOCHd'])
    )

    volatility_ok = ((df['High'] - df['Low']) / df['Close']) > 0.05

    return oversold & rebound & volatility_ok


# =========================
# NU：トレンド押し目
# =========================

def nu_signal(df):
    ma50 = df['Close'].rolling(50).mean()
    ma200 = df['Close'].rolling(200).mean()

    trend_ok = (ma50 > ma200) & (df['Close'] > ma50)

    pullback = (df['STOCHk'] <= 30) & (df['STOCHk'] > 20)

    rebound = (
        (df['STOCHk'].shift(1) < df['STOCHd'].shift(1)) &
        (df['STOCHk'] > df['STOCHd'])
    )

    return trend_ok & pullback & rebound


# =========================
# バックテスト本体
# =========================

def backtest(symbol, signal_func, stop_loss, take_profit=None, years=5):
    df = yf.download(symbol, period=f"{years}y", progress=False)
    df = add_stochastic(df)
    df['signal'] = signal_func(df)

    position = None
    trades = []

    for i in range(1, len(df)):
        today = df.iloc[i]
        yesterday = df.iloc[i - 1]

        # エントリー（翌日始値）
        if position is None and yesterday['signal']:
            position = {
                'entry_date': today.name,
                'entry_price': today['Open']
            }

        if position:
            pnl = (today['Close'] - position['entry_price']) / position['entry_price']

            exit_flag = False

            # 損切り
            if pnl <= stop_loss:
                exit_flag = True

            # 利確（JMIA）
            if take_profit is not None and yesterday['STOCHk'] >= take_profit:
                exit_flag = True

            # NU：トレンド割れ
            if symbol == 'NU':
                ma50 = df['Close'].rolling(50).mean().iloc[i]
                if today['Close'] < ma50:
                    exit_flag = True

            if exit_flag:
                trades.append({
                    'Symbol': symbol,
                    'Entry_Date': position['entry_date'],
                    'Exit_Date': today.name,
                    'Entry_Price': position['entry_price'],
                    'Exit_Price': today['Close'],
                    'PnL_%': pnl * 100
                })
                position = None

    return pd.DataFrame(trades)


# =========================
# 結果集計
# =========================

def summarize(trades):
    if trades.empty:
        return {
            'Trades': 0,
            'Win_Rate': 0,
            'Avg_PnL_%': 0,
            'Max_Loss_%': 0,
            'Total_Return_%': 0
        }

    return {
        'Trades': len(trades),
        'Win_Rate': round((trades['PnL_%'] > 0).mean() * 100, 2),
        'Avg_PnL_%': round(trades['PnL_%'].mean(), 2),
        'Max_Loss_%': round(trades['PnL_%'].min(), 2),
        'Total_Return_%': round(trades['PnL_%'].sum(), 2)
    }


# =========================
# 実行
# =========================

if __name__ == "__main__":

    jmia_trades = backtest(
        symbol='JMIA',
        signal_func=jmia_signal,
        stop_loss=-0.07,
        take_profit=80,
        years=5
    )

    nu_trades = backtest(
        symbol='NU',
        signal_func=nu_signal,
        stop_loss=-0.05,
        years=5
    )

    print("===== JMIA RESULT =====")
    print(summarize(jmia_trades))
    print()

    print("===== NU RESULT =====")
    print(summarize(nu_trades))
