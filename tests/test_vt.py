import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dip_signal as d
T = pd.Timestamp

MONTH = """交易日期\t時間(時/分/秒/毫秒)\t臺指選擇權波動率指數\t收盤前1分鐘平均指數
--------\t-------------------\t--------------------\t-------------------
20261105\t13450000\t\t\t39.92\t\t39.93
20261106\t13450000\t\t\t46.52\t\t46.62
"""
TW = pd.Series({T("2026-11-05"): 41574.72, T("2026-11-06"): 39701.64})
VX = pd.Series({T("2026-11-05"): 39.92, T("2026-11-06"): 46.52})

def test_parse_taifex_official_close_uses_1345_value():
    s = d.parse_taifex_vix_month(MONTH)
    assert s[T("2026-11-06")] == 46.52 and s[T("2026-11-05")] == 39.92

def test_official_1106_previous_close_denominator():
    r = d.build_vt(VX, TW).loc[T("2026-11-06")]
    assert r.vix_chg == 16.53 and r.twii_chg == -4.51 and r.divergence == 0

def test_legacy_us_vix_never_mixed():
    h = pd.DataFrame({"vix": [28.18], "vix_chg": [69.66], "divergence": [0.0], "twii": [41574.72]}, index=[T("2026-11-05")])
    m = d.migrate_hist(h)
    assert "vix" not in m.columns and m.loc[T("2026-11-05"), "us_vix"] == 28.18
    assert d.vt_from_hist(m) is None  # 沒有台指 VIX 欄就不畫，不拿美國 VIX 頂替

def test_shared_dates_only():
    vx = pd.Series({T("2026-11-05"): 39.92, T("2026-11-07"): 40.0})
    df = d.build_vt(vx, TW)
    assert pd.isna(df.loc[T("2026-11-06"), "divergence"])

def L(date, t, last, ref=None):
    return {"date": T(date), "time": t, "last": last, "ref": ref}

def test_live_aligned_uses_previous_official_close():
    r = d.live_divergence(TW, VX, L("2026-11-09", "100000", 38000.0), L("2026-11-09", "100015", 41.0, 46.52))
    assert r["status"] == "盤中暫估" and r["tvix_chg"] == round((41 / 46.52 - 1) * 100, 2)
    assert r["twii_chg"] == round((38000 / 39701.64 - 1) * 100, 2) and r["divergence"] == 2

def test_live_time_skew_suppressed():
    r = d.live_divergence(TW, VX, L("2026-11-09", "100000", 38000.0), L("2026-11-09", "101500", 41.0, 46.52))
    assert "twii_chg" not in r and "不判定" in r["status"]

def test_stale_mis_same_as_official_day_suppressed():
    r = d.live_divergence(TW, VX, L("2026-11-06", "131950", 39690.74), L("2026-11-06", "134500", 46.52, 39.92))
    assert "twii_chg" not in r and "官方" in r["status"]

def test_date_mismatch_or_missing_suppressed():
    assert "twii_chg" not in d.live_divergence(TW, VX, L("2026-11-09", "100000", 1.0), L("2026-11-06", "134500", 46.52, 39.92))
    assert "twii_chg" not in d.live_divergence(TW, VX, None, L("2026-11-09", "100000", 41.0, 46.52))

def test_ref_mismatch_suppressed():
    r = d.live_divergence(TW, VX, L("2026-11-09", "100000", 38000.0), L("2026-11-09", "100000", 41.0, 40.0))
    assert "twii_chg" not in r

def test_after_close_skew_allowed():
    r = d.live_divergence(TW, VX, L("2026-11-09", "133000", 38000.0), L("2026-11-09", "134500", 41.0, 46.52))
    assert r["status"].startswith("收盤後暫估")

def test_official_tw_close_beats_mis():
    s, prov = d.merge_twii_official_mis(TW, (T("2026-11-06"), 39690.74, {}))
    assert s[T("2026-11-06")] == 39701.64 and prov is None
