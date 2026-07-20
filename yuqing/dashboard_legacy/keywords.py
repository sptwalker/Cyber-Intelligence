# -*- coding: utf-8 -*-
"""Legacy keyword-library management page."""

from __future__ import annotations

import html

from ..store import Store
from .common import _page
from .compat import load_watch


def render_keywords(store: Store, query_params: dict) -> str:
    """关键词库管理页面"""
    from ..keywords import KeywordManager, TAGS

    km = KeywordManager(store)
    try:
        entities = [(e["id"], (e.get("aliases") or [e["id"]])[0]) for e in load_watch().get("entities", [])]
    except SystemExit:
        entities = []
    if not entities:
        entities = [(r[0], r[0]) for r in store.conn.execute(
            'SELECT DISTINCT entity_id FROM clean WHERE entity_id IS NOT NULL').fetchall()]
    entity_ids = [eid for eid, _ in entities]
    current_entity = query_params.get('entity', [entity_ids[0] if entity_ids else None])[0]
    current_tag = query_params.get('tag', [''])[0]

    entity_options = ''.join(
        f"<option value='{html.escape(eid)}' {'selected' if eid==current_entity else ''}>{html.escape(label)}</option>"
        for eid, label in entities)
    entity_select = f"<select id='entitySelect' onchange='location.href=\"/keywords?entity=\"+this.value'>{entity_options}</select>" if entities else "<span class=muted>无实体</span>"

    custom_tags = [r[0] for r in store.conn.execute(
        'SELECT DISTINCT tag FROM keywords WHERE entity_id IS ? ORDER BY tag', (current_entity,)).fetchall()
        if r[0] not in TAGS]
    tag_filters = "<a href='/keywords?entity={}&tag='>全部</a>".format(current_entity or '')
    for tag_code, tag_name in list(TAGS.items()) + [(t, t) for t in custom_tags]:
        active = ' style="font-weight:bold"' if tag_code == current_tag else ''
        tag_filters += f" <a href='/keywords?entity={current_entity or ''}&tag={html.escape(tag_code)}'{active}>{html.escape(tag_name)}</a>"

    keywords = km.list(tag=current_tag if current_tag else None, entity_id=current_entity)
    kw_rows = ""
    for kw in keywords:
        tag_label = TAGS.get(kw['tag'], kw['tag'])
        source_label = '🤖AI' if kw['source'] == 'auto' else '👤人工'
        note_display = html.escape(kw['note'] or '')[:30] if kw['note'] else '-'
        kw_rows += f"""<tr>
            <td><input type='checkbox' class='rowchk' data-word="{html.escape(kw['word'])}" data-tag="{kw['tag']}"></td>
            <td>{html.escape(kw['word'])}</td>
            <td><span class='badge' style='background:#6e7781'>{tag_label}</span></td>
            <td>{kw['weight']:.2f}</td>
            <td>{source_label}</td>
            <td class=muted>{note_display}</td>
        </tr>"""
    if not kw_rows:
        kw_rows = "<tr><td colspan='6' class=muted>暂无关键词</td></tr>"

    suggestions = km.list_suggestions(status='pending', entity_id=current_entity, exclude_tag='seed_alias')
    sug_rows = ""
    for sug in suggestions[:10]:
        tag_label = TAGS.get(sug['suggested_tag'], sug['suggested_tag'])
        reason = html.escape(sug['reason'] or '')[:50]
        sug_rows += f"""<tr>
            <td>{html.escape(sug['word'])}</td>
            <td><span class='badge' style='background:#0969da'>{tag_label}</span></td>
            <td>{sug['score']:.2f}</td>
            <td class=muted>{reason}</td>
            <td>
                <button onclick='approveSuggestion({sug["id"]})' style='background:#1a7f37;color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer'>批准</button>
                <button onclick='rejectSuggestion({sug["id"]})' style='background:#cf222e;color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer'>拒绝</button>
            </td>
        </tr>"""
    if not sug_rows:
        sug_rows = "<tr><td colspan='5' class=muted>暂无推荐</td></tr>"

    tag_options = ''.join(f"<option value='{code}'>{name}</option>" for code, name in TAGS.items())
    body = f"""
<h1>关键词库管理</h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/login'>登录与采集</a></p>

<div style='margin:20px 0;padding:15px;background:#f6f8fa;border-radius:6px'>
    <strong>选择实体：</strong> {entity_select}
    <button onclick='runAnalysis()' id='runBtn' style='margin-left:20px;background:#0969da;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>▶ 运行分析</button>
    <span id='runStatus' class=muted style='margin-left:10px'></span>
</div>

<div id='addForm' style='display:none;margin:20px 0;padding:15px;background:#fff;border:2px solid #0969da;border-radius:6px'>
    <h3>添加关键词</h3>
    <form onsubmit='return addKeyword(event)'>
        <table style='border:none'>
            <tr><td>词：</td><td><input type='text' id='word' required style='width:200px'></td></tr>
            <tr><td>标签：</td><td>
                <select id='tag' style='width:200px'>{tag_options}</select>
                <input type='text' id='customTag' maxlength='20' placeholder='或输入自定义标签' style='width:160px;margin-left:8px'>
            </td></tr>
            <tr><td>权重：</td><td><input type='number' id='weight' value='1.0' min='0' max='1' step='0.1' style='width:200px'></td></tr>
            <tr><td>备注：</td><td><input type='text' id='note' style='width:200px'></td></tr>
        </table>
        <button type='submit' style='background:#2da44e;color:white;border:none;padding:8px 16px;border-radius:6px;cursor:pointer'>添加</button>
        <button type='button' onclick='document.getElementById("addForm").style.display="none"' style='margin-left:10px;padding:8px 16px'>取消</button>
    </form>
</div>

<div style='margin:20px 0'>
    <strong>标签筛选：</strong> {tag_filters}
</div>

<div style='display:flex;justify-content:space-between;align-items:center;margin:20px 0 8px'>
    <h2 style='margin:0'>关键词列表 ({len(keywords)} 条)</h2>
    <div>
        <button onclick='document.getElementById("addForm").style.display="block"' style='background:#2da44e;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>+ 添加</button>
        <button onclick='deleteSelected()' style='margin-left:8px;background:#cf222e;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>🗑 删除选中</button>
    </div>
</div>
<table id='kwTable'>
    <thead><tr>
        <th><input type='checkbox' id='chkAll' onclick='toggleAll(this)'></th>
        <th>词</th>
        <th onclick='sortTable(2,"text")' style='cursor:pointer;user-select:none'>标签 ⇅</th>
        <th onclick='sortTable(3,"num")' style='cursor:pointer;user-select:none'>权重 ⇅</th>
        <th onclick='sortTable(4,"text")' style='cursor:pointer;user-select:none'>来源 ⇅</th>
        <th>备注</th>
    </tr></thead>
    <tbody>{kw_rows}</tbody>
</table>

<h2>AI推荐 ({len(suggestions)} 条待审核)</h2>
<table>
    <thead><tr><th>词</th><th>建议标签</th><th>分数</th><th>理由</th><th>操作</th></tr></thead>
    <tbody>{sug_rows}</tbody>
</table>

<script>
const currentEntity = '{current_entity or ''}';

function addKeyword(e) {{
    e.preventDefault();
    const data = {{
        action: 'add',
        word: document.getElementById('word').value,
        tag: document.getElementById('customTag').value.trim() || document.getElementById('tag').value,
        weight: document.getElementById('weight').value,
        note: document.getElementById('note').value,
        entity_id: currentEntity
    }};

    fetch('/api/keywords', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(data)
    }})
    .then(r => r.json())
    .then(result => {{
        if (result.success) {{
            alert('添加成功');
            location.reload();
        }} else {{
            alert('添加失败: ' + result.message);
        }}
    }});
    return false;
}}

function toggleAll(cb) {{
    document.querySelectorAll('.rowchk').forEach(c => c.checked = cb.checked);
}}

function deleteSelected() {{
    const checks = [...document.querySelectorAll('.rowchk:checked')];
    if (!checks.length) {{ alert('请先勾选要删除的关键词'); return; }}
    if (!confirm('确认删除选中的 ' + checks.length + ' 个关键词?')) return;
    Promise.all(checks.map(c => {{
        const data = new URLSearchParams({{action: 'delete', word: c.dataset.word, tag: c.dataset.tag, entity_id: currentEntity}});
        return fetch('/api/keywords', {{method: 'POST', body: data}}).then(r => r.json());
    }})).then(results => {{
        const ok = results.filter(r => r.success).length;
        alert('已删除 ' + ok + '/' + results.length);
        location.reload();
    }});
}}

let sortDir = {{}};
function sortTable(col, type) {{
    const tbody = document.querySelector('#kwTable tbody');
    const rows = [...tbody.querySelectorAll('tr')].filter(r => r.querySelector('.rowchk'));
    if (!rows.length) return;
    const dir = sortDir[col] = -(sortDir[col] || 1);
    rows.sort((a, b) => {{
        let x = a.children[col].textContent.trim(), y = b.children[col].textContent.trim();
        if (type === 'num') return ((parseFloat(x) || 0) - (parseFloat(y) || 0)) * dir;
        return x.localeCompare(y, 'zh') * dir;
    }});
    rows.forEach(r => tbody.appendChild(r));
}}

function approveSuggestion(id) {{
    const data = new URLSearchParams({{action: 'approve', id: id}});
    fetch('/api/keywords', {{method: 'POST', body: data}})
    .then(r => r.json())
    .then(result => {{
        alert(result.message);
        if (result.success) location.reload();
    }});
}}

function rejectSuggestion(id) {{
    const data = new URLSearchParams({{action: 'reject', id: id}});
    fetch('/api/keywords', {{method: 'POST', body: data}})
    .then(r => r.json())
    .then(result => {{
        alert(result.message);
        if (result.success) location.reload();
    }});
}}

function setRunBtn(running) {{
    const btn = document.getElementById('runBtn');
    if (running) {{ btn.textContent = '⏸ 停止采集'; btn.style.background = '#cf222e'; btn.onclick = stopAnalysis; }}
    else {{ btn.textContent = '▶ 运行采集'; btn.style.background = '#0969da'; btn.onclick = runAnalysis; }}
}}

function runAnalysis() {{
    if (!confirm('确认运行一次网络内容分析？\\n流程：采集→分析→报告。需 opencli 已登录 + API key，可能耗时数分钟。')) return;
    fetch('/api/run', {{method: 'POST'}}).then(r => r.json()).then(() => pollRun());
}}

function stopAnalysis() {{
    if (!confirm('确认停止采集？将在完成当前平台后中止，已采数据保留。')) return;
    fetch('/api/run/stop', {{method: 'POST'}}).then(r => r.json()).then(d => {{
        document.getElementById('runStatus').innerHTML = '<span class="spin"></span>' + (d.message || '正在停止…');
        pollRun();
    }});
}}

function pollRun() {{
    fetch('/api/run/status').then(r => r.json()).then(d => {{
        const el = document.getElementById('runStatus');
        setRunBtn(d.running);
        if (d.running) {{
            el.innerHTML = '<span class="spin"></span>' + (d.current || '采集运行中…');
            setTimeout(pollRun, 1500);
        }} else if (d.last) {{
            el.textContent = (d.last.ok ? '✅ ' : '⚠️ ') + d.last.msg + ' (' + d.last.at + ')';
        }} else {{ el.textContent = ''; }}
    }});
}}
pollRun();   // 页面加载即同步一次状态（若正在跑则恢复轮询）
</script>
"""

    return _page("关键词库管理", body)
