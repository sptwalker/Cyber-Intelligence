'use strict';

function loadReports(force){
  if(state.reports.status==='loading') return;
  if(force) WorkbenchAPI.invalidate('/api/v1/reports');
  state.reports.status = 'loading';
  state.reports.error = '';
  renderReportsTab();
  WorkbenchAPI.get('/api/v1/reports', {entity_id:state.entityId}, {noCache:!!force}).then(function(payload){
    state.reports.status = 'success';
    state.reports.data = payload.data;
    state.reports.meta = payload.meta;
    state.reports.error = '';
    var items = payload.data.items || [];
    if(!state.activeReportId || !items.some(function(item){return item.run_id===state.activeReportId;})){
      state.activeReportId = items.length ? items[0].run_id : null;
      state.reports.active = null;
      state.reports.detailStatus = 'idle';
    }
    renderReportsTab();
    if(state.activeReportId && state.reports.detailStatus==='idle') loadReportDetail(state.activeReportId, false);
  }).catch(function(error){
    state.reports.status = 'error';
    state.reports.error = error;
    renderReportsTab();
  });
}

function loadReportDetail(runId, force){
  if(!runId || state.reports.detailStatus==='loading') return;
  var path = '/api/v1/reports/' + encodeURIComponent(runId);
  if(force) WorkbenchAPI.invalidate(path);
  state.reports.detailStatus = 'loading';
  state.reports.active = null;
  renderReportPreview();
  WorkbenchAPI.get(path, {entity_id:state.entityId}, {noCache:!!force}).then(function(payload){
    state.reports.detailStatus = 'success';
    state.reports.active = payload.data.report;
    renderReportHistory();
    renderReportPreview();
  }).catch(function(error){
    state.reports.detailStatus = 'error';
    state.reports.error = error;
    renderReportPreview();
  });
}

function renderReportsTab(){
  var button = $('#generateReportButton');
  if(button){
    button.disabled = state.reports.mutating;
    button.textContent = state.reports.mutating ? '正在生成…' : '生成当前报告';
  }
  if(state.reports.status==='idle'){
    loadReports(false);
    return;
  }
  if(state.reports.status==='loading' && !state.reports.data){
    $('#reportsQuality').innerHTML = '';
    $('#reportHistoryList').innerHTML = overviewStatePanel('loading', '正在读取报告历史…');
    $('#reportPreviewBody').innerHTML = overviewStatePanel('loading', '正在准备报告预览…');
    return;
  }
  if(state.reports.status==='error'){
    var error = state.reports.error || {};
    var action = WorkbenchAPI.isAuthError(error)
      ? '<a class="btn btn-primary" href="/auth/login?next=%2Fv2">重新登录</a>'
      : '<button class="btn btn-primary" onclick="loadReports(true)">重试</button>';
    $('#reportsQuality').innerHTML = '';
    $('#reportHistoryList').innerHTML = overviewStatePanel('error', esc(error.message || '报告列表加载失败'), action);
    $('#reportPreviewBody').innerHTML = '';
    return;
  }
  $('#reportsQuality').innerHTML = qualityNotice(state.reports.meta);
  renderReportHistory();
  renderReportPreview();
}

function renderReportHistory(){
  var items = state.reports.data ? state.reports.data.items || [] : [];
  if(!items.length){
    $('#reportHistoryList').innerHTML = overviewStatePanel('empty', '尚未生成报告，点击右上角开始生成');
    return;
  }
  $('#reportHistoryList').innerHTML = items.map(function(item){
    var active = item.run_id===state.activeReportId;
    return '<button class="report-list-item' + (active?' active':'') + '" data-run-id="' + esc(item.run_id) + '" onclick="selectReport(this.dataset.runId)">'
      + '<b>' + esc(item.title || item.run_id) + '</b>'
      + '<span>' + esc(overviewFormatTime(item.created_at)) + ' · ' + item.citation_count + ' 个来源</span></button>';
  }).join('');
}

function selectReport(runId){
  if(runId===state.activeReportId && state.reports.active) return;
  state.activeReportId = runId;
  state.reports.active = null;
  state.reports.detailStatus = 'idle';
  renderReportHistory();
  loadReportDetail(runId, false);
}

