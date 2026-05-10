import sys
import pandas as pd
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 銘柄別 RSI 閾値（JMIA のみ使用）
RSI_THRESHOLDS = {
    'JMIA': 35,
}

# 前回バックテスト結果（weak含む・10日エグジット）
PREV_RESULTS = {
    'JMIA': {'total': 13, 'strong': 0, 'medium': 4, 'weak': 9, 'pnl': 2.85},
    'NU':   {'total': 0,  'strong': 0, 'medium': 0, 'weak': 0, 'pnl': 0.0},
}

EXIT_DAYS = 10

# =============================
# 指標計算（main.py と同期）
# =============================

def add_indicators(df):
    df = df.copy()
    low_10   = df['Low'].rolling(10).min()
    high_10  = df['High'].rolling(10).max()
    range_10 = high_10 - low_10
    df['STOCHk'] = 100 * ((df['Close'] - low_10) / range_10.where(range_10.abs() > 1e-10, other=pd.NA)).fillna(50)
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    delta    = df['Close'].diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + avg_gain / avg_loss.clip(lower=1e-10)))
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']        = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df


# =============================
# シグナル関数（main.py と同期）
# =============================

def _composite_signal(df, rsi_threshold=35, base_condition=None):
    """JMIA 用。strong / medium / '' を返す。"""
    stoch_oversold = (df['STOCHk'] <= 20) | (df['STOCHd'] <= 20)
    stoch_cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    stoch_ok   = stoch_oversold & stoch_cross_up
    rsi_ok     = df['RSI'] <= rsi_threshold
    macd_cross = (df['MACD'] > df['MACD_signal']) & (df['MACD'].shift(1) <= df['MACD_signal'].shift(1))

    cond_a = stoch_ok & rsi_ok
    cond_b = macd_cross

    if base_condition is not None:
        cond_a = cond_a & base_condition
        cond_b = cond_b & base_condition

    strength = pd.Series('', index=df.index)
    strength = strength.where(~cond_b, 'medium')
    strength = strength.where(~cond_a, 'strong')
    return strength


def get_jmia_signal(df):
    vol_ok = ((df['High'] - df['Low']) / df['Close'].replace(0, 1)) > 0.03
    return _composite_signal(df, rsi_threshold=RSI_THRESHOLDS['JMIA'], base_condition=vol_ok)


def get_nu_signal(df):
    """NU: トレンドフォロー型プルバック戦略（調整版）
    前提 : Close > MA200
    strong: RSI 40-55 AND STOCHk <= 40
    medium: MACDゴールデンクロス OR (STOCHk <= 40 AND RSI <= 50)
    優先順: strong > medium
    """
    above_ma200 = df['Close'] > df['MA200']
    macd_cross  = (df['MACD'] > df['MACD_signal']) & (df['MACD'].shift(1) <= df['MACD_signal'].shift(1))
    rsi_sweet   = (df['RSI'] >= 40) & (df['RSI'] <= 55)
    stoch_40    = df['STOCHk'] <= 40
    rsi_50      = df['RSI'] <= 50

    stoch_30    = df['STOCHk'] <= 30

    cond_a = above_ma200 & rsi_sweet & stoch_30                    # strong
    cond_b = above_ma200 & (macd_cross | (stoch_40 & rsi_50))     # medium

    strength = pd.Series('', index=df.index)
    strength = strength.where(~cond_b, 'medium')
    strength = strength.where(~cond_a, 'strong')
    return strength


# =============================
# バックテスト実行
# =============================

def _strength_icon(s):
    return '🔴' if s == 'strong' else '🟡'

def _tier_stats(label, subset):
    if subset.empty:
        return f"  {label}: 0回"
    wr  = (subset['利益'] > 0).mean()
    avg = subset['利益'].mean()
    tot = subset['利益'].sum()
    return f"  {label}: {len(subset)}回 / 勝率 {wr:.0%} / 平均 ${avg:+.2f} / 累計 ${tot:+.2f}"

BACKTEST_DAYS = 252  # バックテスト対象期間（営業日数 ≒ 1年）

