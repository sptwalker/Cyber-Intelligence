'use strict';

function loadBacklog(force){
  if(state.backlog.status==='loading') return;
  if(force) WorkbenchAPI.invalidate('/api/v1/backlog');
  state.backlog.status = 'loading';
  state.backlog.error = '';
  renderBacklogTable();
  WorkbenchAPI.get('/api/v1/backlog', {
    entity_id:state.entityId,
    range:state.backlog.range
  }, {noCache:!!force}).then(function(payload){
    state.backlog = {status:'success', data:payload.data, meta:payload.meta, error:'', range:payload.data.range};
    renderBacklogTable();
  }).catch(function(error){
    state.backlog.status = 'error';
    state.backlog.error = error;
    renderBacklogTable();
  });
}

function renderBacklogTable(){
  if(state.backlog.status==='idle'){
    loadBacklog(false);
    return;
  }
  if(state.backlog.status==='loading' && !state.backlog.data){
    $('#backlogQuality').innerHTML = overviewStatePanel('loading', '正在聚合真实用户诉求…');
    $('#backlogTableBody').innerHTML = '';
    return;
  }
  if(state.backlog.status==='error'){
    var error = state.backlog.error || {};
    $('#backlogQuality').innerHTML = overviewStatePanel('error', esc(error.message || '诉求加载失败'), '<button class="btn btn-primary" onclick="loadBacklog(true)">重试</button>');
    $('#backlogTableBody').innerHTML = '';
    return;
  }
  $('#backlogQuality').innerHTML = qualityNotice(state.backlog.meta);
  var items = state.backlog.data.items || [];
  if(!items.length){
    var text = state.backlog.meta.data_quality==='ok' ? '当前没有形成可聚合诉求' : '数据不完整，暂不能判断是否没有用户诉求';
    $('#backlogTableBody').innerHTML = '<tr><td colspan="5">' + overviewStatePanel('empty', text) + '</td></tr>';
    return;
  }
  $('#backlogTableBody').innerHTML = items.map(function(item){
    return '<tr><td><span class="badge badge-outline">' + esc(item.kind) + '</span></td><td><b>' + esc(item.topic) + '</b></td>'
      + '<td>' + item.count + '</td><td>' + item.heat + '</td><td><code>' + esc(item.sample) + '</code></td></tr>';
  }).join('');
}

function downloadBacklogCSV(){
  var params = new URLSearchParams();
  if(state.entityId) params.set('entity_id', state.entityId);
  window.location.href = '/api/v1/backlog.csv' + (params.toString() ? '?' + params.toString() : '');
}
