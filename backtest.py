import pandas as pd
import yfinance as yf
import holidays
from datetime import datetime

# --- シグナルロジック（メインソースと同期） ---

def add_indicators(df):
    df = df.copy()
    # ストキャスティクス（期間10）
    low_10   = df['Low'].rolling(10).min()
    high_10  = df['High'].rolling(10).max()
    range_10 = high_10 - low_10
    df['STOCHk'] = 100 * ((df['Close'] - low_10) / range_10.where(range_10.abs() > 1e-10, other=pd.NA)).fillna(50)
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    # RSI（期間14: Wilderの平滑化EWM）
    delta    = df['Close'].diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + avg_gain / avg_loss.clip(lower=1e-10)))
    # 移動平均線
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    # MACD（12/26/9）
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']        = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

def _composite_signal(df, base_condition=None):
    """複合シグナル判定。'strong' / 'medium' / '' を返す Series。
    条件A (strong): STOCHk≤20 AND RSI≤35
    条件B (medium): MACDゴールデンクロス AND (STOCHk≤20 OR RSI≤35)
    """
    stoch_oversold = (df['STOCHk'] <= 20) | (df['STOCHd'] <= 20)
    stoch_cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    stoch_ok   = stoch_oversold & stoch_cross_up
    rsi_ok     = df['RSI'] <= 35
    macd_cross = (df['MACD'] > df['MACD_signal']) & (df['MACD'].shift(1) <= df['MACD_signal'].shift(1))

    cond_a = stoch_ok & rsi_ok
    cond_b = macd_cross & (stoch_ok | rsi_ok)

    if base_condition is not None:
        cond_a = cond_a & base_condition
        cond_b = cond_b & base_condition

    strength = pd.Series('', index=df.index)
    strength = strength.where(~cond_b, 'medium')
    strength = strength.where(~cond_a, 'strong')
    return strength


def get_jmia_signal(df):
    vol_ok = ((df['High'] - df['Low']) / df['Close'].replace(0, 1)) > 0.03
    return _composite_signal(df, base_condition=vol_ok)

def get_nu_signal(df):
    trend_ok = (df['MA50'] > df['MA200']) & (df['Close'] > df['MA50'])
    return _composite_signal(df, base_condition=trend_ok)

# --- バックテスト実行 ---

def run_backtest(symbol, signal_func):
    print(f"--- 【{symbol}】 バックテスト開始 ---")
    df = yf.download(symbol, period='1y', progress=False)
    if df.empty:
        print(f"【エラー】{symbol} のデータ取得に失敗しました。")
        return
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df['signal_strength'] = signal_func(df)
    df['signal']          = df['signal_strength'] != ''

    trades = []
    buy_dates = []
    in_position = False
    buy_price  = 0
    buy_date   = None
    buy_strength = ''

    for i in range(1, len(df)):
        # 冷却期間（7日）チェック — Timestamp 同士で比較
        cutoff = df.index[i] - pd.Timedelta(days=7)
        recent_trade = any(bd > cutoff for bd in buy_dates)

        # 買い実行（シグナル翌日の始値）
        if df['signal'].iloc[i-1] and not in_position and not recent_trade:
            buy_price    = df['Open'].iloc[i]
            buy_date     = df.index[i]
            buy_strength = df['signal_strength'].iloc[i-1]
            buy_dates.append(buy_date)
            in_position  = True

        # 簡易エグジット（購入から5営業日後に売却）
        elif in_position and len(df.iloc[:i]) - len(df.loc[:buy_date]) >= 5:
            sell_price = df['Close'].iloc[i]
            profit     = sell_price - buy_price
            icon       = '🔴' if buy_strength == 'strong' else '🟡'
            trades.append({
                '強度':      f"{icon} {buy_strength}",
                '購入日':    buy_date.strftime('%Y-%m-%d'),
                '購入単価':  round(buy_price, 2),
                '売却日':    df.index[i].strftime('%Y-%m-%d'),
                '売却単価':  round(sell_price, 2),
                '利益':      round(profit, 2),
                '騰落率(%)': round((profit / buy_price) * 100, 2),
            })
            in_position = False

    results = pd.DataFrame(trades)
    if not results.empty:
        print(results.to_string(index=False))
        print("-" * 30)
        print(f"合計取引数  : {len(results)}回")
        print(f"勝率        : {(results['利益'] > 0).mean():.2%}")
        strong_r = results[results['強度'].str.contains('strong')]
        medium_r = results[results['強度'].str.contains('medium')]
        print(f"  強シグナル: {len(strong_r)}回 / 勝率 {(strong_r['利益'] > 0).mean():.2%}" if not strong_r.empty else "  強シグナル: 0回")
        print(f"  中シグナル: {len(medium_r)}回 / 勝率 {(medium_r['利益'] > 0).mean():.2%}" if not medium_r.empty else "  中シグナル: 0回")
        print(f"累計損益    : ${results['利益'].sum():.2f}")
    else:
        print("過去1年間でシグナルは検出されませんでした。")
    print("\n")

if __name__ == "__main__":
    run_backtest('JMIA', get_jmia_signal)
    run_backtest('NU', get_nu_signal)
