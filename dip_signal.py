#!/usr/bin/env python3
"""抄底訊號面板 — S&P 500 / Nasdaq 100 寬度 + 回撤；另顯示 VIX×台股背離參考
美股抄底條件（同時成立才是訊號，不變）：
  1) S&P 500 與 Nasdaq 100 成分股「收盤價站上 20 日均線」占比皆 < 15%
  2) ^GSPC 與 ^NDX 自 52 週（252 交易日）收盤高點回撤皆介於 10%–20%
背離參考（僅顯示／提醒，不改美股 4 條件）：
  台股加權當日下跌（相對昨收 < 0）且 VIX 當日跌幅 ≥ 5% → 背離；≥ 8% → 強背離
台股來源優先證交所 TWSE；背離面板只顯示共同交易日（判定日）。\n安裝：python3 -m venv ~/DipSignal/.venv && ~/DipSignal/.venv/bin/pip install yfinance pandas lxml requests
用法：python dip_signal.py [--mode daily|intraday|rebuild] [--out 路徑.html] [--data-dir 資料夾] [--force]
  daily（預設）：完整重算美股寬度／回撤／BTC／VIX×台股，寫入 history 與面板
  intraday：僅更新台股（MIS 即時）與 VIX，重繪面板；非台股交易時段自動略過
  rebuild：不抓新數據，只依 history/meta 重繪桌面／widget／mobile
  背離判定永遠只用共同交易日（判定日），不改回各自最新日
"""
import argparse, html, io, os, sys, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd, requests, yfinance as yf

TPE = ZoneInfo("Asia/Taipei")
BREADTH_TH = 15.0
DD_LO, DD_HI = 10.0, 20.0
VIX_DIV_TH, VIX_STRONG_TH = 5.0, 8.0  # VIX 當日跌幅門檻（%）
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) DipSignal/1.0"}

# 台股盤中視窗（台北）：開盤 09:00 至收盤後緩衝 13:35
TW_SESSION_START = (9, 0)
TW_SESSION_END = (13, 35)
INTRADAY_INTERVAL_MIN = 30  # launchd 預設間隔；腳本本身不節流

def in_tw_session(now=None):
    """台股一般交易時段（含收盤後短暫緩衝）；週末一律 False。"""
    now = now or dt.datetime.now(TPE)
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return TW_SESSION_START <= hm <= TW_SESSION_END

def save_meta(path, meta):
    import json
    out = {}
    for k, v in meta.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "item"):
            out[k] = v.item()
        else:
            out[k] = v
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_meta(path):
    import json
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    meta = dict(raw)
    if "date" in meta and meta["date"]:
        meta["date"] = pd.Timestamp(meta["date"]).tz_localize(None).normalize()
    return meta

def refresh_vt_meta(vt_full, base=None):
    """依 history／最新 VT 表重算判定日與 wait；背離只認共同交易日。"""
    vt_meta = dict(base or {})
    if vt_full is None or vt_full.empty:
        vt_meta["wait"] = "尚無共同交易日"
        return vt_meta
    vix_latest = vt_full["vix"].dropna()
    tw_latest = vt_full["twii"].dropna()
    both_df = vt_full.dropna(subset=["vix_chg", "twii_chg"])
    vt_meta["vix_latest"] = vix_latest.index[-1] if len(vix_latest) else None
    vt_meta["twii_latest"] = tw_latest.index[-1] if len(tw_latest) else None
    vt_meta["judge_date"] = both_df.index[-1] if not both_df.empty else None
    jd, vd, td = vt_meta.get("judge_date"), vt_meta.get("vix_latest"), vt_meta.get("twii_latest")
    if jd is None:
        vt_meta["wait"] = "尚無共同交易日"
    elif vd is not None and td is not None and vd == td == jd:
        vt_meta["wait"] = None
    elif vd is not None and jd is not None and vd > jd and (td is None or td <= jd):
        vt_meta["wait"] = "等待台股交易日"
    elif td is not None and jd is not None and td > jd and (vd is None or vd <= jd):
        vt_meta["wait"] = "等待 VIX 交易日"
    elif vd != jd or td != jd:
        vt_meta["wait"] = "等待共同交易日"
    else:
        vt_meta["wait"] = None
    return vt_meta


def wiki_tables(url):
    r = requests.get(url, headers=UA, timeout=30); r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))

def find_col(tables, names):
    for t in tables:
        for c in t.columns:
            if str(c).strip() in names and len(t) > 90:
                return t[c].astype(str).str.strip().str.replace(".", "-", regex=False).tolist()
    raise RuntimeError("找不到成分股表格")

