'use strict';

function loadOverview(force){
  if(state.overview.status==='loading') return;
  if(force) WorkbenchAPI.invalidate('/api/v1/overview');
  state.overview.status = 'loading';
  state.overview.error = '';
  renderOverview();
  WorkbenchAPI.get('/api/v1/overview', {
    entity_id:state.entityId,
    range:state.range
  }, {noCache:!!force}).then(function(payload){
    state.overview = {status:'success', data:payload.data, meta:payload.meta, error:''};
    state.entityId = payload.meta.entity_id || '';
    renderOverview();
    renderHealthStrip();
    renderNav();
  }).catch(function(error){
    state.overview = {status:'error', data:null, meta:null, error:error};
    renderOverview();
    renderHealthStrip();
  });
}

function renderOverview(){
  if(state.overview.status==='idle'){
    loadOverview(false);
    return;
  }
  if(state.overview.status==='loading'){
    $('#topRiskCard').innerHTML = overviewStatePanel('loading', '正在读取真实舆情数据…');
    $('#overviewStats').innerHTML = overviewStatSkeletons();
    $('#sentimentTrendChart').innerHTML = overviewStatePanel('loading', '正在加载趋势…');
    $('#todoList').innerHTML = overviewStatePanel('loading', '正在整理真实待办…');
    return;
  }
  if(state.overview.status==='error'){
    var error = state.overview.error || {};
    var action = WorkbenchAPI.isAuthError(error)
      ? '<a class="btn btn-primary" href="/auth/login?next=%2Fv2">重新登录</a>'
      : '<button class="btn btn-primary" onclick="loadOverview(true)">重试</button>';
    $('#topRiskCard').innerHTML = overviewStatePanel('error', esc(error.message || '总览加载失败'), action);
    $('#overviewStats').innerHTML = '';
    $('#sentimentTrendChart').innerHTML = '';
    $('#todoList').innerHTML = '';
    return;
  }
  var data = state.overview.data;
  var meta = state.overview.meta;
  renderTopRiskCard(data, meta);
  renderOverviewStats(data, meta);
  renderOverviewInsights(data);
  renderTodoList(data);
}

function overviewStatePanel(kind, message, action){
  var icon = kind==='loading' ? '◌' : kind==='error' ? '!' : '○';
  return '<div class="state-panel state-' + kind + '"><b>' + icon + ' ' + message + '</b>'
    + (action ? '<div class="state-action">' + action + '</div>' : '') + '</div>';
}

function overviewStatSkeletons(){
  var html = '';
  for(var i=0;i<4;i++) html += '<div class="stat-card skeleton-card"><div></div><div></div><div></div></div>';
  return html;
}

function qualityNotice(meta){
  if(!meta || meta.data_quality==='ok') return '';
  var notes = (meta.quality_notes || []).map(esc).join('；');
  var label = meta.data_quality==='unknown' ? '数据状态未知' : '数据可能不完整';
  return '<div class="quality-banner quality-' + esc(meta.data_quality) + '"><b>' + label + '</b>'
    + (notes ? '<span>' + notes + '</span>' : '') + '</div>';
}

function renderTopRiskCard(data, meta){
  var top = data.top_incident;
  var html = qualityNotice(meta);
  if(!top){
    var text = meta.data_quality==='ok' ? '当前没有进行中的预警事件' : '当前无法确认是否无风险，请先核查采集状态';
    $('#topRiskCard').innerHTML = html + overviewStatePanel('empty', text);
    return;
  }
  html += '<div class="priority-row">';
  html += '<div class="priority-main"><div class="priority-meta">' + levelBadge(top.level)
    + apiIncidentStatusBadge(top.status) + '<span class="muted">' + esc(overviewFormatTime(top.created_at)) + '</span></div>';
  html += '<div class="priority-title">⚠ ' + esc(top.summary || top.incident_id) + '</div>';
  html += '<div class="muted">事件编号 ' + esc(top.incident_id) + ' · 当前责任记录 ' + esc(top.actor || 'system') + '</div></div>';
  html += '<button class="btn btn-primary" onclick="switchView(\'alerts\')">进入预警中心 →</button></div>';
  $('#topRiskCard').innerHTML = html;
}

function metricDisplay(value, meta){
  if(value===null || value===undefined) return '—';
  if(value===0 && meta && meta.data_quality==='unknown') return '—';
  return value;
}

