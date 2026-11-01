"""把 mobile.html 轉成 GitHub Pages 用的 site/（更新鈕改開 GitHub Actions）。"""
import json, os, shutil, sys
repo = os.environ.get("GITHUB_REPOSITORY", "luke120513-sys/dip-signal")
wf = os.environ.get("WORKFLOW_FILE", "update.yml")
url = f"https://github.com/{repo}/actions/workflows/{wf}"
site = "site"
shutil.rmtree(site, ignore_errors=True); os.makedirs(site)
shutil.copytree("static", os.path.join(site, "static"))
h = open("mobile.html", encoding="utf-8").read()
h = h.replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"').replace('href="/static/', 'href="static/')
inject = f"""
<script>
(function(){{
  var URL={json.dumps(url)};
  var HINT='到 GitHub 按 Run workflow，約 1–2 分鐘後重新整理';
  ['btnFull','btnIntra'].forEach(function(id){{
    var b=document.getElementById(id); if(!b) return;
    var n=b.cloneNode(true); n.disabled=false;
    if(id==='btnIntra'){{ n.style.display='none'; }} else {{ n.textContent='更新'; }}
    b.parentNode.replaceChild(n,b);
    n.addEventListener('click',function(){{
      window.open(URL,'_blank','noopener');
      var t=document.getElementById('toast');
      if(t){{ t.className='show ok'; t.textContent=HINT; }} else alert(HINT);
    }});
  }});
  var tip=document.getElementById('installTip');
  if(tip) tip.textContent='Chrome 右上 ⋮ →「安裝應用程式」或「新增至主畫面」。';
}})();
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