def constituents():
    spx = find_col(wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"), {"Symbol"})
    ndx = None
    for u in ("https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", "https://en.wikipedia.org/wiki/Nasdaq-100"):
        try:
            ndx = find_col(wiki_tables(u), {"Ticker", "Symbol"}); break
        except Exception:
            continue
    if not ndx: raise RuntimeError("無法取得 Nasdaq 100 成分股")
    return sorted(set(spx)), sorted(set(ndx))

def download_close(tickers, period):
    df = yf.download(tickers, period=period, auto_adjust=True, progress=False, threads=True)
    c = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    return c

def breadth_series(close, members):
    cols = [m for m in members if m in close.columns]
    c = close[cols]
    ma = c.rolling(20, min_periods=20).mean()
    valid = ma.notna() & c.notna()
    above = (c > ma) & valid
    n = valid.sum(axis=1)
    pct = (above.sum(axis=1) / n * 100).where(n >= 0.8 * len(members))
    return pct, n

def drawdown_series(s):
    hi = s.rolling(252, min_periods=200).max()
    return (1 - s / hi) * 100, hi

def compute():
    spx_m, ndx_m = constituents()
    allt = sorted(set(spx_m) | set(ndx_m))
    close = download_close(allt, "8mo")
    idx = download_close(["^GSPC", "^NDX"], "2y")
    sb, sn = breadth_series(close, spx_m)
    nb, nn = breadth_series(close, ndx_m)
    sd, shi = drawdown_series(idx["^GSPC"]); nd, nhi = drawdown_series(idx["^NDX"])
    df = pd.DataFrame({"spx_breadth": sb, "ndx_breadth": nb, "spx_dd": sd, "ndx_dd": nd}).dropna()
    df["signal"] = ((df.spx_breadth < BREADTH_TH) & (df.ndx_breadth < BREADTH_TH) &
                    df.spx_dd.between(DD_LO, DD_HI) & df.ndx_dd.between(DD_LO, DD_HI)).astype(int)
    last = df.index[-1]
    meta = dict(date=last, spx_n=int(sn[last]), spx_total=len(spx_m), ndx_n=int(nn[last]), ndx_total=len(ndx_m),
                spx_close=float(idx["^GSPC"][last]), ndx_close=float(idx["^NDX"][last]),
                spx_hi=float(shi[last]), ndx_hi=float(nhi[last]),
                missing=len([t for t in allt if t not in close.columns or close[t].dropna().empty]))
    return df.round(2), meta

VRP_URL = "https://charts-cdn.checkonchain.com/btconchain/cointime/pricing_mvrv_vaulted_1/pricing_mvrv_vaulted_1_light.html"

def fetch_vrp():
    """解析 Checkonchain『Vaulted Realized Price and MVRV』Plotly 圖表內嵌資料（Price、Vaulted Realized 兩條線）"""
    import base64, json
    import numpy as np
    r = requests.get(VRP_URL, headers=UA, timeout=60); r.raise_for_status()
    s = r.text; i = s.find("Plotly.newPlot("); j = s.find("[", i)
    traces, _ = json.JSONDecoder().raw_decode(s[j:])
    def arr(v):
        if isinstance(v, dict) and "bdata" in v:
            return np.frombuffer(base64.b64decode(v["bdata"]), dtype=np.dtype(v["dtype"])).tolist()
        return v
    got = {}
    for t in traces:
        if t.get("name") in ("Price", "Vaulted Realized"):
            got[t["name"]] = pd.Series(arr(t["y"]), index=pd.to_datetime(arr(t["x"])).normalize())
    if len(got) < 2: raise RuntimeError("圖表內找不到 Price / Vaulted Realized")
    df = pd.DataFrame({"btc_price": got["Price"], "btc_vrp": got["Vaulted Realized"]}).dropna()
    df = df[(df.btc_price > 0) & (df.btc_vrp > 0)].tail(400)
    df["btc_vmvrv"] = df.btc_price / df.btc_vrp
    return df.round({"btc_price": 2, "btc_vrp": 2, "btc_vmvrv": 4})


def _parse_twse_mi5mins_csv(text):
    """解析 TWSE MI_5MINS_HIST 英文 CSV → DateIndex + Closing Index Series"""
    import csv
    rows = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or row[0].strip().lower() == "date" or "Historical" in row[0] or "歷史" in row[0]:
            continue
        if len(row) < 5:
            continue
        d, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
        try:
            dtv = pd.to_datetime(d.replace("/", "-"))
            close = float(str(c).replace(",", ""))
        except Exception:
            continue
        rows.append((dtv.normalize(), close))
    if not rows:
        raise RuntimeError("TWSE CSV 無有效列")
    s = pd.Series({d: c for d, c in rows}, dtype=float).sort_index()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def fetch_twii_twse(months=9):
    """證交所發行量加權股價指數日收盤（MI_5MINS_HIST，按月抓）。"""
    now = dt.datetime.now(TPE)
    series = []
    for i in range(months):
        y = now.year
        m = now.month - i
        while m <= 0:
            m += 12
            y -= 1
        date_q = f"{y}{m:02d}01"
        url = f"https://www.twse.com.tw/en/indicesReport/MI_5MINS_HIST?response=csv&date={date_q}"
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        # 英文版標 charset=ms950，內容多為 ASCII；先試 utf-8 再 big5
        raw = r.content
        for enc in ("utf-8", "cp950", "big5"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                text = raw.decode("utf-8", errors="ignore")
        try:
            series.append(_parse_twse_mi5mins_csv(text))
        except Exception:
            continue
    if not series:
        raise RuntimeError("TWSE MI_5MINS_HIST 抓不到資料")
    out = pd.concat(series).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out.rename("twii")


def fetch_twii_mis_today():
    """證交所 MIS 即時／當日加權指數。回傳 (date, last_price, meta) 或 None。
    meta 含 prev_close、t（時間字串）、intraday（是否盤中即時價）。
    """
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    j = r.json()
    arr = j.get("msgArray") or []
    if not arr:
        return None
    row = arr[0]
    d = row.get("d") or row.get("^")
    z = row.get("z")
    y = row.get("y")  # 昨收
    t = row.get("t") or row.get("tt") or ""
    if not d or z in (None, "", "-"):
        return None
    day = pd.to_datetime(str(d)).normalize()
    close = float(str(z).replace(",", ""))
    prev = None
    try:
        if y not in (None, "", "-"):
            prev = float(str(y).replace(",", ""))
    except Exception:
        prev = None
    meta = {"prev_close": prev, "t": str(t), "intraday": in_tw_session()}
    return day, close, meta


def merge_twii_official_mis(official, mis):
    """官方收盤優先：MIS 只能補『晚於最後官方收盤日』的那一天，且標為暫估。
    official: Series 或 None；mis: (day, close, meta) 或 None。回傳 (Series, provisional_day 或 None)。"""
    s = official.copy() if official is not None else pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize() if len(s) else s.index
    prov = None
    if mis is not None:
        day, close, _ = mis
        day = pd.Timestamp(day).tz_localize(None).normalize() if pd.Timestamp(day).tzinfo else pd.Timestamp(day).normalize()
        last_off = s.index.max() if len(s) else None
        if last_off is None or day > last_off:
            s.loc[day] = float(close)
            prov = day
    return s.sort_index().astype(float), prov


def fetch_twii_close(prefer_mis_only=False):
    """台股加權：TWSE MI_5MINS_HIST 官方收盤優先；MIS 僅補官方尚未公布的當日（暫估），
    絕不覆蓋官方收盤。失敗退回 Yahoo ^TWII。回傳 (Series, source_label)。"""
    try:
        s = None if prefer_mis_only else fetch_twii_twse(months=9)
        mis = None
        try:
            mis = fetch_twii_mis_today()
        except Exception as e:
            print(f"⚠️ TWSE MIS 當日補值失敗：{e}", file=sys.stderr)
            if s is None:
                raise
        s, prov = merge_twii_official_mis(s, mis)
        if s.empty:
            raise RuntimeError("TWSE 無資料")
        src = "TWSE 官方收盤"
        if prov is not None:
            t = (mis[2].get("t") or "") if mis else ""
            src += f"＋MIS 暫估 {prov:%m/%d}" + (f" {t}" if t else "")
        return s, src
    except Exception as e:
        print(f"⚠️ TWSE 台股失敗，改用 Yahoo：{e}", file=sys.stderr)
        raw = download_close(["^TWII"], "8mo")
        return raw["^TWII"].dropna().astype(float), "Yahoo ^TWII（備援，可能延遲）"


CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def last_completed_us_session(now=None):
    """回傳已收盤的最後美股日期上限：美東 16:15 前，當天不算完成。"""
    from zoneinfo import ZoneInfo as _Z
    now = (now or dt.datetime.now(_Z("UTC"))).astimezone(_Z("America/New_York"))
    d = pd.Timestamp(now.date())
    return d if (now.hour, now.minute) >= (16, 15) else d - pd.Timedelta(days=1)


def only_completed_vix(s, now=None):
    return s[s.index <= last_completed_us_session(now)]


def parse_cboe_vix_csv(text):
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().upper() for c in df.columns]
    s = pd.Series(df["CLOSE"].astype(float).values, index=pd.to_datetime(df["DATE"], format="%m/%d/%Y").dt.normalize())
    return s.sort_index().rename("vix")


def fetch_vix_close():
    """美國 VIX 官方收盤：CBOE 歷史 CSV 優先，失敗退 Yahoo ^VIX；只保留已收盤交易日。"""
    try:
        r = requests.get(CBOE_VIX_URL, headers=UA, timeout=30); r.raise_for_status()
        s = parse_cboe_vix_csv(r.text)
        s = s[s.index >= s.index.max() - pd.Timedelta(days=260)]
        src = "CBOE 官方"
    except Exception as e:
        print(f"⚠️ CBOE VIX 失敗，改用 Yahoo：{e}", file=sys.stderr)
        s = download_close(["^VIX"], "8mo")["^VIX"].dropna().astype(float)
        src = "Yahoo ^VIX（CBOE 備援）"
    return only_completed_vix(s.dropna().astype(float)), src


def build_vt(vix, twii):
    """各自市場序列先算 pct_change，再依共同日期對齊；只有兩邊都有值才判定背離。"""
    vix_chg = vix.pct_change() * 100
    twii_chg = twii.pct_change() * 100
    df = pd.DataFrame({"vix": vix, "twii": twii, "vix_chg": vix_chg, "twii_chg": twii_chg})
    both = df["vix_chg"].notna() & df["twii_chg"].notna()
    div = pd.Series(0, index=df.index, dtype=int)
    down = both & (df["twii_chg"] < 0)
    div = div.mask(down & (df["vix_chg"] <= -VIX_DIV_TH), 1)
    div = div.mask(down & (df["vix_chg"] <= -VIX_STRONG_TH), 2)
    df["divergence"] = div.where(both)
    return df.round({"vix": 2, "twii": 2, "vix_chg": 2, "twii_chg": 2})


def fetch_vix_twii():
    """VIX（Yahoo）× 台股加權（優先 TWSE）日線；背離只用共同交易日。
    divergence：0 無／1 背離（台股跌且 VIX 跌≥5%）／2 強背離（台股跌且 VIX 跌≥8%）
    回傳 (DataFrame, meta_dict)
    """
    twii, tw_src = fetch_twii_close()
    vix, vix_src = fetch_vix_close()
    df = build_vt(vix, twii)
    vix_latest = df["vix"].dropna()
    tw_latest = df["twii"].dropna()
    both_df = df.dropna(subset=["vix_chg", "twii_chg"])
    judge = both_df.index[-1] if not both_df.empty else None
    meta = {
        "twii_source": tw_src,
        "vix_source": vix_src,
        "vix_latest": vix_latest.index[-1] if len(vix_latest) else None,
        "twii_latest": tw_latest.index[-1] if len(tw_latest) else None,
        "judge_date": judge,
    }
    if judge is not None and meta["vix_latest"] is not None and meta["twii_latest"] is not None:
        vd, td = meta["vix_latest"], meta["twii_latest"]
        if vd == td == judge:
            meta["wait"] = None
        elif vd > judge and td == judge:
            meta["wait"] = "等待台股交易日"
        elif td > judge and vd == judge:
            meta["wait"] = "等待 VIX 交易日"
        else:
            meta["wait"] = "等待共同交易日"
    else:
        meta["wait"] = "尚無共同交易日"
    return df, meta

VT_COLS = ["vix", "twii", "vix_chg", "twii_chg", "divergence"]
US_COLS = ["spx_breadth", "ndx_breadth", "spx_dd", "ndx_dd"]
BTC_COLS = ["btc_price", "btc_vrp", "btc_vmvrv"]

def spark2(a, b, w=300, h=40, ca="#ffb74d", cb="#00e676"):
    if len(a) < 2: return "<div class='na'>資料不足</div>"
    lo, hi = min(a + b), max(a + b); pad = (hi - lo) * 0.08 or 1; lo -= pad; hi += pad
    X = lambda i: 4 + i * (w - 8) / (len(a) - 1); Y = lambda y: h - 4 - (y - lo) / (hi - lo) * (h - 8)
    line = lambda v, c, sw: f"<polyline points='{' '.join(f'{X(i):.1f},{Y(y):.1f}' for i, y in enumerate(v))}' fill='none' stroke='{c}' stroke-width='{sw}'/>"
    return f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none'>{line(b, cb, 2)}{line(a, ca, 2)}<circle cx='{X(len(a)-1):.1f}' cy='{Y(a[-1]):.1f}' r='3.5' fill='{ca}'/></svg>"

def btc_block(btc, compact):
    if btc is None or btc.empty:
        return "<div class='btc'><div class='m'>BTC Vaulted Realized Price：資料暫時無法取得</div></div>"
    r = btc.iloc[-1]; b60 = btc.tail(60); above = r.btc_price >= r.btc_vrp
    pct = (r.btc_price / r.btc_vrp - 1) * 100
    pos = f"線上 +{pct:.1f}%" if above else f"線下 {pct:.1f}%"
    w, h = (300, 34) if compact else (1100, 110)
    return f"""<div class='btc'><div class='bh'><span>₿ BTC vs Vaulted Realized</span><span class='pos {'up' if above else 'dn'}'>{pos}</span></div>
<div class='bv'><div><small>BTC 價格</small><b style='color:#ffb74d'>${r.btc_price:,.0f}</b></div><div><small>Vaulted Realized</small><b style='color:#00e676'>${r.btc_vrp:,.0f}</b></div><div><small>Vaulted MVRV</small><b>{r.btc_vmvrv:.3f}</b></div></div>
{spark2(list(b60.btc_price), list(b60.btc_vrp), w=w, h=h)}<div class='m'>資料 {btc.index[-1]:%Y-%m-%d}｜Checkonchain｜僅供參考，不影響美股訊號</div></div>"""

def div_label(level):
    return {0: "無背離", 1: "背離訊號", 2: "強背離"}.get(int(level), "—")

def vix_twii_block(vt, compact, vt_meta=None):
    """面板只標「判定日」（共同交易日）兩邊收盤與漲跌；未成對則顯示等待對方。"""
    vt_meta = vt_meta or {}
    if vt is None or vt.empty:
        return "<div class='vt'><div class='m'>VIX × 台股背離：資料暫時無法取得</div></div>"
    both = vt.dropna(subset=["vix_chg", "twii_chg"])
    vix_latest = vt["vix"].dropna()
    tw_latest = vt["twii"].dropna()
    vix_d = vix_latest.index[-1] if len(vix_latest) else None
    tw_d = tw_latest.index[-1] if len(tw_latest) else None
    tw_ds = f"{tw_d:%m/%d}" if tw_d is not None else "—"
    vix_ds = f"{vix_d:%m/%d}" if vix_d is not None else "—"
    latest_line = f"台股最新 {tw_ds}｜美國 VIX 最新 {vix_ds}（{html.escape(str(vt_meta.get('vix_source') or 'CBOE'))}，非台指 VIX）"
    src = vt_meta.get("twii_source") or "—"
    delay = "" if src.startswith("TWSE") else "｜資料可能延遲"
    if both.empty:
        wait = vt_meta.get("wait") or "尚無共同交易日"
        return f"<div class='vt'><div class='bh'><span>⚡ 美國 VIX（CBOE）× 台股背離</span><span class='dtag wait'>{wait}</span></div><div class='m'>{latest_line}｜來源 {html.escape(src)}{delay}</div></div>"
    r = both.iloc[-1]; d = both.index[-1]
    lv = 0 if pd.isna(r.divergence) else int(r.divergence)
    wait = vt_meta.get("wait")
    if wait is None and vix_d is not None and tw_d is not None:
        if vix_d == tw_d == d:
            wait = None
        elif vix_d > d and (tw_d is None or tw_d <= d):
            wait = "等待台股交易日"
        elif tw_d > d and (vix_d is None or vix_d <= d):
            wait = "等待 VIX 交易日"
        elif vix_d != d or tw_d != d:
            wait = "等待共同交易日"
    tag = div_label(lv)
    cls = {0: "none", 1: "div", 2: "strong"}[lv]
    wait_badge = f" <span class='dtag wait'>{html.escape(wait)}</span>" if wait else ""
    sync_note = html.escape(wait) if wait else "兩邊日期已對齊"
    vix_s = f"{r.vix_chg:+.1f}%"
    tw_s = f"{r.twii_chg:+.1f}%"
    # 判定日兩邊收盤（同一天），不要各自抓最新日造成不同步
    vix_px = float(r.vix); tw_px = float(r.twii)
    h60 = both.tail(60)
    w, h = (300, 34) if compact else (1100, 110)
    chart = spark(list(h60.vix_chg), lo_band=-VIX_DIV_TH, w=w, h=h, color="#ff8a80") if len(h60) >= 2 else ""
    foot = f"判定日 {d:%Y-%m-%d}｜{latest_line}｜台股來源 {html.escape(src)}{delay}｜{sync_note}｜門檻：台跌且 VIX 跌≥{VIX_DIV_TH:.0f}%／≥{VIX_STRONG_TH:.0f}%｜不影響美股條件"
    if compact:
        return f"""<div class='vt'><div class='bh'><span>⚡ 美國 VIX（CBOE）× 台股背離</span><span><span class='dtag {cls}'>{tag}</span>{wait_badge}</span></div>
<div class='bv'><div><small>判定日</small><b style='color:#c9d1d9'>{d:%m/%d}</b><small>共同交易日</small></div>
<div><small>美國 VIX 該日</small><b style='color:#ff8a80'>{vix_px:.1f}</b><small>{vix_s}</small></div>
<div><small>台股 該日</small><b style='color:#4fc3f7'>{tw_px:,.0f}</b><small>{tw_s}</small></div></div>
{chart}<div class='m'>{latest_line}｜{html.escape(src)}{delay}</div></div>"""
    return f"""<div class='vt'><div class='bh'><span>⚡ 美國 VIX（CBOE）× 台股背離（參考）</span><span><span class='dtag {cls}'>{tag}</span>{wait_badge}</span></div>
<div class='bv'><div><small>判定日（共同交易日）</small><b>{d:%Y-%m-%d}</b><small>{sync_note}</small></div>
<div><small>美國 VIX 該日收盤</small><b style='color:#ff8a80'>{vix_px:.2f}</b><small>{vix_s}</small></div>
<div><small>台股加權該日收盤</small><b style='color:#4fc3f7'>{tw_px:,.0f}</b><small>{tw_s}</small></div></div>
{chart}<div class='m'>{foot}</div></div>"""

def update_csv(df, path, overwrite_cols=None):
    if os.path.exists(path):
        old = pd.read_csv(path, index_col=0, parse_dates=True)
        # 這些欄位每次重算，避免舊的 pad/0% 髒值經 combine_first 回流
        if overwrite_cols:
            old = old.drop(columns=[c for c in overwrite_cols if c in old.columns], errors="ignore")
        df = df.combine_first(old).sort_index()
    df.index.name = "date"; df.to_csv(path); return df

def spark(vals, lo_band=None, hi_band=None, w=520, h=110, color="#4fc3f7"):
    v = [x for x in vals]
    if len(v) < 2: return "<div class='na'>資料不足</div>"
    ymin, ymax = min(v + [b for b in (lo_band, hi_band) if b is not None]), max(v + [b for b in (lo_band, hi_band) if b is not None])
    ymin -= 2; ymax += 2; rng = ymax - ymin or 1
    X = lambda i: 6 + i * (w - 12) / (len(v) - 1); Y = lambda y: h - 6 - (y - ymin) / rng * (h - 12)
    s = f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none'>"
    if lo_band is not None and hi_band is not None:
        s += f"<rect x='0' y='{Y(hi_band):.1f}' width='{w}' height='{Y(lo_band)-Y(hi_band):.1f}' fill='rgba(255,82,82,.13)'/>"
    elif lo_band is not None:
        s += f"<rect x='0' y='{Y(lo_band):.1f}' width='{w}' height='{h-Y(lo_band):.1f}' fill='rgba(255,82,82,.13)'/>"
        s += f"<line x1='0' x2='{w}' y1='{Y(lo_band):.1f}' y2='{Y(lo_band):.1f}' stroke='#ff5252' stroke-dasharray='4 4'/>"
    pts = " ".join(f"{X(i):.1f},{Y(y):.1f}" for i, y in enumerate(v))
    s += f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2.2'/>"
    s += f"<circle cx='{X(len(v)-1):.1f}' cy='{Y(v[-1]):.1f}' r='4' fill='{color}'/></svg>"
    return s

def render(hist, meta, out, btc=None, vt=None, vt_meta=None):
    r = hist.iloc[-1]; h60 = hist.tail(60)
    c_sb, c_nb = r.spx_breadth < BREADTH_TH, r.ndx_breadth < BREADTH_TH
    c_sd, c_nd = DD_LO <= r.spx_dd <= DD_HI, DD_LO <= r.ndx_dd <= DD_HI
    met = sum([c_sb, c_nb, c_sd, c_nd]); sig = met == 4
    near = (not sig) and r.spx_breadth < 20 and r.ndx_breadth < 20 and r.spx_dd >= 8 and r.ndx_dd >= 8
    light, label = ("green", "抄底訊號成立") if sig else (("yellow", "接近觸發") if near else ("red", "尚未觸發"))
    def gap_b(x): return "✅ 已達成" if x < BREADTH_TH else f"還需下降 {x-BREADTH_TH+0.01:.1f} 個百分點"
    def gap_d(x):
        if x < DD_LO: return f"還需再跌 {DD_LO-x:.1f}%"
        if x > DD_HI: return f"⚠️ 已超過 20%（超出 {x-DD_HI:.1f}%）"
        return "✅ 已在 10–20% 區間"
    def card(title, val, ok, gap, sub, chart):
        return f"""<div class='card {'ok' if ok else ''}'><div class='ct'>{title}<span class='badge'>{'達成' if ok else '未達成'}</span></div>
<div class='big'>{val}</div><div class='gap'>{gap}</div><div class='sub'>{sub}</div><div class='chart'>{chart}</div></div>"""
    now = dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M")
    rows = "".join(f"<tr><td>{d:%m/%d}</td><td>{x.spx_breadth:.1f}%</td><td>{x.ndx_breadth:.1f}%</td><td>{x.spx_dd:.1f}%</td><td>{x.ndx_dd:.1f}%</td><td>{'🟢' if x.signal == 1 else '—'}</td></tr>"
                   for d, x in hist.tail(10).iloc[::-1].iterrows())
    cards = (card("S&amp;P 500 成分股站上 20MA", f"{r.spx_breadth:.1f}%", c_sb, gap_b(r.spx_breadth), f"門檻 &lt; 15%｜{meta['spx_n']}/{meta['spx_total']} 檔有效", spark(list(h60.spx_breadth), BREADTH_TH, color="#4fc3f7")) +
             card("Nasdaq 100 成分股站上 20MA", f"{r.ndx_breadth:.1f}%", c_nb, gap_b(r.ndx_breadth), f"門檻 &lt; 15%｜{meta['ndx_n']}/{meta['ndx_total']} 檔有效", spark(list(h60.ndx_breadth), BREADTH_TH, color="#b388ff")) +
             card("S&amp;P 500 自 52 週高點回撤", f"{r.spx_dd:.1f}%", c_sd, gap_d(r.spx_dd), f"收盤 {meta['spx_close']:,.0f}｜高點 {meta['spx_hi']:,.0f}", spark(list(h60.spx_dd), DD_LO, DD_HI, color="#4fc3f7")) +
             card("Nasdaq 100 自 52 週高點回撤", f"{r.ndx_dd:.1f}%", c_nd, gap_d(r.ndx_dd), f"收盤 {meta['ndx_close']:,.0f}｜高點 {meta['ndx_hi']:,.0f}", spark(list(h60.ndx_dd), DD_LO, DD_HI, color="#b388ff")))
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>抄底訊號 {label}</title><style>
body{{margin:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,"PingFang TC","Heiti TC",sans-serif;padding:28px}}
.wrap{{max-width:1180px;margin:auto}} h1{{font-size:26px;margin:0 0 4px}} .muted{{color:#8b949e;font-size:14px}}
.hero{{display:flex;align-items:center;gap:28px;background:#161b22;border:1px solid #30363d;border-radius:18px;padding:24px;margin:18px 0}}
.lights{{background:#000;border-radius:40px;padding:12px 10px;display:flex;flex-direction:column;gap:10px}}
.l{{width:46px;height:46px;border-radius:50%;opacity:.15}} .l.red{{background:#ff5252}} .l.yellow{{background:#ffd740}} .l.green{{background:#00e676}}
.l.on{{opacity:1;box-shadow:0 0 24px currentColor}} .l.red.on{{color:#ff5252}} .l.yellow.on{{color:#ffd740}} .l.green.on{{color:#00e676}}
.btc .bh,.vt .bh{{display:flex;justify-content:space-between;font-size:18px}} .pos{{font-weight:700}} .up{{color:#69f0ae}} .dn{{color:#ff8a80}} .dtag{{font-size:14px;padding:2px 10px;border-radius:10px;font-weight:700}} .dtag.none{{background:#21262d;color:#8b949e}} .dtag.div{{background:#3d2f1f;color:#ffd740}} .dtag.strong{{background:#3d1f1f;color:#ff8a80}} .dtag.wait{{background:#1f2a3d;color:#4fc3f7}} .dtag.wait{{background:#1f2a3d;color:#4fc3f7}}
.bv{{display:flex;gap:60px;margin:10px 0}} .bv small{{display:block;color:#8b949e;font-size:14px}} .bv b{{font-size:44px;font-variant-numeric:tabular-nums}} .btc svg,.vt svg{{width:100%;height:110px;background:#0d1117;border-radius:8px}} .m{{color:#8b949e;font-size:13px;margin-top:6px}}
.status{{font-size:44px;font-weight:800}} .grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:20px}} .card.ok{{border-color:#00e676}}
.ct{{font-size:16px;color:#c9d1d9;display:flex;justify-content:space-between}} .badge{{font-size:13px;padding:2px 10px;border-radius:10px;background:#3d1f1f;color:#ff8a80}}
.ok .badge{{background:#12321f;color:#69f0ae}} .big{{font-size:64px;font-weight:800;letter-spacing:-1px;margin:6px 0}}
.gap{{font-size:18px;color:#ffd740}} .ok .gap{{color:#69f0ae}} .sub{{color:#8b949e;font-size:14px;margin:4px 0 10px}}
.chart svg{{width:100%;height:110px;background:#0d1117;border-radius:8px}} table{{width:100%;border-collapse:collapse;margin-top:18px;background:#161b22;border-radius:12px}}
td,th{{padding:8px 12px;text-align:right;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums}} td:first-child,th:first-child{{text-align:left}}
</style></head><body><div class="wrap">
<h1>📉 抄底訊號面板</h1><div class="muted">更新時間：{now}（台北時間）｜資料截至美股交易日 {meta['date']:%Y-%m-%d}</div>
<div class="hero"><div class="lights"><div class="l red {'on' if light=='red' else ''}"></div><div class="l yellow {'on' if light=='yellow' else ''}"></div><div class="l green {'on' if light=='green' else ''}"></div></div>
<div><div class="status">{label}</div><div class="muted" style="font-size:17px">四項條件達成 {met}/4　｜　條件：兩大指數成分股站上 20MA 占比皆 &lt; 15%，且兩指數自 52 週高點回撤皆在 10%–20%</div></div></div>
<div class="grid">{cards}</div><div class="card" style="margin-top:18px">{vix_twii_block(vt, False, vt_meta)}</div><div class="card" style="margin-top:18px">{btc_block(btc, False)}</div>
<div class="muted" style="margin-top:10px">小圖為近 {len(h60)} 個交易日走勢，紅色區域為觸發區。</div>
<table><tr><th>日期</th><th>SPX 站上20MA</th><th>NDX 站上20MA</th><th>SPX 回撤</th><th>NDX 回撤</th><th>訊號</th></tr>{rows}</table>
<p class="muted">資料來源：成分股清單 Wikipedia；美股 Yahoo Finance；美國 VIX 用 CBOE 官方收盤（失敗退 Yahoo ^VIX，非台指 VIX）；台股加權用證交所 TWSE 官方收盤（MI_5MINS_HIST），MIS 只作官方未公布當日暫估，失敗才退 Yahoo ^TWII。回撤以 252 交易日收盤最高價計算。歷史寬度以「目前」成分股回推，有存活者偏差。僅供參考，非投資建議。</p>
</div></body></html>"""
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write(page)
    os.replace(tmp, out)
    return sig, met, label

def render_widget(hist, meta, out, btc=None, vt=None, vt_meta=None):
    r = hist.iloc[-1]; h60 = hist.tail(60)
    ok = [r.spx_breadth < BREADTH_TH, r.ndx_breadth < BREADTH_TH, DD_LO <= r.spx_dd <= DD_HI, DD_LO <= r.ndx_dd <= DD_HI]
    met = sum(ok); sig = met == 4
    near = (not sig) and r.spx_breadth < 20 and r.ndx_breadth < 20 and r.spx_dd >= 8 and r.ndx_dd >= 8
    light, label = ("green", "抄底訊號成立") if sig else (("yellow", "接近觸發") if near else ("red", "尚未觸發"))
    def gb(x): return "✓ 達成" if x < BREADTH_TH else f"差 {x-BREADTH_TH:.1f} 點"
    def gd(x): return f"差 {DD_LO-x:.1f}%" if x < DD_LO else ("超過 20%" if x > DD_HI else "✓ 區間內")
    items = [("SPX 站上20MA", r.spx_breadth, ok[0], gb(r.spx_breadth), spark(list(h60.spx_breadth), BREADTH_TH, w=300, h=24, color="#4fc3f7")),
             ("NDX 站上20MA", r.ndx_breadth, ok[1], gb(r.ndx_breadth), spark(list(h60.ndx_breadth), BREADTH_TH, w=300, h=24, color="#b388ff")),
             ("SPX 回撤", r.spx_dd, ok[2], gd(r.spx_dd), spark(list(h60.spx_dd), DD_LO, DD_HI, w=300, h=24, color="#4fc3f7")),
             ("NDX 回撤", r.ndx_dd, ok[3], gd(r.ndx_dd), spark(list(h60.ndx_dd), DD_LO, DD_HI, w=300, h=24, color="#b388ff"))]
    rows = "".join(f"<div class='it {'ok' if o else ''}'><div class='h'><span>{t}</span><b>{v:.1f}%</b></div><div class='g'>{g}</div>{c}</div>" for t, v, o, g, c in items)
    now = dt.datetime.now(TPE).strftime("%m/%d %H:%M")
    col = {"red": "#ff5252", "yellow": "#ffd740", "green": "#00e676"}[light]
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;overflow:hidden;background:transparent}}
body{{font-family:-apple-system,"PingFang TC",sans-serif;color:#e6edf3;-webkit-user-select:none;cursor:default}}
.w{{box-sizing:border-box;height:100%;background:rgba(13,17,23,.88);border:1px solid #30363d;border-radius:18px;padding:10px 14px}}
.top{{display:flex;align-items:center;gap:12px;margin-bottom:4px}}
.dot{{width:30px;height:30px;border-radius:50%;background:{col};box-shadow:0 0 16px {col}}}
.st{{font-size:22px;font-weight:800}} .m{{font-size:11px;color:#8b949e}}
.it{{padding:3px 0;border-top:1px solid #21262d}} .h{{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;color:#c9d1d9}}
.h b{{font-size:21px;color:#fff;font-variant-numeric:tabular-nums}} .g{{font-size:12px;color:#ffd740;margin:-2px 0 3px}} .ok .g{{color:#69f0ae}} .ok .h b{{color:#69f0ae}}
svg{{width:100%;height:24px;display:block}}
.btc,.vt{{border-top:1px solid #30363d;padding-top:5px;margin-top:2px}} .bh{{display:flex;justify-content:space-between;font-size:13px;color:#c9d1d9}}
.pos{{font-weight:700}} .up{{color:#69f0ae}} .dn{{color:#ff8a80}} .bv{{display:flex;justify-content:space-between;margin:2px 0}}
.bv small{{display:block;font-size:10px;color:#8b949e}} .bv b{{font-size:15px;font-variant-numeric:tabular-nums}} .btc svg,.vt svg{{height:34px}}
.dtag{{font-size:11px;padding:1px 7px;border-radius:8px;font-weight:700}} .dtag.none{{background:#21262d;color:#8b949e}} .dtag.div{{background:#3d2f1f;color:#ffd740}} .dtag.strong{{background:#3d1f1f;color:#ff8a80}}
</style></head><body><div class="w"><div class="top"><div class="dot"></div><div><div class="st">{label}</div>
<div class="m">條件 {met}/4｜資料 {meta['date']:%m/%d}｜更新 {now} 台北</div></div></div>{rows}{vix_twii_block(vt, True, vt_meta)}{btc_block(btc, True)}</div></body></html>"""
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write(page)
    os.replace(tmp, out)

def rebuild_outputs(data_dir, out_html, hist, meta, vt_meta):
    us = hist.dropna(subset=US_COLS)
    if us.empty:
        raise RuntimeError("history.csv 尚無美股寬度資料，請先跑一次 --mode daily")
    # meta.date 對齊最後一筆有效美股列
    meta = dict(meta)
    meta["date"] = us.index[-1]
    btc = hist.dropna(subset=BTC_COLS) if set(BTC_COLS) <= set(hist.columns) else None
    vt_full = hist.reindex(columns=VT_COLS) if set(VT_COLS) <= set(hist.columns) else None
    vt_meta = refresh_vt_meta(vt_full, vt_meta)
    sig, met, label = render(us, meta, out_html, btc, vt_full, vt_meta)
    render_widget(us, meta, os.path.join(data_dir, "widget.html"), btc, vt_full, vt_meta)
    try:
        from mobile_page import render_mobile
        render_mobile(us, meta, os.path.join(data_dir, "mobile.html"), btc, vt_full, vt_meta)
    except Exception as e:
        print(f"⚠️ mobile.html 產生失敗：{e}", file=sys.stderr)
    return us, btc, vt_full, vt_meta, sig, met, label


def run_daily(out_html, data_dir):
    df, meta = compute()
    try:
        df = df.join(fetch_vrp(), how="outer")
    except Exception as e:
        print(f"⚠️ Vaulted Realized Price 取得失敗：{e}", file=sys.stderr)
    vt_meta = {}
    try:
        vt_df, vt_meta = fetch_vix_twii()
        df = df.join(vt_df, how="outer")
    except Exception as e:
        print(f"⚠️ VIX/台股 取得失敗：{e}", file=sys.stderr)
    hist = update_csv(df, os.path.join(data_dir, "history.csv"), overwrite_cols=VT_COLS + BTC_COLS)
    save_meta(os.path.join(data_dir, "meta.json"), meta)
    us, btc, vt_full, vt_meta, sig, met, label = rebuild_outputs(data_dir, out_html, hist, meta, vt_meta)
    r = us.iloc[-1]
    if btc is not None and not btc.empty:
        b = btc.iloc[-1]
        print(f"BTC {btc.index[-1]:%Y-%m-%d} 價格 {b.btc_price:,.0f} Vaulted Realized {b.btc_vrp:,.0f} Vaulted MVRV {b.btc_vmvrv:.3f}")
    vt = vt_full.dropna(subset=["vix_chg", "twii_chg"]) if vt_full is not None else None
    if vt is not None and not vt.empty:
        v = vt.iloc[-1]
        w = vt_meta.get("wait") or "已對齊"
        print(f"背離判定日 {vt.index[-1]:%Y-%m-%d} 台股 {v.twii_chg:+.2f}% VIX {v.vix_chg:+.2f}% → {div_label(0 if pd.isna(v.divergence) else int(v.divergence))}｜{w}｜台股來源 {vt_meta.get('twii_source','?')}｜最新 台 {vt_meta.get('twii_latest')} / VIX {vt_meta.get('vix_latest')}")
    print(f"[{dt.datetime.now(TPE):%Y-%m-%d %H:%M} 台北] daily｜{meta['date']:%Y-%m-%d} SPX寬度 {r.spx_breadth:.1f}% NDX寬度 {r.ndx_breadth:.1f}% "
          f"SPX回撤 {r.spx_dd:.1f}% NDX回撤 {r.ndx_dd:.1f}% 條件 {met}/4 → {label}；缺資料 {meta.get('missing', '?')} 檔")


def intraday_view(old, twii_live):
    """以 history 已定案資料為底，只在記憶體中加上 history 沒有的台股暫估日。"""
    prev_tw = old["twii"].dropna().astype(float) if "twii" in old.columns else pd.Series(dtype=float)
    new = twii_live[twii_live.index > (prev_tw.index.max() if len(prev_tw) else pd.Timestamp.min)]
    tw = pd.concat([prev_tw, new]).sort_index()
    vix = old["vix"].dropna().astype(float) if "vix" in old.columns else pd.Series(dtype=float)
    vt = build_vt(vix, tw)
    view = old.copy().reindex(old.index.union(vt.index))
    for c in VT_COLS:
        view[c] = vt[c]
    return view


def run_intraday(out_html, data_dir, force=False):
    now = dt.datetime.now(TPE)
    if not force and not in_tw_session(now):
        print(f"[{now:%Y-%m-%d %H:%M} 台北] intraday｜非台股交易時段（平日 {TW_SESSION_START[0]:02d}:{TW_SESSION_START[1]:02d}–{TW_SESSION_END[0]:02d}:{TW_SESSION_END[1]:02d}），略過")
        return
    hist_path = os.path.join(data_dir, "history.csv")
    meta_path = os.path.join(data_dir, "meta.json")
    if not os.path.exists(hist_path) or not os.path.exists(meta_path):
        raise RuntimeError("缺少 history.csv 或 meta.json，請先跑一次 --mode daily")
    meta = load_meta(meta_path)
    # 輕量：MIS 即時台股 + Yahoo VIX；不重抓美股寬度／BTC
    twii, tw_src = fetch_twii_close(prefer_mis_only=True)
    old = pd.read_csv(hist_path, index_col=0, parse_dates=True)
    # 盤中結果只用於畫面，不寫回 history（避免暫估值污染已定案收盤）
    hist = intraday_view(old, twii)
    vt_meta = {"twii_source": tw_src + "（盤中，不寫入歷史）", "vix_source": "history（CBOE 已收盤）", "mode": "intraday"}
    us, btc, vt_full, vt_meta, sig, met, label = rebuild_outputs(data_dir, out_html, hist, meta, vt_meta)
    vt = vt_full.dropna(subset=["vix_chg", "twii_chg"]) if vt_full is not None else None
    live = twii.dropna()
    live_s = f"{live.index[-1]:%Y-%m-%d} {float(live.iloc[-1]):,.2f}" if len(live) else "—"
    if vt is not None and not vt.empty:
        v = vt.iloc[-1]
        w = vt_meta.get("wait") or "已對齊"
        print(f"背離判定日 {vt.index[-1]:%Y-%m-%d} 台股 {v.twii_chg:+.2f}% VIX {v.vix_chg:+.2f}% → {div_label(0 if pd.isna(v.divergence) else int(v.divergence))}｜{w}｜來源 {tw_src}")
    print(f"[{now:%Y-%m-%d %H:%M} 台北] intraday｜台股最新 {live_s}｜美股條件沿用 {meta['date']:%Y-%m-%d} {met}/4 → {label}")


def run_rebuild(out_html, data_dir):
    """只依既有 history/meta 重繪面板（不抓新行情）。"""
    hist_path = os.path.join(data_dir, "history.csv")
    meta_path = os.path.join(data_dir, "meta.json")
    if not os.path.exists(hist_path) or not os.path.exists(meta_path):
        raise RuntimeError("缺少 history.csv 或 meta.json，請先跑一次 --mode daily")
    meta = load_meta(meta_path)
    hist = pd.read_csv(hist_path, index_col=0, parse_dates=True)
    vt_full = hist.reindex(columns=VT_COLS) if set(VT_COLS) <= set(hist.columns) else None
    vt_meta = refresh_vt_meta(vt_full, {"mode": "rebuild"})
    us, btc, vt_full, vt_meta, sig, met, label = rebuild_outputs(data_dir, out_html, hist, meta, vt_meta)
    print(f"[{dt.datetime.now(TPE):%Y-%m-%d %H:%M} 台北] rebuild｜資料 {meta['date']:%Y-%m-%d} 條件 {met}/4 → {label}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/抄底訊號.html"))
    ap.add_argument("--data-dir", default=os.path.expanduser("~/DipSignal"))
    ap.add_argument("--mode", choices=("daily", "intraday", "rebuild"), default="daily")
    ap.add_argument("--force", action="store_true", help="intraday 時略過交易時段檢查（測試用）")
    a = ap.parse_args()
    os.makedirs(a.data_dir, exist_ok=True)
    if a.mode == "intraday":
        run_intraday(a.out, a.data_dir, force=a.force)
    elif a.mode == "rebuild":
        run_rebuild(a.out, a.data_dir)
    else:
        run_daily(a.out, a.data_dir)

if __name__ == "__main__":
    main()
