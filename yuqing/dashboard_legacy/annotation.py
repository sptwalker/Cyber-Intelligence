# -*- coding: utf-8 -*-
"""Legacy active-learning annotation console."""

from __future__ import annotations

import html

from ..store import Store
from .common import _page
from .compat import load_watch


def render_annotate(store: Store, query_params: dict) -> str:
    """标注控制台（训练模式）：主动学习队列 + 多维标注 + 划选圈词。队列/写全走 fetch。"""
    from ..keywords import SUBJECTS, STANCES, IMPORTANCE, TAGS
    try:
        entities = [(e["id"], (e.get("aliases") or [e["id"]])[0]) for e in load_watch().get("entities", [])]
    except SystemExit:
        entities = [(r[0], r[0]) for r in store.conn.execute(
            "SELECT DISTINCT entity_id FROM clean WHERE entity_id IS NOT NULL").fetchall()]
    cur = query_params.get("entity", [entities[0][0] if entities else ""])[0]
    ent_opts = "".join(f"<option value='{html.escape(e)}' {'selected' if e==cur else ''}>{html.escape(nm)}</option>"
                       for e, nm in entities)
    subj_opts = "".join(f"<label><input type=radio name=subject value='{s}'>{s}</label> " for s in SUBJECTS)
    stance_opts = "".join(f"<label><input type=radio name=stance value='{s}'>{s}</label> " for s in STANCES)
    imp_opts = "".join(f"<label><input type=radio name=importance value='{s}'>{s}</label> " for s in IMPORTANCE)
    role_opts = "".join(f"<option value='{c}'>{n}</option>" for c, n in TAGS.items())
    done = store.annotated_count()
    body = f"""
<h1>标注控制台 <span class=muted>（训练模式）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/keywords'>关键词库</a> ｜ <a href='/watch'>监控配置</a>
   ｜ 累计已标注 <b>{done}</b> 条</p>
<p class=muted>系统按"最没把握+高影响+多样"挑代表样本给你标；圈选的词会进关键词库待审，产品名会进种子建议。</p>

<div style='margin:12px 0'>实体：
  <select id='entSel' onchange='location.href="/annotate?entity="+this.value'>{ent_opts}</select>
  <span id='queueInfo' class=muted style='margin-left:12px'>加载队列…</span>
</div>

<div id='card' class='card' style='border:1px solid #d0d7de;border-radius:8px;padding:16px;display:none'>
  <div id='meta' class=muted></div>
  <div style='margin:6px 0'><b>采样原因：</b><span id='reason'></span></div>
  <div id='body' style='background:#f6f8fa;padding:12px;border-radius:6px;line-height:1.9;user-select:text;cursor:text'></div>
  <div class=muted style='margin:6px 0'>机器判定(对照)：<span id='mach'></span></div>
  <hr>
  <div style='margin:8px 0'><b>主体：</b>{subj_opts}</div>
  <div style='margin:8px 0'><b>立场：</b>{stance_opts}</div>
  <div style='margin:8px 0'><b>重要性：</b>{imp_opts} <span class=muted>（预填，可改）</span></div>
  <div style='margin:8px 0'><b>圈选的词</b> <span class=muted>（在正文里鼠标划选自动加行）</span>：
    <div id='words'></div>
    <template id='wordRow'>
      <div style='margin:4px 0'>「<span class=w></span>」→
        <select class=role>{role_opts}</select>
        <button type=button class=del>✕</button></div>
    </template>
  </div>
  <div style='margin:8px 0'>备注：<input id='note' style='width:60%'></div>
  <div style='margin-top:12px'>
    <button onclick='save()' style='background:#2da44e;color:#fff;border:none;padding:8px 18px;border-radius:6px;cursor:pointer'>保存并下一条 ▶</button>
    <button onclick='nextCard()' style='margin-left:8px;padding:8px 18px'>跳过</button>
    <span id='msg' class=muted style='margin-left:12px'></span>
  </div>
</div>
<div id='empty' class=muted style='display:none;padding:40px;text-align:center'>🎉 该实体暂无待标样本</div>

<script>
const ENTITY='{cur}';
let queue=[], cur_=null;
function esc(s){{return (s||'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));}}
function loadQueue(){{
  fetch('/api/annotate/queue?entity='+encodeURIComponent(ENTITY)).then(r=>r.json()).then(d=>{{
    queue=d.queue||[]; document.getElementById('queueInfo').textContent='待标 '+queue.length+' 条';
    nextCard();
  }});
}}
function nextCard(){{
  document.querySelectorAll('input[type=radio]').forEach(x=>x.checked=false);
  document.getElementById('words').innerHTML=''; document.getElementById('note').value='';
  document.getElementById('msg').textContent='';
  if(!queue.length){{document.getElementById('card').style.display='none';document.getElementById('empty').style.display='block';return;}}
  cur_=queue.shift();
  document.getElementById('card').style.display='block'; document.getElementById('empty').style.display='none';
  document.getElementById('meta').innerHTML='['+esc(cur_.platform)+'] @'+esc(cur_.author||'?')+' 粉'+(cur_.author_followers||0)+' · '+esc(cur_.publish_ts||'')+' · <a href="'+esc(cur_.url||'#')+'" target=_blank>原文</a>';
  document.getElementById('reason').textContent=cur_.reason||'';
  document.getElementById('body').textContent=cur_.text||'';
  document.getElementById('mach').textContent=(cur_.polarity||'?')+' / 置信'+(cur_.confidence??'?')+' / risk'+(cur_.risk??'?');
  // 重要性预填
  const imp = (cur_.risk>=40)?'高':(cur_.risk>=10)?'中':'低';
  const el=[...document.querySelectorAll('input[name=importance]')].find(x=>x.value===imp); if(el)el.checked=true;
  document.getElementById('queueInfo').textContent='待标 '+queue.length+' 条';
}}
// 划选圈词
document.getElementById('body').addEventListener('mouseup',()=>{{
  const w=(window.getSelection().toString()||'').trim();
  if(!w||w.length>20)return;
  const t=document.getElementById('wordRow').content.cloneNode(true);
  t.querySelector('.w').textContent=w;
  t.querySelector('.del').onclick=e=>e.target.closest('div').remove();
  document.getElementById('words').appendChild(t);
  window.getSelection().removeAllRanges();
}});
function radioVal(n){{const e=document.querySelector('input[name='+n+']:checked');return e?e.value:null;}}
function save(){{
  const words=[...document.querySelectorAll('#words > div')].map(d=>({{word:d.querySelector('.w').textContent, role:d.querySelector('.role').value}}));
  const payload={{doc_id:cur_.doc_id, entity_id:cur_.entity_id||ENTITY, sample_source:'active',
    subject:radioVal('subject'), stance:radioVal('stance'), importance:radioVal('importance'),
    picked_words:words, note:document.getElementById('note').value}};
  if(!payload.subject||!payload.stance){{document.getElementById('msg').textContent='请至少选主体和立场';return;}}
  fetch('/api/annotate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}})
    .then(r=>r.json()).then(d=>{{ if(d.success) nextCard(); else document.getElementById('msg').textContent='保存失败: '+(d.message||''); }});
}}
loadQueue();
</script>
"""
    return _page("标注控制台", body)
