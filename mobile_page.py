# -*- coding: utf-8 -*-
"""手機優先單頁：美股 4 條件 + BTC Vaulted + VIX×台股背離，含「更新」按鈕。"""
from __future__ import annotations
import datetime as dt
import html
import os
from zoneinfo import ZoneInfo

import pandas as pd

from dip_signal import (
    BREADTH_TH, DD_HI, DD_LO, TPE, VIX_DIV_TH, VIX_STRONG_TH,
    btc_block, spark, vix_twii_block,
)

def render_mobile(hist, meta, out, btc=None, vt=None, vt_meta=None):
    r = hist.iloc[-1]
    h60 = hist.tail(60)
    c_sb = r.spx_breadth < BREADTH_TH
    c_nb = r.ndx_breadth < BREADTH_TH
    c_sd = DD_LO <= r.spx_dd <= DD_HI
    c_nd = DD_LO <= r.ndx_dd <= DD_HI
    oks = [c_sb, c_nb, c_sd, c_nd]
    met = sum(oks)
    sig = met == 4
    near = (not sig) and r.spx_breadth < 20 and r.ndx_breadth < 20 and r.spx_dd >= 8 and r.ndx_dd >= 8
    light, label = (
        ("green", "抄底訊號成立") if sig else
        (("yellow", "接近觸發") if near else ("red", "尚未觸發"))
    )
    col = {"red": "#ff5252", "yellow": "#ffd740", "green": "#00e676"}[light]

    def gap_b(x):
        return "✅ 已達成" if x < BREADTH_TH else f"還需下降 {x - BREADTH_TH:.1f} 點"

    def gap_d(x):
        if x < DD_LO:
            return f"還需再跌 {DD_LO - x:.1f}%"
        if x > DD_HI:
            return f"⚠️ 已超過 20%"
        return "✅ 已在 10–20%"

    cards = [
        ("SPX 站上 20MA", f"{r.spx_breadth:.1f}%", c_sb, gap_b(r.spx_breadth),
         spark(list(h60.spx_breadth), BREADTH_TH, w=340, h=56, color="#4fc3f7")),
        ("NDX 站上 20MA", f"{r.ndx_breadth:.1f}%", c_nb, gap_b(r.ndx_breadth),
         spark(list(h60.ndx_breadth), BREADTH_TH, w=340, h=56, color="#b388ff")),
        ("SPX 回撤", f"{r.spx_dd:.1f}%", c_sd, gap_d(r.spx_dd),
         spark(list(h60.spx_dd), DD_LO, DD_HI, w=340, h=56, color="#4fc3f7")),
        ("NDX 回撤", f"{r.ndx_dd:.1f}%", c_nd, gap_d(r.ndx_dd),
         spark(list(h60.ndx_dd), DD_LO, DD_HI, w=340, h=56, color="#b388ff")),
    ]
    card_html = "".join(
        f"""<section class="card {'ok' if ok else ''}">
<div class="row"><span class="t">{html.escape(title)}</span>
<span class="badge">{'達成' if ok else '未達'}</span></div>
<div class="big">{val}</div>
<div class="gap">{gap}</div>
<div class="chart">{chart}</div>
</section>"""
        for title, val, ok, gap, chart in cards
    )

    now = dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M")
    data_day = meta["date"].strftime("%Y-%m-%d") if hasattr(meta["date"], "strftime") else str(meta["date"])[:10]
    vt_html = vix_twii_block(vt, False, vt_meta)
    btc_html = btc_block(btc, False)

    page = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0d1117">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<title>抄底訊號 · {label}</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: #0d1117; color: #e6edf3;
  font-family: -apple-system, "PingFang TC", "Heiti TC", sans-serif;
  padding: 16px 14px 120px;
  -webkit-text-size-adjust: 100%;
}}
.wrap {{ max-width: 480px; margin: 0 auto; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
.muted {{ color: #8b949e; font-size: 13px; line-height: 1.4; }}
.hero {{
  display: flex; gap: 14px; align-items: center;
  background: #161b22; border: 1px solid #30363d; border-radius: 16px;
  padding: 16px; margin: 14px 0;
}}
.dot {{
  width: 42px; height: 42px; border-radius: 50%; flex-shrink: 0;
  background: {col}; box-shadow: 0 0 22px {col};
}}
.status {{ font-size: 26px; font-weight: 800; line-height: 1.2; }}
.card {{
  background: #161b22; border: 1px solid #30363d; border-radius: 14px;
  padding: 14px; margin: 10px 0;
}}
.card.ok {{ border-color: #00e676; }}
.row {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
.t {{ font-size: 15px; color: #c9d1d9; }}
.badge {{
  font-size: 12px; padding: 2px 8px; border-radius: 999px;
  background: #3d1f1f; color: #ff8a80; white-space: nowrap;
}}
.ok .badge {{ background: #12321f; color: #69f0ae; }}
.big {{
  font-size: 40px; font-weight: 800; letter-spacing: -1px;
  font-variant-numeric: tabular-nums; margin: 4px 0;
}}
.gap {{ font-size: 15px; color: #ffd740; }}
.ok .gap {{ color: #69f0ae; }}
.chart svg {{ width: 100%; height: 56px; display: block; background: #0d1117; border-radius: 8px; }}
.btc .bv, .vt .bv {{ display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin: 8px 0; }}
.btc .bv small, .vt .bv small {{ display: block; color: #8b949e; font-size: 12px; }}
.btc .bv b, .vt .bv b {{ font-size: 22px; font-variant-numeric: tabular-nums; }}
.btc svg, .vt svg {{ width: 100%; height: 72px; background: #0d1117; border-radius: 8px; }}
.bh {{ display: flex; justify-content: space-between; align-items: center; font-size: 16px; gap: 8px; }}
.pos {{ font-weight: 700; }} .up {{ color: #69f0ae; }} .dn {{ color: #ff8a80; }}
.dtag {{ font-size: 13px; padding: 2px 8px; border-radius: 8px; font-weight: 700; }}
.dtag.none {{ background: #21262d; color: #8b949e; }}
.dtag.div {{ background: #3d2f1f; color: #ffd740; }}
.dtag.strong {{ background: #3d1f1f; color: #ff8a80; }}
.dtag.wait {{ background: #1f2a3d; color: #4fc3f7; }}
.m {{ color: #8b949e; font-size: 12px; margin-top: 6px; line-height: 1.35; }}
.bar {{
  position: fixed; left: 0; right: 0; bottom: 0;
  background: rgba(13,17,23,.94); border-top: 1px solid #30363d;
  padding: 12px 14px calc(12px + env(safe-area-inset-bottom));
  backdrop-filter: blur(10px);
}}
.bar-inner {{ max-width: 480px; margin: 0 auto; display: flex; gap: 8px; flex-wrap: wrap; }}
button {{
  flex: 1; border: 0; border-radius: 12px; padding: 14px 12px;
  font-size: 16px; font-weight: 700; cursor: pointer;
  min-width: 40%;
}}
#btnFull {{ background: #238636; color: #fff; }}
#btnIntra {{ background: #21262d; color: #e6edf3; border: 1px solid #30363d; }}
#btnInstall {{ background: #1f6feb; color: #fff; display: none; }}
#btnInstall.show {{ display: block; }}
#installTip {{ display: none; width: 100%; margin: 0; font-size: 12px; color: #8b949e; text-align: center; line-height: 1.4; }}
#installTip.show {{ display: block; }}
button:disabled {{ opacity: .55; cursor: wait; }}
#toast {{
  display: none; margin-top: 8px; font-size: 13px; color: #8b949e; text-align: center;
}}
#toast.show {{ display: block; }}
#toast.err {{ color: #ff8a80; }}
#toast.ok {{ color: #69f0ae; }}
.hint {{ margin: 8px 0 0; font-size: 12px; color: #8b949e; }}
code {{ background: #21262d; padding: 1px 6px; border-radius: 6px; font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📉 抄底訊號</h1>
  <div class="muted">更新時間：{now}（台北）｜美股資料日 {data_day}</div>
  <div class="hero">
    <div class="dot" aria-hidden="true"></div>
    <div>
      <div class="status">{html.escape(label)}</div>
      <div class="muted">美股條件 {met}/4　｜　寬度皆 &lt; 15% 且回撤皆在 10–20%</div>
    </div>
  </div>
  {card_html}
  <section class="card">{vt_html}</section>
  <section class="card">{btc_html}</section>
  <p class="muted">按下下方「更新」才會重抓數據。完整更新含美股寬度，可能要 1–3 分鐘；盤中快更只刷新台股與 VIX。僅供參考，非投資建議。</p>
  <p class="hint">手機請連與 Mac 同一 Wi‑Fi，開啟此頁服務位址（見底部提示）。外網需另開 tunnel。</p>
</div>
<div class="bar">
  <div class="bar-inner">
    <button type="button" id="btnIntra">盤中快更</button>
    <button type="button" id="btnFull">完整更新</button>
    <button type="button" id="btnInstall">加到主畫面</button>
    <p id="installTip"></p>
  </div>
  <div id="toast"></div>
</div>
<script>
(function () {{
  const toast = document.getElementById('toast');
  const btnFull = document.getElementById('btnFull');
  const btnIntra = document.getElementById('btnIntra');
  function setBusy(on, msg) {{
    btnFull.disabled = on; btnIntra.disabled = on;
    toast.className = 'show';
    toast.textContent = msg || (on ? '更新中…' : '');
  }}
  function showErr(m) {{ toast.className = 'show err'; toast.textContent = m; }}
  function showOk(m) {{ toast.className = 'show ok'; toast.textContent = m; }}
  async function poll(jobId) {{
    for (let i = 0; i < 180; i++) {{
      await new Promise(r => setTimeout(r, 2000));
      const r = await fetch('/api/job?id=' + encodeURIComponent(jobId), {{ cache: 'no-store' }});
      if (!r.ok) throw new Error('查詢狀態失敗 HTTP ' + r.status);
      const j = await r.json();
      if (j.status === 'running') {{
        setBusy(true, '更新中… ' + (j.elapsed_sec || 0) + 's');
        continue;
      }}
      if (j.status === 'ok') {{
        showOk('完成，重新載入…');
        setTimeout(() => location.reload(), 600);
        return;
      }}
      throw new Error(j.error || '更新失敗');
    }}
    throw new Error('逾時，請稍後再試或看 Mac 上的 run.log');
  }}
  async function start(mode) {{
    setBusy(true, mode === 'intraday' ? '盤中快更啟動…' : '完整更新啟動（可能需數分鐘）…');
    try {{
      const r = await fetch('/api/update?mode=' + encodeURIComponent(mode), {{ method: 'POST' }});
      const j = await r.json().catch(() => ({{}}));
      if (r.status === 409) {{
        showErr(j.error || '已有更新在跑');
        if (j.job_id) await poll(j.job_id);
        return;
      }}
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      await poll(j.job_id);
    }} catch (e) {{
      const msg = String(e && e.message || e);
      if (location.protocol === 'file:') {{
        showErr('這是本機檔案，無法按更新。請用手機開 http://<Mac區網IP>:8765/');
      }} else if (/Failed to fetch|NetworkError|Load failed/i.test(msg)) {{
        showErr('連不上更新服務。請確認 Mac 上 dip_server 有開，且手機同一 Wi‑Fi。');
      }} else {{
        showErr(msg);
      }}
      btnFull.disabled = false; btnIntra.disabled = false;
    }}
  }}
  btnFull.addEventListener('click', () => start('daily'));
  btnIntra.addEventListener('click', () => start('intraday'));
  const btnInstall = document.getElementById('btnInstall');
  const installTip = document.getElementById('installTip');
  if (installTip) installTip.textContent = 'Chrome 右上 ⋮ →「安裝應用程式」或「新增至主畫面」。Tunnel 網址變更後需重新新增。';
  let deferredPrompt = null;
  const _bip = 'before' + 'install' + 'prompt';
  window.addEventListener(_bip, (e) => {{
    e.preventDefault();
    deferredPrompt = e;
    if (btnInstall) btnInstall.classList.add('show');
    if (installTip) installTip.classList.remove('show');
  }});
  window.addEventListener('appinstalled', () => {{
    deferredPrompt = null;
    if (btnInstall) btnInstall.classList.remove('show');
    if (installTip) installTip.classList.remove('show');
  }});
  if (btnInstall) btnInstall.addEventListener('click', async () => {{
    if (!deferredPrompt) {{
      if (installTip) installTip.classList.add('show');
      return;
    }}
    deferredPrompt.prompt();
    try {{ await deferredPrompt.userChoice; }} catch (_) {{}}
    deferredPrompt = null;
    btnInstall.classList.remove('show');
  }});
  setTimeout(() => {{
    const standalone = window.matchMedia('(display-mode: standalone)').matches;
    if (!deferredPrompt && !standalone) {{
      if (installTip) installTip.classList.add('show');
      if (btnInstall) btnInstall.classList.add('show');
    }}
  }}, 800);
  // 啟動時若伺服器有寫入目前位址，顯示在 toast 旁
  fetch('/api/info', {{ cache: 'no-store' }}).then(r => r.json()).then(info => {{
    if (info && info.lan_urls && info.lan_urls.length) {{
      const el = document.querySelector('.hint');
      if (el) el.innerHTML = '手機請開：<code>' + info.lan_urls[0] + '</code>（同一 Wi‑Fi）';
    }}
  }}).catch(() => {{}});
}})();
</script>
</body>
</html>"""
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, out)
