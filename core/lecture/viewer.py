"""Self-contained synchronized lecture viewer (one HTML file).

Slides + transcript are embedded; the video is referenced by relative path.
Behaviour: playing the video highlights the current transcript line and
slide; clicking a line or slide seeks the video; the box filters the
transcript and slide text. Data comes straight from the store.
"""
import base64
import html
import json

from .repo import LectureRepository
from .storage import StorageService


def export_html(repo: LectureRepository, storage: StorageService, lecture_id: int, out_path: str,
                video_href: str) -> str:
    d = repo.viewer_data(lecture_id)
    for s in d["slides"]:
        s["image"] = "data:image/png;base64," + base64.b64encode(storage.read_bytes(s.pop("image_key"))).decode()
    payload = json.dumps(d).replace("</", "<\\/")
    page = _TEMPLATE.replace("__TITLE__", html.escape(d["lecture"]["title"] or "Lecture")) \
                    .replace("__VIDEO__", html.escape(video_href)).replace("__DATA__", payload)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


_TEMPLATE = """<!doctype html><meta charset=utf-8><title>__TITLE__</title>
<style>
body{font:15px system-ui;margin:0;display:grid;grid-template-columns:150px 1fr 1fr;height:100vh}
#thumbs{overflow:auto;background:#eee;padding:6px}#thumbs img{width:100%;border:3px solid transparent;cursor:pointer;margin-bottom:6px}
#thumbs img.on{border-color:#2563eb}#mid{padding:10px;overflow:auto}#mid video{width:100%}#cur{width:100%;margin-top:8px;border:1px solid #ccc}
#right{overflow:auto;padding:10px;border-left:1px solid #ddd}.seg{padding:4px 6px;cursor:pointer;border-radius:4px}.seg.on{background:#dbeafe}
.t{color:#666;font-variant-numeric:tabular-nums;margin-right:8px}.hdr{font-weight:600;margin:12px 0 4px;color:#1e3a8a}mark{background:#fde68a}
</style>
<div id=thumbs></div>
<div id=mid><video id=v src="__VIDEO__" controls></video><img id=cur></div>
<div id=right><input id=q placeholder="Search transcript & slides" style="width:100%;padding:6px"><div id=list></div></div>
<script>
const D=__DATA__,v=document.getElementById('v'),fmt=s=>{s=Math.floor(s);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const th=document.getElementById('thumbs'),cur=document.getElementById('cur'),list=document.getElementById('list');
const slideStart={};D.timeline.forEach(e=>{if(e.slide_index&&!(e.slide_index in slideStart))slideStart[e.slide_index]=e.start});
D.slides.forEach(s=>{const i=document.createElement('img');i.src=s.image;i.id='th'+s.idx;i.title=s.text;
 i.onclick=()=>{if(s.idx in slideStart)v.currentTime=slideStart[s.idx]};th.appendChild(i)});
function render(q){list.innerHTML='';const re=q?new RegExp('('+q.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')+')','gi'):null;let last=null;
 D.segments.forEach((g,n)=>{if(re&&!g.text.match(re))return;
  if(g.slide_index!==last){last=g.slide_index;const h=document.createElement('div');h.className='hdr';h.textContent=g.slide_index?('Slide '+g.slide_index):'(no slide detected)';list.appendChild(h)}
  const d=document.createElement('div');d.className='seg';d.id='seg'+n;d.onclick=()=>{v.currentTime=g.start;v.play()};
  const t=document.createElement('span');t.className='t';t.textContent=fmt(g.start);d.appendChild(t);
  const x=document.createElement('span');x.innerHTML=re?g.text.replace(/[&<]/g,c=>({'&':'&amp;','<':'&lt;'})[c]).replace(re,'<mark>$1</mark>'):g.text.replace(/[&<]/g,c=>({'&':'&amp;','<':'&lt;'})[c]);d.appendChild(x);list.appendChild(d)})}
document.getElementById('q').oninput=e=>render(e.target.value.trim());render('');
v.ontimeupdate=()=>{const t=v.currentTime;const e=D.timeline.find(e=>t>=e.start&&t<e.end);
 document.querySelectorAll('.on').forEach(x=>x.classList.remove('on'));
 if(e&&e.slide_index){const s=D.slides.find(s=>s.idx===e.slide_index);cur.src=s.image;document.getElementById('th'+s.idx).classList.add('on')}
 const n=D.segments.findIndex(g=>t>=g.start&&t<g.end);if(n>=0){const el=document.getElementById('seg'+n);if(el){el.classList.add('on');el.scrollIntoView({block:'nearest'})}}};
</script>"""
