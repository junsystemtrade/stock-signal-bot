import pandas as pd
import yfinance as yf
import holidays
from datetime import datetime

# --- シグナルロジック（提示されたものと同期） ---

def add_indicators(df):
    df = df.copy()
    low_14 = df['Low'].rolling(14).min()
    high_14 = df['High'].rolling(14).max()
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14).replace(0, 1))
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

# --- バックテスト実行エンジン ---

def run_backtest(symbol, signal_func):
    print(f"--- Backtesting {symbol} ---")
    df = yf.download(symbol, period='1y', progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    df = add_indicators(df)
    df['signal'] = signal_func(df)
    
    trades = []
    in_position = False
    buy_price = 0
    
    for i in range(1, len(df)):
        # 冷却期間（7日）の簡易再現：直近7日にトレードがあればスキップ
        recent_trade = any(t['buy_date'] > df.index[i] - pd.Timedelta(days=7) for t in trades)
        
        # シグナル発生（翌日Openで買い）
        if df['signal'].iloc[i-1] and not in_position and not recent_trade:
            buy_price = df['Open'].iloc[i]
            buy_date = df.index[i]
            in_position = True
            
        # エグジットロジックがないため、便宜上5日後に売却して収益性を確認
        elif in_position and (df.index[i] - buy_date).days >= 5:
            sell_price = df['Close'].iloc[i]
            profit = sell_price - buy_price
            trades.append({
                'buy_date': buy_date,
                'buy_price': buy_price,
                'sell_date': df.index[i],
                'sell_price': sell_price,
                'profit': profit,
                'return_%': (profit / buy_price) * 100
            })
            in_position = False

    results = pd.DataFrame(trades)
    if not results.empty:
        print(results)
        print(f"Total Trades: {len(results)}")
        print(f"Win Rate: {(results['profit'] > 0).mean():.2%}")
        print(f"Total Return: ${results['profit'].sum():.2f}")
    else:
        print("No signals detected in the past year.")
    print("\n")

if __name__ == "__main__":
    run_backtest('JMIA', get_jmia_signal)
    run_backtest('NU', get_nu_signal)
