# -*- coding: utf-8 -*-
"""Legacy watch-file and system configuration pages."""

from __future__ import annotations

import html

from .common import _page
from .compat import watch_path


def render_watch() -> str:
    """监控配置编辑页：直接编辑 watch.yaml（唯一事实源）。保存前后端强校验，写前自动备份。"""
    path = watch_path()
    try:
        with open(path, encoding="utf-8") as stream:
            content = stream.read()
    except Exception as exc:
        content = f"# 读取失败：{exc}"
    body = f"""
<h1>监控配置 <span class=muted>（watch.yaml，采集的搜索对象来源）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/login'>登录与采集</a> ｜ <a href='/keywords'>关键词库</a> ｜ <a href='/accounts'>账号白名单</a></p>
<p class=muted>生效文件：<code>{html.escape(path)}</code>；保存后下轮采集/刷新即生效，无需重启。写入前自动备份到 <code>watch.yaml.bak</code>。</p>

<div style='margin:16px 0;padding:14px;background:#f6f8fa;border-radius:8px'>
  <b>🌱 种子词建议</b> <span class=muted>（系统挖的"产品词"，确认后写入 aliases 扩召回；评价词自动分流到 <a href='/keywords'>关键词库</a>）</span>
  <button onclick='mineSeeds()' style='margin-left:10px;padding:4px 12px;border-radius:6px;border:1px solid #d0d7de;cursor:pointer'>🔄 生成建议</button>
  <span id='mineMsg' class=muted style='margin-left:8px'></span>
  <table id='seedTbl' style='margin-top:8px;display:none'>
    <thead><tr><th>词</th><th>区分度</th><th>共现</th><th>来源</th><th>操作</th></tr></thead>
    <tbody id='seedRows'></tbody>
  </table>
</div>

<details style='margin:8px 0'><summary class=muted style='cursor:pointer'>字段说明（点开）</summary>
<pre class=muted>platforms: [weibo, zhihu, xiaohongshu, douyin, bilibili, tieba, hupu, smzdm, weixin, heimao]
            # 只能填以上平台；决定采哪些站
entities:
  - id: youdoo            # 实体唯一标识（进 clean/报告/看板下拉）
    type: self            # self=报告主体+预警；competitor=只做SOV/情绪对比
    aliases: ["Youdoo Box","有度盒子"]   # aliases[0]=搜索词；全部别名用于相关性过滤（必须含其一）
    must_not: ["Doo Prime"]              # 命中即判非本品，硬排除同名歧义
    track_users: ["weibo:123"]           # 可选，定向抓这些账号
    crisis_boost: ["卡顿","死机","退货"]  # 该实体专属危机词，命中→风险×1.5</pre></details>

<textarea id='yaml' style='width:100%;height:420px;font:13px/1.5 Consolas,Menlo,monospace;padding:10px;border:1px solid #d0d7de;border-radius:6px'>{html.escape(content)}</textarea>
<p>
    <button onclick='saveWatch()' style='background:#2da44e;color:white;border:none;padding:8px 18px;border-radius:6px;cursor:pointer'>💾 保存</button>
    <button onclick='location.reload()' style='margin-left:8px;padding:8px 18px'>撤销更改</button>
    <span id='watchMsg' class=muted style='margin-left:12px'></span>
</p>

<script>
function saveWatch() {{
    const el = document.getElementById('watchMsg');
    el.textContent = '保存中…';
    fetch('/api/watch', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{content: document.getElementById('yaml').value}})}})
    .then(r => r.json()).then(d => {{
        el.innerHTML = (d.success ? '✅ ' : '⚠️ ') + d.message;
    }}).catch(e => {{ el.textContent = '⚠️ 请求失败'; }});
}}
function esc(s){{return (s||'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
function loadSeeds() {{
  fetch('/api/seed/list').then(r=>r.json()).then(d=>{{
    const rows=d.seeds||[]; const tb=document.getElementById('seedRows');
    document.getElementById('seedTbl').style.display = rows.length?'table':'none';
    tb.innerHTML = rows.map(s=>'<tr><td><b>'+esc(s.word)+'</b></td><td>×'+
      (s.reason||'').replace(/[^0-9.×]/g,'').slice(0,5)+'</td><td>'+esc(s.reason||'')+
      '</td><td class=muted>'+esc((s.source_docs||'').slice(0,30))+'</td><td>'+
      '<button onclick="seedAct('+s.id+',\\'approve\\')" style="background:#2da44e;color:#fff;border:none;padding:3px 10px;border-radius:5px;cursor:pointer">✓加入aliases</button> '+
      '<button onclick="seedAct('+s.id+',\\'reject\\')" style="padding:3px 8px">✗</button></td></tr>').join('');
    if(!rows.length) document.getElementById('mineMsg').textContent='暂无待确认种子词';
  }});
}}
function mineSeeds() {{
  document.getElementById('mineMsg').textContent='挖词中…（需 EMBED key + 已向量化数据）';
  fetch('/api/seed',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'mine'}})}})
    .then(r=>r.json()).then(d=>{{ document.getElementById('mineMsg').textContent=d.message||''; loadSeeds(); }});
}}
function seedAct(id, action) {{
  fetch('/api/seed',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:action,id:id}})}})
    .then(r=>r.json()).then(d=>{{ document.getElementById('mineMsg').textContent=d.message||''; loadSeeds(); }});
}}
loadSeeds();
</script>
"""
    return _page("监控配置", body)


def render_config(*, saved: bool = False, test_msg: str = "") -> str:
    from .. import config
    rows = ""
    for key, label, secret, display, is_set in config.masked():
        if key == "YUQING_MODE":
            current = display or "daily"
            options = "".join(f"<option value='{mode}' {'selected' if mode==current else ''}>{mode}</option>"
                              for mode in ("daily", "training"))
            field = f"<select name={key}>{options}</select>"
        elif secret:
            placeholder = f"已设置 {html.escape(display)}，留空则不改" if is_set else "未设置"
            field = f"<input type=password name={key} placeholder='{placeholder}' autocomplete=off>"
        else:
            field = f"<input type=text name={key} value='{html.escape(display)}'>"
        rows += (f"<tr><td>{html.escape(label)}</td><td>{field}</td>"
                 f"<td class=muted>{'✓已配置' if is_set else '—'}</td></tr>")
    notice = "<p style='color:#1a7f37'>✓ 已保存</p>" if saved else ""
    if test_msg:
        notice += f"<p class=muted>{html.escape(test_msg)}</p>"
    tests = (" ｜ 测试连通："
             "<a href='/config/test?p=deepseek'>DeepSeek</a> "
             "<a href='/config/test?p=minimax'>MiniMax</a> "
             "<a href='/config/test?p=feishu'>飞书</a>")
    body = (
        "<h1>系统配置 <span class=muted>（本机写入，密钥仅存本地 yuqing_config.json）</span></h1>"
        f"<p><a href='/'>← 返回看板</a>{tests}</p>" + notice +
        "<form method=post action='/config'>"
        "<table><tr><th>配置项</th><th>值</th><th>状态</th></tr>" + rows + "</table>"
        "<p><button type=submit>保存</button> "
        "<span class=muted>密钥留空=保持原值；明文项留空=清除回退默认</span></p></form>"
        "<p class=muted>⚠️ 保存后需重跑批 <code>python -m yuqing.run</code> 生效。"
        "密钥以明文存本地文件（同 env 风险），已 gitignore 不进仓库。</p>")
    return _page("系统配置", body)
