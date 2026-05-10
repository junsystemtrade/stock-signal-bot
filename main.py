import os
import sys
import datetime
import pandas as pd
import yfinance as yf
from discord import SyncWebhook
import pytz
import time
import holidays

# Windows環境でのUTF-8出力を強制
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- 設定 ---
SYMBOLS = ['JMIA', 'NU']
CSV_FILE = 'trade_history.csv'
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

# タイムゾーン
JST = pytz.timezone('Asia/Tokyo')
US_EAST = pytz.timezone('US/Eastern')

# 米国祝日
US_HOLIDAYS = holidays.US()

# =============================
# 共通指標計算
# =============================

def add_indicators(df):
    low_14  = df['Low'].rolling(14).min()
    high_14 = df['High'].rolling(14).max()
    range_14 = high_14 - low_14
    # ストキャスティクス計算（range=0の場合は中立値50を使用）
    df['STOCHk'] = 100 * ((df['Close'] - low_14) / range_14.where(range_14.abs() > 1e-10, other=pd.NA)).fillna(50)
    df['STOCHd'] = df['STOCHk'].rolling(3).mean()
    # 移動平均線
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    # MACD
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']        = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df


def jmia_signal(df):
    """JMIA: 逆張り反転シグナル (緩和版)"""
    oversold = (df['STOCHk'] <= 20) | (df['STOCHd'] <= 20)
    cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    vol_ok   = ((df['High'] - df['Low']) / df['Close'].replace(0, 1)) > 0.03
    macd_up  = df['MACD'] > df['MACD_signal']
    return oversold & cross_up & vol_ok & macd_up


def nu_signal(df):
    """NU: 上昇トレンド中の押し目買い (緩和版)"""
    trend_ok = (df['MA50'] > df['MA200']) & (df['Close'] > df['MA50'])
    pullback = (df['STOCHk'] <= 40) & (df['STOCHk'] > 15)
    cross_up = (df['STOCHk'] > df['STOCHd']) & (df['STOCHk'].shift(1) <= df['STOCHd'].shift(1))
    macd_ok  = df['MACD'] > df['MACD_signal']
    return trend_ok & pullback & cross_up & macd_ok


SIGNAL_CONFIG = {
    'JMIA': {'func': jmia_signal},
    'NU':   {'func': nu_signal},
}

# =============================
# データ取得
# =============================

def get_stock_data(symbol, date_today_us):
    # 土日または米国祝日はスキップ
    if date_today_us.weekday() >= 5 or date_today_us in US_HOLIDAYS:
        print(f"【情報】米国市場休場のためスキップ: {symbol}")
        return None

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
            print(f"【エラー】データ取得失敗 {symbol} (試行 {attempt+1}/3): {e}")
            time.sleep(2)

    if os.path.exists(filename):
        try:
            print(f"【警告】最新データ取得失敗。ローカルキャッシュを利用します: {symbol}")
            return pd.read_csv(filename, index_col=0, parse_dates=True)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


# =============================
# メイン処理
# =============================