function renderReportPreview(){
  if(!state.activeReportId){
    $('#reportPreviewTitle').textContent = '报告预览';
    $('#reportPreviewBody').innerHTML = overviewStatePanel('empty', '选择或生成一份报告');
    return;
  }
  if(state.reports.detailStatus==='loading'){
    $('#reportPreviewBody').innerHTML = overviewStatePanel('loading', '正在读取报告正文…');
    return;
  }
  if(state.reports.detailStatus==='error'){
    var message = state.reports.error && state.reports.error.message || '报告详情加载失败';
    $('#reportPreviewBody').innerHTML = overviewStatePanel('error', esc(message), '<button class="btn btn-primary" onclick="loadReportDetail(state.activeReportId,true)">重试</button>');
    return;
  }
  var report = state.reports.active;
  if(!report){
    $('#reportPreviewBody').innerHTML = overviewStatePanel('empty', '请选择一份报告');
    return;
  }
  $('#reportPreviewTitle').textContent = report.title || report.run_id;
  var citations = report.citations || [];
  var sourceButtons = citations.length
    ? '<div class="report-sources"><div class="panel-title">来源溯源</div>' + citations.map(function(docId){
        return '<button class="btn btn-sm" onclick="showSourceDoc(\'' + docId + '\')">来源 ' + docId + '</button>';
      }).join('') + '</div>'
    : '<div class="hint-bar">本报告没有文档引用。</div>';
  $('#reportPreviewBody').innerHTML = '<div class="report-meta">生成时间 ' + esc(overviewFormatTime(report.created_at))
    + ' · run_id ' + esc(report.run_id) + '</div><pre class="report-markdown">' + esc(report.markdown || '') + '</pre>' + sourceButtons;
}

function generateReport(){
  if(state.reports.mutating) return;
  state.reports.mutating = true;
  renderReportsTab();
  WorkbenchAPI.post('/api/v1/reports/generate', {entity_id:state.entityId}).then(function(payload){
    state.reports.mutating = false;
    state.activeReportId = payload.data.report.run_id;
    state.reports.active = payload.data.report;
    state.reports.detailStatus = 'success';
    WorkbenchAPI.invalidate('/api/v1/reports');
    WorkbenchAPI.invalidate('/api/v1/overview');
    state.overview.status = 'idle';
    showToast('报告已生成并保存', 'green');
    loadReports(true);
  }).catch(function(error){
    state.reports.mutating = false;
    showToast(error.message || '报告生成失败', 'red');
    renderReportsTab();
  });
}

function reportSafeHref(value){
  if(!value) return '';
  try{
    var parsed = new URL(value, window.location.origin);
    return parsed.protocol==='http:' || parsed.protocol==='https:' ? parsed.href : '';
  }catch(ignore){ return ''; }
}

function showSourceDoc(docId){
  openDrawer('来源 ' + docId, overviewStatePanel('loading', '正在读取来源文档…'));
  WorkbenchAPI.get('/api/v1/docs/' + encodeURIComponent(docId), {entity_id:state.entityId}, {noCache:true}).then(function(payload){
    var doc = payload.data.document;
    var source = reportSafeHref(doc.url);
    var link = source ? '<a class="btn btn-sm" href="' + esc(source) + '" target="_blank" rel="noopener noreferrer">打开原帖</a>' : '';
    var html = '<div class="source-document-meta">' + esc((window.PLATFORM_LABELS || {})[doc.platform] || doc.platform)
      + ' · ' + esc(doc.author || '匿名') + ' · ' + esc(overviewFormatTime(doc.published_at || doc.fetched_at)) + '</div>'
      + '<div class="source-document-text">' + esc(doc.text || '') + '</div>'
      + '<div class="hint-bar">机器结论：' + esc(doc.polarity || '未知') + ' · 风险 ' + (doc.risk===null || doc.risk===undefined ? '—' : doc.risk) + '</div>'
      + link;
    $('#drawerBody').innerHTML = html;
  }).catch(function(error){
    $('#drawerBody').innerHTML = overviewStatePanel('error', esc(error.message || '来源文档加载失败'));
  });
}
