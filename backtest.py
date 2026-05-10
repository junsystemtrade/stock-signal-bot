import pandas as pd
import yfinance as yf
import holidays
from datetime import datetime

# --- シグナルロジック（メインソースと同期） ---

def add_indicators(df):
    df = df.copy()
    low_14   = df['Low'].rolling(14).min()
    high_14  = df['High'].rolling(14).max()
    range_14 = high_14 - low_14
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / range_14.where(range_14.abs() > 1e-10, other=pd.NA)).fillna(50)
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']        = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

def get_jmia_signal(df):
    oversold = (df['STOCHk'] <= 20) | (df['STOCHd'] <= 20)
    cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    vol_ok   = ((df['High'] - df['Low']) / df['Close'].replace(0, 1)) > 0.03
    macd_up  = df['MACD'] > df['MACD_signal']
    return oversold & cross_up & vol_ok & macd_up

def get_nu_signal(df):
    trend_ok = (df['MA50'] > df['MA200']) & (df['Close'] > df['MA50'])
    pullback = (df['STOCHk'] <= 40) & (df['STOCHk'] > 15)
    cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    macd_ok  = df['MACD'] > df['MACD_signal']
    return trend_ok & pullback & cross_up & macd_ok

# --- バックテスト実行 ---

def run_backtest(symbol, signal_func):
    print(f"--- 【{symbol}】 バックテスト開始 ---")
    df = yf.download(symbol, period='1y', progress=False)
    if df.empty:
        print(f"【エラー】{symbol} のデータ取得に失敗しました。")
        return
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = add_indicators(df)
    df['signal'] = signal_func(df)

    trades = []
    buy_dates = []  # 冷却期間チェック用（Timestamp で管理）
    in_position = False
    buy_price = 0
    buy_date  = None

    for i in range(1, len(df)):
        # 冷却期間（7日）チェック — Timestamp 同士で比較
        cutoff = df.index[i] - pd.Timedelta(days=7)
        recent_trade = any(bd > cutoff for bd in buy_dates)

        # 買い実行（シグナル翌日の始値）
        if df['signal'].iloc[i-1] and not in_position and not recent_trade:
            buy_price = df['Open'].iloc[i]
            buy_date  = df.index[i]
            buy_dates.append(buy_date)
            in_position = True

        # 簡易エグジット（購入から5営業日後に売却）
        elif in_position and len(df.iloc[:i]) - len(df.loc[:buy_date]) >= 5:
            sell_price = df['Close'].iloc[i]
            profit = sell_price - buy_price
            trades.append({
                '購入日':    buy_date.strftime('%Y-%m-%d'),
                '購入単価':  round(buy_price, 2),
                '売却日':    df.index[i].strftime('%Y-%m-%d'),
                '売却単価':  round(sell_price, 2),
                '利益':      round(profit, 2),
                '騰落率(%)': round((profit / buy_price) * 100, 2)
            })
            in_position = False

    results = pd.DataFrame(trades)
    if not results.empty:
        print(results.to_string(index=False))
        print("-" * 30)
        print(f"合計取引数  : {len(results)}回")
        print(f"勝率        : {(results['利益'] > 0).mean():.2%}")
        print(f"累計損益    : ${results['利益'].sum():.2f}")
    else:
        print("過去1年間でシグナルは検出されませんでした。")
    print("\n")

if __name__ == "__main__":
    run_backtest('JMIA', get_jmia_signal)
    run_backtest('NU', get_nu_signal)