def main():
    print("--- 株価チェック処理開始 ---")

    now_jst   = datetime.datetime.now(JST)
    today_jst = now_jst.date()
    now_us    = now_jst.astimezone(US_EAST)
    today_us  = now_us.date()

    # CSVの読み込みまたは新規作成
    cols = ['Date', 'Symbol', 'Status', 'Buy_Price', 'Shares']
    if os.path.exists(CSV_FILE):
        try:
            trade_log = pd.read_csv(CSV_FILE)
            trade_log['Buy_Price'] = pd.to_numeric(trade_log['Buy_Price'], errors='coerce').fillna(0.0)
            trade_log['Status']    = trade_log['Status'].astype(str).str.strip()
            trade_log['Date']      = trade_log['Date'].astype(str)
            # Shares列が存在しない場合は1で補完（後方互換）
            if 'Shares' not in trade_log.columns:
                trade_log['Shares'] = 1
            trade_log['Shares'] = pd.to_numeric(trade_log['Shares'], errors='coerce').fillna(1).astype(int)
        except Exception as e:
            print(f"【エラー】CSV読み込みエラー: {e}")
            trade_log = pd.DataFrame(columns=cols)
    else:
        trade_log = pd.DataFrame(columns=cols)

    notifications = []
    symbol_status = []

    for symbol in SYMBOLS:
        df = get_stock_data(symbol, today_us)
        if df is None or df.empty:
            symbol_status.append(f"【{symbol}】\n⚠️ 市場休場またはデータ取得失敗")
            continue

        valid_df = df.dropna(subset=['Close']).copy()
        if len(valid_df) < 200:
            symbol_status.append(f"【{symbol}】\n⚠️ 指標計算に必要なデータ不足 (最低200日分必要)")
            continue

        # 指標とシグナルの計算
        valid_df = add_indicators(valid_df)
        valid_df['buy_signal'] = SIGNAL_CONFIG[symbol]['func'](valid_df)

        last_row      = valid_df.tail(1).squeeze()
        last_date_str = valid_df.index[-1].strftime('%Y-%m-%d')
        current_price = float(last_row['Close'])

        # --- トレードロジック ---
        
        # 1. 前日に発生したシグナルを今日の始値で「約定(holding)」に変更
        mask_signal = (trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'signal')
        if mask_signal.any():
            trade_log.loc[mask_signal, 'Buy_Price'] = float(last_row['Open'])
            trade_log.loc[mask_signal, 'Status']    = 'holding'
            print(f"【約定】{symbol} を始値 ${last_row['Open']:.2f} で保有ステータスに更新しました。")

        # 2. 冷却期間チェック（直近7日以内に取引があれば新規シグナルを無視）
        recent_cutoff = (valid_df.index[-1] - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        recent_trades = trade_log[
            (trade_log['Symbol'] == symbol) &
            (trade_log['Date'] >= recent_cutoff)
        ]
        cooldown_active = not recent_trades.empty

        # 3. 新規買いシグナルの判定
        if bool(last_row['buy_signal']) and not cooldown_active:
            # 重複登録防止（同日・同銘柄・未約定シグナルの二重登録を防ぐ）
            exists = not trade_log[
                (trade_log['Date'] == last_date_str) &
                (trade_log['Symbol'] == symbol) &
                (trade_log['Status'].isin(['signal', 'holding']))
            ].empty
            if not exists:
                new_row = {'Date': last_date_str, 'Symbol': symbol, 'Status': 'signal', 'Buy_Price': 0.0, 'Shares': 1}
                trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                notifications.append(f"🚨 **買いシグナル発生**: {symbol}")
                print(f"【シグナル】{symbol} の買いシグナルを記録しました。")

        # 4. 現在の保有状況の集計
        holdings    = trade_log[(trade_log['Symbol'] == symbol) & (trade_log['Status'] == 'holding')]
        num_shares  = int(holdings['Shares'].sum()) if not holdings.empty else 0
        current_val = current_price * num_shares
        cost_basis  = (holdings['Buy_Price'] * holdings['Shares']).sum() if not holdings.empty else 0.0
        profit_loss = current_val - cost_basis
        profit_str  = f"${profit_loss:+.2f}"

        symbol_status.append(
            f"【{symbol}】\n現在の株価: ${current_price:.2f}\n保有数: {num_shares}株\n評価額: ${current_val:.2f} (合計損益: {profit_str})"
        )

    # 重複を削除して保存
    trade_log = trade_log.drop_duplicates(subset=['Date', 'Symbol', 'Status'], keep='first')
    trade_log.to_csv(CSV_FILE, index=False)

    # --- 通知メッセージ作成 ---
    msg = f"📅 **{today_jst} トレード報告**\n\n"
    msg += "📢 **シグナル判定**\n"
    msg += "\n".join(notifications) if notifications else "✅ 新規シグナルはありません"
    msg += "\n\n📊 **現在のステータス**\n" + "\n\n".join(symbol_status)

    # 週次レポート（土曜日のみ）
    if today_jst.weekday() == 5:
        monday = (today_jst - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
        weekly = trade_log[
            (trade_log['Date'] >= monday) &
            (trade_log['Status'] == 'holding')
        ]
        msg += "\n\n📜 **【週報】今週の新規約定一覧**\n"
        if not weekly.empty:
            msg += "\n".join([f"・{r['Date']} : {r['Symbol']} 取得単価 ${float(r['Buy_Price']):.2f}" for _, r in weekly.iterrows()])
        else:
            msg += "今週の新規約定はありませんでした。"

    # 市場休場日の補足
    if today_jst.weekday() == 6 or today_jst in holidays.Japan():
        msg += "\n\n📌 ※本日は日本の休日のため、前営業日時点のデータに基づいています。"

    # --- Discord送信 ---
    if DISCORD_WEBHOOK_URL:
        try:
            webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
            chunk_size = 1900
            for i in range(0, len(msg), chunk_size):
                webhook.send(msg[i:i+chunk_size])
            print("【完了】Discord通知を送信しました。")
        except Exception as e:
            print(f"【エラー】Discord送信に失敗しました: {e}")

    print("\n--- 送信内容 ---")
    print(msg)
    print("\n--- 処理終了 ---")


if __name__ == "__main__":
    main()
