'use strict';

var INCIDENT_COLUMNS = [
  {id:'pending_confirmation', label:'待确认'},
  {id:'confirmed', label:'已确认'},
  {id:'escalated', label:'已升级'},
  {id:'closed', label:'已结束'}
];

function loadIncidents(force){
  if(state.incidents.status==='loading') return;
  if(force) WorkbenchAPI.invalidate('/api/v1/incidents');
  state.incidents.status = 'loading';
  state.incidents.error = '';
  renderAlertsKanban();
  WorkbenchAPI.get('/api/v1/incidents', {entity_id:state.entityId}, {noCache:!!force}).then(function(payload){
    state.incidents = {
      status:'success', data:payload.data, meta:payload.meta, error:'',
      active:state.incidents.active, mutating:false
    };
    renderAlertsKanban();
    renderCooldownList();
  }).catch(function(error){
    state.incidents.status = 'error';
    state.incidents.error = error;
    renderAlertsKanban();
  });
}

function renderAlertsKanban(){
  if(state.incidents.status==='idle'){
    loadIncidents(false);
    return;
  }
  if(state.incidents.status==='loading' && !state.incidents.data){
    $('#alertsKanban').innerHTML = '<div class="kanban-col">' + overviewStatePanel('loading', '正在读取事件状态…') + '</div>';
    return;
  }
  if(state.incidents.status==='error'){
    var error = state.incidents.error || {};
    $('#alertsKanban').innerHTML = '<div class="kanban-col">' + overviewStatePanel('error', esc(error.message || '事件加载失败'), '<button class="btn btn-primary" onclick="loadIncidents(true)">重试</button>') + '</div>';
    return;
  }
  var items = state.incidents.data.items || [];
  $('#alertsKanban').innerHTML = INCIDENT_COLUMNS.map(function(column){
    var selected = items.filter(function(item){
      return column.id==='closed' ? (item.status==='resolved' || item.status==='suppressed') : item.status===column.id;
    });
    var cards = selected.map(incidentCardHTML).join('') || '<div class="empty-hint">暂无</div>';
    return '<div class="kanban-col"><div class="kanban-col-title"><span>' + column.label + '</span><span class="badge badge-gray">' + selected.length + '</span></div>' + cards + '</div>';
  }).join('');
}

function incidentCardHTML(item){
  return '<button class="alert-card incident-button' + (item.level==='P0'?' level-p0':'') + '" onclick="openAlertDrawer(\'' + esc(item.incident_id) + '\')">'
    + '<div class="a-meta">' + levelBadge(item.level) + apiIncidentStatusBadge(item.status) + '</div>'
    + '<div class="a-title">' + esc(item.summary || item.incident_id) + '</div>'
    + '<div class="a-meta"><span>' + esc(overviewFormatTime(item.created_at)) + '</span><span>· ' + item.allowed_actions.length + ' 个可用操作</span></div></button>';
}

function renderCooldownList(){
  if(state.incidents.status!=='success') return;
  var closed = (state.incidents.data.items || []).filter(function(item){
    return item.status==='resolved' || item.status==='suppressed';
  });
  $('#cooldownList').innerHTML = closed.length ? closed.map(function(item){
    return '<div class="card" style="padding:10px 12px;margin-bottom:8px;"><div style="display:flex;justify-content:space-between;gap:12px;"><b>'
      + esc(item.summary || item.incident_id) + '</b>' + apiIncidentStatusBadge(item.status) + '</div><div class="muted">'
      + esc(item.note || '无处置备注') + '</div></div>';
  }).join('') : '<div class="empty-hint">暂无已结束事件</div>';
}

function openAlertDrawer(id){
  state.incidents.active = id;
  openDrawer('事件详情', overviewStatePanel('loading', '正在读取事件详情…'));
  WorkbenchAPI.get('/api/v1/incidents/' + encodeURIComponent(id), {}, {noCache:true}).then(function(payload){
    state.incidents.active = payload.data.incident;
    renderIncidentDrawer(payload.data.incident);
  }).catch(function(error){
    $('#drawerBody').innerHTML = overviewStatePanel('error', esc(error.message || '事件详情加载失败'));
  });
}

function renderIncidentDrawer(item){
  var html = '<div class="priority-meta">' + levelBadge(item.level) + apiIncidentStatusBadge(item.status) + '</div>';
  html += '<div class="priority-title">' + esc(item.summary || item.incident_id) + '</div>';
  html += '<div class="card" style="padding:10px 12px;margin-bottom:12px;"><div class="muted">事件编号</div>' + esc(item.incident_id)
    + '<div class="muted" style="margin-top:8px;">来源文档</div>' + esc(item.doc_id || '—')
    + '<div class="muted" style="margin-top:8px;">最近处置</div>' + esc(item.actor || 'system') + ' · ' + esc(overviewFormatTime(item.updated_at)) + '</div>';
  if(item.note) html += '<div class="hint-bar">处置备注：' + esc(item.note) + '</div>';
  html += '<div class="panel-title" style="margin-top:14px;">允许操作</div>';
  if(!item.allowed_actions.length){
    html += '<div class="empty-hint">当前事件已结束，无可用操作</div>';
  }else{
    html += '<div class="collection-actions">' + item.allowed_actions.map(function(action){
      return '<button class="btn ' + (action.action==='suppress'?'btn-danger':'btn-primary') + '" '
        + (state.incidents.mutating?'disabled':'') + ' onclick="transitionIncident(\'' + esc(item.incident_id) + '\',\'' + esc(action.action) + '\')">'
        + esc(action.label) + '</button>';
    }).join('') + '</div>';
  }
  $('#drawerBody').innerHTML = html;
}

function transitionIncident(id, action){
  if(state.incidents.mutating) return;
  var note = prompt('请输入处置备注（可留空）：', '');
  if(note===null) return;
  state.incidents.mutating = true;
  if(state.incidents.active && state.incidents.active.incident_id) renderIncidentDrawer(state.incidents.active);
  WorkbenchAPI.post('/api/v1/incidents/' + encodeURIComponent(id) + '/transition', {action:action, note:note}).then(function(payload){
    state.incidents.mutating = false;
    state.incidents.active = payload.data.incident;
    showToast('事件状态已更新', 'green');
    WorkbenchAPI.invalidate('/api/v1/incidents');
    renderIncidentDrawer(payload.data.incident);
    loadIncidents(true);
    state.overview.status = 'idle';
  }).catch(function(error){
    state.incidents.mutating = false;
    showToast(error.message || '事件状态更新失败', 'red');
    if(state.incidents.active && state.incidents.active.incident_id) renderIncidentDrawer(state.incidents.active);
  });
}
