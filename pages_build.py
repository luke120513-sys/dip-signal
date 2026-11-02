"""把 mobile.html 轉成 GitHub Pages 用的 site/（更新鈕經 Cloudflare Worker 觸發 GitHub Actions）。"""
import json, os, shutil, sys
repo = os.environ.get("GITHUB_REPOSITORY", "luke120513-sys/dip-signal")
wf = os.environ.get("WORKFLOW_FILE", "update.yml")
url = "https://dip-update.luke120513.workers.dev"
site = "site"
shutil.rmtree(site, ignore_errors=True); os.makedirs(site)
shutil.copytree("static", os.path.join(site, "static"))
h = open("mobile.html", encoding="utf-8").read()
h = h.replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"').replace('href="/static/', 'href="static/')
inject = """
<script>
(function(){
  var W='https://dip-update.luke120513.workers.dev';
  function msg(t,ok){var e=document.getElementById('toast');if(e){e.className='show '+(ok?'ok':'err');e.textContent=t;}}
  var MAP={busy:'已有更新在進行中，請稍候',too_soon:'剛更新過，請 3 分鐘後再試'};
  ['btnFull','btnIntra'].forEach(function(id){
    var b=document.getElementById(id); if(!b) return;
    var n=b.cloneNode(true); n.disabled=false;
    if(id==='btnIntra'){ n.style.display='none'; } else { n.textContent='更新'; }
    b.parentNode.replaceChild(n,b);
    n.addEventListener('click',function(){
      n.disabled=true; n.textContent='更新中…'; msg('已送出更新，約 1–2 分鐘…',true);
      var t0=Date.now()-30000, start=Date.now();
      function fail(t){n.disabled=false;n.textContent='更新';msg(t,false);}
      function done(){msg('計算完成，等待網頁發佈…',true);setTimeout(function(){location.replace(location.pathname+'?t='+Date.now());},60000);}
      function poll(){
        if(Date.now()-start>900000) return fail('等候逾時，請稍後重新整理');
        fetch(W+'/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
          var r=d.run;
          if(r&&Date.parse(r.created_at)>=t0&&r.status==='completed'){
            if(r.conclusion==='success') return done();
            return fail('更新失敗（'+r.conclusion+'）');
          }
          if(r&&Date.parse(r.created_at)>=t0) msg('更新中…（'+(r.status==='queued'?'排隊中':'計算中')+'）',true);
          setTimeout(poll,5000);
        }).catch(function(){setTimeout(poll,5000);});
      }
      fetch(W+'/trigger',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
        if(d.ok) return setTimeout(poll,5000);
        if(d.error==='busy'&&d.run){t0=Date.parse(d.run.created_at);msg(MAP.busy,true);return setTimeout(poll,5000);}
        fail(MAP[d.error]||('更新失敗：'+d.error));
      }).catch(function(){fail('無法連線到更新服務');});
    });
  });
  var tip=document.getElementById('installTip');
  if(tip) tip.textContent='Chrome 右上 ⋮ →「安裝應用程式」或「新增至主畫面」。';
})();
</script>
"""
h = h.replace("</body>", inject + "</body>") if "</body>" in h else h + inject
open(os.path.join(site, "index.html"), "w", encoding="utf-8").write(h)
m = json.load(open("manifest.webmanifest", encoding="utf-8"))
m["start_url"] = "./"; m["scope"] = "./"
for i in m["icons"]: i["src"] = i["src"].lstrip("/")
json.dump(m, open(os.path.join(site, "manifest.webmanifest"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(os.path.join(site, ".nojekyll"), "w").close()
print("site built ->", url)
