# -*- coding: utf-8 -*-
"""Legacy login and account access pages."""

from __future__ import annotations

import html

from ..store import Store
from .common import _page


def render_login() -> str:
    """登录与采集页：桥状态 + 各平台登录态（JS 异步拉取）+ 一键开登录页 + 运行采集。"""
    body = """
<h1>登录与采集</h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/watch'>监控配置</a> ｜ <a href='/keywords'>关键词库</a></p>
<p class=muted>采集复用你本机 Chrome 的登录会话。登录需人工扫码/短信（无法全自动），登录后点"运行采集"即可无人值守跑批。</p>

<div id='bridge' class=muted style='margin:12px 0'>检测浏览器桥…</div>

<h2>平台登录状态</h2>
<table>
    <thead><tr><th>平台</th><th>登录态</th><th>身份/备注</th><th>操作</th></tr></thead>
    <tbody id='loginRows'><tr><td colspan='4' class=muted>加载中…</td></tr></tbody>
</table>
<p><button onclick='refreshLogin()'>🔄 重新检测</button></p>

<div style='margin-top:24px;padding:15px;background:#f6f8fa;border-radius:6px'>
    <strong>登录好后：</strong>
    <button onclick='runAnalysis()' id='runBtn' style='margin-left:12px;background:#0969da;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>▶ 运行采集</button>
    <span id='runStatus' class=muted style='margin-left:10px'></span>
</div>

<script>
function badge(ok) {
    return ok ? '<span style="color:#1a7f37">✅ 已登录</span>' : '<span style="color:#8c8c8c">⬜ 未登录</span>';
}
function refreshLogin() {
    document.getElementById('bridge').textContent = '检测浏览器桥…';
    document.getElementById('loginRows').innerHTML = '<tr><td colspan="4" class=muted>检测中…（黑猫走浏览器桥略慢）</td></tr>';
    fetch('/api/login/status').then(r => r.json()).then(d => {
        document.getElementById('bridge').innerHTML = (d.bridge_ok ? '✅ ' : '❌ ') + d.bridge_msg;
        document.getElementById('loginRows').innerHTML = d.platforms.map(p => {
            const extra = p.identity || p.error || (p.method === 'browser' ? '浏览器探测' : '');
            return '<tr><td>' + p.platform + '</td><td>' + badge(p.logged_in) + '</td>'
                + '<td class=muted>' + extra + '</td>'
                + '<td><button onclick="openLogin(\\'' + p.platform + '\\')">打开登录页</button></td></tr>';
        }).join('') || '<tr><td colspan="4" class=muted>无需登录的平台</td></tr>';
    }).catch(() => { document.getElementById('bridge').textContent = '❌ 状态查询失败'; });
}
function openLogin(p) {
    fetch('/api/login/open', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({platform: p})})
    .then(r => r.json()).then(d => alert(d.message));
}
function setRunBtn(running) {
    const btn = document.getElementById('runBtn');
    if (running) { btn.textContent = '⏸ 停止采集'; btn.style.background = '#cf222e'; btn.onclick = stopAnalysis; }
    else { btn.textContent = '▶ 运行采集'; btn.style.background = '#0969da'; btn.onclick = runAnalysis; }
}
function runAnalysis() {
    if (!confirm('确认运行一次采集分析？\\n流程：采集→分析→报告，可能耗时数分钟。')) return;
    fetch('/api/run', {method: 'POST'}).then(r => r.json()).then(() => pollRun());
}
function stopAnalysis() {
    if (!confirm('确认停止采集？将在完成当前平台后中止，已采数据保留。')) return;
    fetch('/api/run/stop', {method: 'POST'}).then(r => r.json()).then(d => {
        document.getElementById('runStatus').innerHTML = '<span class="spin"></span>' + (d.message || '正在停止…');
        pollRun();
    });
}
function pollRun() {
    fetch('/api/run/status').then(r => r.json()).then(d => {
        const el = document.getElementById('runStatus');
        setRunBtn(d.running);
        if (d.running) {
            el.innerHTML = '<span class="spin"></span>' + (d.current || '采集运行中…');
            setTimeout(pollRun, 1500);
        } else if (d.last) {
            el.textContent = (d.last.ok ? '✅ ' : '⚠️ ') + d.last.msg + ' (' + d.last.at + ')';
        } else { el.textContent = ''; }
    });
}
refreshLogin();
pollRun();
</script>
"""
    return _page("登录与采集", body)


def render_accounts(store: Store) -> str:
    """官方账号白名单管理：登记官方/准官方/媒体账号，主体维确定性判定用。"""
    rows = ""
    for account in store.list_accounts():
        account = dict(account)
        rows += (f"<tr><td>{html.escape(account.get('platform') or '(全平台)')}</td>"
                 f"<td>{html.escape(account['author'])}</td><td>{html.escape(account['subject_type'])}</td>"
                 f"<td class=muted>{html.escape(account.get('entity_id') or '')}</td>"
                 f"<td><button onclick='delAcct({account['id']})'>✕</button></td></tr>")
    if not rows:
        rows = "<tr><td colspan=5 class=muted>暂无登记账号（未登记的一律判为 用户·KOL）</td></tr>"
    body = f"""
<h1>官方账号白名单 <span class=muted>（主体维确定性判定）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/watch'>监控配置</a> ｜ <a href='/annotate'>标注</a></p>
<p class=muted>登记官方/准官方/媒体账号，分析时命中即确定性判主体（盖过 LLM）；未登记默认"用户·KOL"。</p>
<div style='margin:14px 0;padding:12px;background:#f6f8fa;border-radius:8px'>
  <b>添加</b>：账号 <input id='au' placeholder='昵称，须与帖子作者一致' style='width:220px'>
  类型 <select id='st'><option>官方</option><option>准官方</option><option>媒体</option></select>
  平台 <input id='pf' placeholder='留空=全平台' style='width:90px'>
  <button onclick='addAcct()' style='background:#2da44e;color:#fff;border:none;padding:5px 14px;border-radius:6px;cursor:pointer'>+ 添加</button>
  <span id='acctMsg' class=muted style='margin-left:8px'></span>
</div>
<table><thead><tr><th>平台</th><th>账号</th><th>类型</th><th>实体</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<script>
function addAcct() {{
  const au=document.getElementById('au').value.trim();
  if(!au){{document.getElementById('acctMsg').textContent='请填账号';return;}}
  fetch('/api/accounts',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{action:'add',author:au,subject_type:document.getElementById('st').value,platform:document.getElementById('pf').value.trim()}})}})
    .then(r=>r.json()).then(d=>{{ if(d.success) location.reload(); else document.getElementById('acctMsg').textContent=d.message||'失败'; }});
}}
function delAcct(id) {{
  if(!confirm('删除该账号登记？')) return;
  fetch('/api/accounts',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'delete',id:id}})}})
    .then(r=>r.json()).then(d=>{{ if(d.success) location.reload(); }});
}}
</script>
"""
    return _page("账号白名单", body)
