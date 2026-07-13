'use strict';

var PLATFORM_LABELS = {
  weibo:'微博', zhihu:'知乎', xiaohongshu:'小红书', douyin:'抖音', bilibili:'B站',
  tieba:'贴吧', hupu:'虎扑', smzdm:'值得买', weixin:'公众号', heimao:'黑猫投诉'
};

function loadCollection(force, includeLogin){
  if(state.collection.status==='loading' && !force) return;
  if(force) WorkbenchAPI.invalidate('/api/v1/collection/status');
  state.collection.status = 'loading';
  state.collection.error = '';
  renderCollectTable();
  WorkbenchAPI.get('/api/v1/collection/status', {
    entity_id:state.entityId,
    include_login:includeLogin===false ? '0' : '1'
  }, {noCache:true}).then(function(payload){
    var previous = state.collection.data;
    if(includeLogin===false && previous && previous.platforms){
      var oldLogin = {};
      previous.platforms.forEach(function(item){ oldLogin[item.platform] = item.login; });
      payload.data.platforms.forEach(function(item){ if(oldLogin[item.platform]) item.login = oldLogin[item.platform]; });
      payload.data.bridge = previous.bridge;
    }
    state.collection.status = 'success';
    state.collection.data = payload.data;
    state.collection.meta = payload.meta;
    state.collection.error = '';
    renderCollectTable();
    renderHealthStrip();
    scheduleCollectionPoll();
  }).catch(function(error){
    state.collection.status = 'error';
    state.collection.error = error;
    renderCollectTable();
    stopCollectionPolling();
  });
}

function renderCollectTable(){
  if(state.collection.status==='idle'){
    loadCollection(false, true);
    return;
  }
  if(state.collection.status==='loading' && !state.collection.data){
    $('#collectionExecution').innerHTML = overviewStatePanel('loading', '正在检测采集环境和登录状态…');
    $('#collectTableBody').innerHTML = '<tr><td colspan="6">' + overviewStatePanel('loading', '正在读取平台状态…') + '</td></tr>';
    return;
  }
  if(state.collection.status==='error'){
    var error = state.collection.error || {};
    var action = WorkbenchAPI.isAuthError(error)
      ? '<a class="btn btn-primary" href="/auth/login?next=%2Fv2">重新登录</a>'
      : '<button class="btn btn-primary" onclick="loadCollection(true,true)">重试</button>';
    $('#collectionExecution').innerHTML = overviewStatePanel('error', esc(error.message || '采集状态加载失败'), action);
    $('#collectTableBody').innerHTML = '';
    return;
  }

  var data = state.collection.data;
  renderCollectionExecution(data);
  var rows = data.platforms || [];
  if(!rows.length){
    $('#collectTableBody').innerHTML = '<tr><td colspan="6">' + overviewStatePanel('empty', '监控配置中没有平台') + '</td></tr>';
    return;
  }
  $('#collectTableBody').innerHTML = rows.map(function(item){
    var login = item.login || {};
    var loginText = item.login_required
      ? (login.logged_in ? '<span class="badge badge-green">已登录</span>' : '<span class="badge badge-amber">需登录</span>')
      : '<span class="badge badge-gray">免登录</span>';
    var fetched = item.n_fetched===null || item.n_fetched===undefined ? '—' : item.n_fetched + ' 条';
    return '<tr><td><b>' + esc(PLATFORM_LABELS[item.platform] || item.platform) + '</b></td>'
      + '<td>' + collectionHealthBadge(item.health) + '</td>'
      + '<td>' + esc(overviewFormatTime(item.ts)) + '</td>'
      + '<td>' + fetched + '</td>'
      + '<td>' + loginText + '</td>'
      + '<td class="muted">' + esc(item.note || login.error || '—') + '</td></tr>';
  }).join('');
}

function renderCollectionExecution(data){
  var execution = data.execution || {};
  var run = data.run || {};
  var bridge = data.bridge || {};
  var runningText = run.running ? (run.current || '运行中') : '当前空闲';
  var disabledStart = state.collection.mutating || run.running || !execution.can_run;
  var disabledStop = state.collection.mutating || !run.running;
  var quality = state.collection.meta ? qualityNotice(state.collection.meta) : '';
  var html = quality + '<div class="collection-execution-grid">';
  html += '<div><div class="stat-label">采集执行环境</div><b>' + esc(execution.mode || 'unknown') + '</b><p class="muted">' + esc(execution.message || '') + '</p></div>';
  html += '<div><div class="stat-label">当前运行状态</div><b>' + esc(runningText) + '</b><p class="muted">浏览器桥：' + esc(bridge.message || '未检测') + '</p></div>';
  html += '<div class="collection-actions"><button class="btn btn-primary" ' + (disabledStart?'disabled':'') + ' onclick="startCollectionRun()">启动全量采集</button>';
  html += '<button class="btn btn-danger" ' + (disabledStop?'disabled':'') + ' onclick="stopCollectionRun()">请求停止</button>';
  html += '<button class="btn" ' + (state.collection.mutating?'disabled':'') + ' onclick="loadCollection(true,true)">重新检测</button></div></div>';
  $('#collectionExecution').innerHTML = html;
}

function collectionHealthBadge(health){
  if(health==='ok') return '<span class="badge badge-green">正常</span>';
  if(health==='suspect') return '<span class="badge badge-amber">存疑</span>';
  if(health==='fail') return '<span class="badge badge-red">失败</span>';
  return '<span class="badge badge-gray">未知</span>';
}

function startCollectionRun(){
  if(state.collection.mutating) return;
  state.collection.mutating = true;
  renderCollectTable();
  WorkbenchAPI.post('/api/v1/collection/run', {}).then(function(payload){
    showToast(payload.data.message, 'green');
    WorkbenchAPI.invalidate('/api/v1/collection/status');
    state.collection.mutating = false;
    loadCollection(true, false);
  }).catch(collectionMutationFailed);
}

function stopCollectionRun(){
  if(state.collection.mutating) return;
  state.collection.mutating = true;
  renderCollectTable();
  WorkbenchAPI.post('/api/v1/collection/stop', {}).then(function(payload){
    showToast(payload.data.message, 'green');
    WorkbenchAPI.invalidate('/api/v1/collection/status');
    state.collection.mutating = false;
    loadCollection(true, false);
  }).catch(collectionMutationFailed);
}

function collectionMutationFailed(error){
  state.collection.mutating = false;
  showToast(error.message || '操作失败', 'red');
  renderCollectTable();
}

function scheduleCollectionPoll(){
  stopCollectionPolling();
  if(state.activeView!=='collect' || !state.collection.data || !state.collection.data.run.running) return;
  state.collection.pollTimer = setTimeout(function(){ loadCollection(true, false); }, 3000);
}

function stopCollectionPolling(){
  if(state.collection.pollTimer){
    clearTimeout(state.collection.pollTimer);
    state.collection.pollTimer = null;
  }
}