def run_backtest(symbol, signal_func, min_strength='any'):
    """min_strength: 'any'=strong+medium両方実行 / 'strong'=strongのみ実行"""
    label = f"強度フィルター: {min_strength}" if min_strength != 'any' else "全強度"
    print(f"--- 【{symbol}】 バックテスト開始 (エグジット: {EXIT_DAYS}営業日 / {label}) ---")
    # MA200 を正確に計算するため 2 年分取得し、指標計算後に直近 1 年をスライス
    df_full = yf.download(symbol, period='2y', progress=False)
    if df_full.empty:
        print(f"【エラー】{symbol} のデータ取得に失敗しました。")
        return {'symbol': symbol, 'total': 0, 'strong': 0, 'medium': 0, 'pnl': 0.0}
    if isinstance(df_full.columns, pd.MultiIndex):
        df_full.columns = df_full.columns.get_level_values(0)

    df_full = add_indicators(df_full)
    df_full['signal_strength'] = signal_func(df_full)
    df_full['signal']          = df_full['signal_strength'] != ''

    # 直近 1 年分のみでバックテスト（MA200 が既に収束済み）
    df = df_full.iloc[-BACKTEST_DAYS:].copy()

    trades     = []
    buy_dates  = []
    in_position  = False
    buy_price    = 0
    buy_date     = None
    buy_strength = ''

    for i in range(1, len(df)):
        cutoff       = df.index[i] - pd.Timedelta(days=7)
        recent_trade = any(bd > cutoff for bd in buy_dates)

        sig = df['signal_strength'].iloc[i-1]
        signal_ok = (sig == 'strong') if min_strength == 'strong' else (sig != '')
        if signal_ok and not in_position and not recent_trade:
            buy_price    = df['Open'].iloc[i]
            buy_date     = df.index[i]
            buy_strength = df['signal_strength'].iloc[i-1]
            buy_dates.append(buy_date)
            in_position  = True

        elif in_position and len(df.iloc[:i]) - len(df.loc[:buy_date]) >= EXIT_DAYS:
            sell_price = df['Close'].iloc[i]
            profit     = sell_price - buy_price
            trades.append({
                '強度':      f"{_strength_icon(buy_strength)} {buy_strength}",
                '購入日':    buy_date.strftime('%Y-%m-%d'),
                '購入単価':  round(buy_price, 2),
                '売却日':    df.index[i].strftime('%Y-%m-%d'),
                '売却単価':  round(sell_price, 2),
                '利益':      round(profit, 2),
                '騰落率(%)': round((profit / buy_price) * 100, 2),
            })
            in_position = False

    results  = pd.DataFrame(trades)
    strong_r = results[results['強度'].str.contains('strong')] if not results.empty else pd.DataFrame()
    medium_r = results[results['強度'].str.contains('medium')] if not results.empty else pd.DataFrame()

    if not results.empty:
        print(results.to_string(index=False))
        print("-" * 52)
        total_wr = (results['利益'] > 0).mean()
        print(f"合計取引数  : {len(results)}回 / 勝率 {total_wr:.0%} / 累計損益 ${results['利益'].sum():+.2f}")
        print(_tier_stats('🔴 strong', strong_r))
        print(_tier_stats('🟡 medium', medium_r))
    else:
        print("過去1年間でシグナルは検出されませんでした。")

    print()
    return {
        'symbol': symbol,
        'total':  len(results),
        'strong': len(strong_r),
        'medium': len(medium_r),
        'pnl':    results['利益'].sum() if not results.empty else 0.0,
    }


# =============================
# メイン実行 & 比較出力
# =============================

if __name__ == "__main__":
    r_jmia = run_backtest('JMIA', get_jmia_signal)
    r_nu   = run_backtest('NU',   get_nu_signal, min_strength='strong')

    # ── strong / medium 件数・勝率・平均損益サマリー ──
    print("=" * 60)
    print("  強度別サマリー（JMIA + NU 合算）")
    print("=" * 60)
    fmt = f"{'銘柄':<6} {'合計':<6} {'strong':<8} {'medium':<8} {'累計損益'}"
    print(fmt)
    print("-" * 60)
    grand_total = grand_pnl = 0
    for r in [r_jmia, r_nu]:
        print(
            f"{r['symbol']:<6} {r['total']:<6} "
            f"{r['strong']:<8} {r['medium']:<8} "
            f"${r['pnl']:+.2f}"
        )
        grand_total += r['total']
        grand_pnl   += r['pnl']
    print("-" * 60)
    print(f"{'合計':<6} {grand_total:<6} {'':8} {'':8} ${grand_pnl:+.2f}")
    print()

    # ── weak 廃止による JMIA への影響 ──
    prev = PREV_RESULTS['JMIA']
    curr = r_jmia
    removed_weak  = prev['weak']
    removed_total = prev['total'] - curr['total']
    pnl_diff      = curr['pnl'] - prev['pnl']
    print("=" * 60)
    print("  weak 廃止による JMIA への影響")
    print("=" * 60)
    print(f"  前回 (weak含む) : {prev['total']}回  strong={prev['strong']} medium={prev['medium']} weak={prev['weak']}  累計 ${prev['pnl']:+.2f}")
    print(f"  今回 (weak廃止) : {curr['total']}回  strong={curr['strong']} medium={curr['medium']}          累計 ${curr['pnl']:+.2f}")
    print(f"  変化            : 取引数 {-removed_total:+d}回 (weak {removed_weak}件削除) / 損益差 ${pnl_diff:+.2f}")
    print("=" * 60)