function renderOverviewStats(data, meta){
  var metrics = data.metrics || {};
  var trend = data.sentiment_trend || [];
  var stats = [
    {label:'所选范围声量', value:metricDisplay(metrics.total_volume, meta), hint:'按当前时间范围统计', spark:trend.map(function(x){return x.total;}), color:'#2563eb'},
    {label:'品牌健康指数 BHI', value:metricDisplay(metrics.bhi, meta), hint:metrics.bhi_label || '暂无评级', spark:(data.bhi_trend || []).map(function(x){return x.bhi;}), color:'#7c3aed'},
    {label:'所选范围负面', value:metricDisplay(metrics.negative_count, meta), hint:metrics.highest_risk===null ? '暂无风险样本' : '最高风险 ' + metrics.highest_risk, spark:trend.map(function(x){return x.negative;}), color:'#e11d48'},
    {label:'进行中预警', value:metricDisplay(metrics.active_incident_count, meta), hint:'以事件状态机为准', spark:[], color:'#ea580c'}
  ];
  var html = '';
  for(var i=0;i<stats.length;i++){
    var item = stats[i];
    html += '<div class="stat-card' + (item.label.indexOf('BHI')!==-1?' stat-card-featured':'') + '">';
    html += '<div class="stat-label">' + item.label + '</div><div class="stat-value">' + item.value + '</div>';
    html += '<div class="stat-trend flat">' + esc(item.hint) + '</div>';
    if(item.spark.length>1) html += '<div class="sparkline-wrap">' + buildSparkline(item.spark, item.color) + '</div>';
    html += '</div>';
  }
  $('#overviewStats').innerHTML = html;
}

function renderOverviewInsights(data){
  var labels = {'7d':'近 7 日情感趋势','30d':'近 30 日情感趋势','90d':'近 90 日情感趋势'};
  if($('#overviewTrendTitle')) $('#overviewTrendTitle').textContent = labels[data.range] || '所选范围情感趋势';
  var rows = (data.sentiment_trend || []).map(function(item){
    return {date:item.day.slice(5), pos:item.positive, neg:item.negative, neu:item.neutral};
  });
  if(!rows.length){
    $('#sentimentTrendChart').innerHTML = overviewStatePanel('empty', '所选时间范围内暂无可分析内容');
    return;
  }
  $('#sentimentTrendChart').innerHTML = buildMultiLineChart(rows, [
    {key:'pos', name:'正面', color:'#10b981'},
    {key:'neg', name:'负面', color:'#f43f5e'},
    {key:'neu', name:'中性', color:'#8b5cf6'}
  ]);
}

function renderTodoList(data){
  var todos = [];
  if(data.top_incident){
    todos.push({text:'处理 ' + data.top_incident.level + ' 事件：' + (data.top_incident.summary || data.top_incident.incident_id), action:"switchView('alerts')"});
  }
  if(data.pending_review_count>0){
    todos.push({text:'复核 ' + data.pending_review_count + ' 条待处理内容', action:"switchView('review')"});
  }
  if(data.latest_report){
    todos.push({text:'查看最新报告 ' + data.latest_report.run_id, action:"switchView('reports')"});
  }
  if(!todos.length){
    $('#todoList').innerHTML = overviewStatePanel('empty', '当前没有待处理事项');
    return;
  }
  $('#todoList').innerHTML = todos.map(function(item){
    if(item.disabled){
      return '<button class="todo-item todo-button is-disabled" disabled aria-disabled="true" title="功能开发中">⊘ ' + esc(item.text) + '</button>';
    }
    return '<button class="todo-item todo-button" onclick="' + item.action + '">☐ ' + esc(item.text) + '</button>';
  }).join('');
}

function apiIncidentStatusBadge(status){
  var labels = {pending_confirmation:'待确认', confirmed:'已确认', escalated:'已升级', resolved:'已解决', suppressed:'已抑制'};
  var cls = status==='pending_confirmation' ? 'badge-red' : status==='escalated' ? 'badge-amber' : 'badge-blue';
  return '<span class="badge ' + cls + '">' + esc(labels[status] || status) + '</span>';
}

function overviewFormatTime(value){
  if(!value) return '时间未知';
  return String(value).replace('T', ' ').slice(0, 16);
}

function renderBHIChart(){
  $('#bhiChart').innerHTML = buildLineChart(BHI_TREND, 'date', 'score', '#8b5cf6', 620, 180);
}
