import datetime as dt, os, sys
from zoneinfo import ZoneInfo
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dip_signal as d

T = pd.Timestamp

def test_official_close_beats_stale_mis():
    off = pd.Series({T("2026-11-05"): 41574.72, T("2026-11-06"): 39701.64})
    s, prov = d.merge_twii_official_mis(off, (T("2026-11-06"), 39690.74, {"t": "13:19:50"}))
    assert s[T("2026-11-06")] == 39701.64 and prov is None

def test_stale_mis_older_day_ignored():
    off = pd.Series({T("2026-11-06"): 39701.64})
    s, prov = d.merge_twii_official_mis(off, (T("2026-11-05"), 1.0, {}))
    assert s[T("2026-11-06")] == 39701.64 and T("2026-11-05") not in s.index and prov is None

def test_mis_only_fills_new_day_as_provisional():
    off = pd.Series({T("2026-11-06"): 39701.64})
    s, prov = d.merge_twii_official_mis(off, (T("2026-11-09"), 40000.0, {}))
    assert prov == T("2026-11-09") and s[T("2026-11-09")] == 40000.0

def test_cboe_parse_and_known_values():
    txt = "DATE,OPEN,HIGH,LOW,CLOSE\n10/29/2026,17.15,17.36,16.21,17.20\n10/30/2026,18.43,23.93,18.03,19.42\n11/02/2026,19.05,21.77,18.03,21.15\n11/05/2026,17.14,28.36,16.70,28.18\n11/06/2026,27.68,27.76,23.47,25.64\n"
    s = d.parse_cboe_vix_csv(txt)
    assert s[T("2026-11-02")] == 21.15 and s[T("2026-10-30")] == 19.42
    assert round((s.pct_change() * 100)[T("2026-11-06")], 2) == -9.01

def test_unclosed_us_session_excluded():
    s = pd.Series({T("2026-11-05"): 28.18, T("2026-11-06"): 25.0})
    now = dt.datetime(2026, 11, 6, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert list(d.only_completed_vix(s, now).index) == [T("2026-11-05")]
    now2 = dt.datetime(2026, 11, 6, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    assert len(d.only_completed_vix(s, now2)) == 2

def test_own_market_pct_then_shared_dates():
    vix = pd.Series({T("2026-10-30"): 19.42, T("2026-11-02"): 21.15})  # 無 10/31
    tw = pd.Series({T("2026-10-30"): 45211.83, T("2026-10-31"): 45000.0, T("2026-11-02"): 44920.14})
    df = d.build_vt(vix, tw)
    assert round(df.loc[T("2026-11-02"), "vix_chg"], 2) == 8.91  # 對 10/30，非 0
    assert pd.isna(df.loc[T("2026-10-31"), "divergence"])

def test_strong_divergence_1106():
    vix = pd.Series({T("2026-11-05"): 28.18, T("2026-11-06"): 25.64})
    tw = pd.Series({T("2026-11-05"): 41574.72, T("2026-11-06"): 39701.64})
    r = d.build_vt(vix, tw).loc[T("2026-11-06")]
    assert r.twii_chg == -4.51 and r.vix_chg == -9.01 and r.divergence == 2

def test_intraday_view_does_not_override_history():
    old = pd.DataFrame({"vix": [28.18, 25.64], "twii": [41574.72, 39701.64]}, index=[T("2026-11-05"), T("2026-11-06")])
    live = pd.Series({T("2026-11-06"): 1.0, T("2026-11-09"): 40000.0})
    v = d.intraday_view(old, live)
    assert v.loc[T("2026-11-06"), "twii"] == 39701.64 and v.loc[T("2026-11-09"), "twii"] == 40000.0
    assert pd.isna(v.loc[T("2026-11-09"), "divergence"])
