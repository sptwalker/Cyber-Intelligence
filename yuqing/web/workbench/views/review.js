'use strict';

var REVIEW_REASON_LABELS = {
  low_confidence:'低置信', irony:'反讽', high_risk:'高风险', model_disagreement:'模型分歧'
};

function reviewFilterValues(){
  return {
    status:$('#filterStatus') ? $('#filterStatus').value : state.review.filters.status,
    platform:$('#filterPlatform') ? $('#filterPlatform').value : state.review.filters.platform,
    confidence:$('#filterConf') ? $('#filterConf').value : state.review.filters.confidence,
    limit:state.review.filters.limit || 20
  };
}

function renderReviewFilters(){
  var filters = state.review.filters;
  var platforms = state.review.data ? state.review.data.platforms : Object.keys(window.PLATFORM_LABELS || {});
  var select = $('#filterPlatform');
  select.innerHTML = '<option value="">全部平台</option>' + platforms.map(function(platform){
    return '<option value="' + esc(platform) + '">' + esc((window.PLATFORM_LABELS || {})[platform] || platform) + '</option>';
  }).join('');
  $('#filterStatus').value = filters.status || 'pending';
  select.value = filters.platform || '';
  $('#filterConf').value = filters.confidence || 'all';
}

function applyReviewFilters(){
  state.review.filters = reviewFilterValues();
  state.reviewSelected = {};
  state.activeReviewId = null;
  loadReviews(true, null, false);
}

function loadReviews(force, cursor, append){
  if(state.review.status==='loading' && !append) return;
  if(force) WorkbenchAPI.invalidate('/api/v1/reviews');
  state.review.status = 'loading';
  state.review.error = '';
  renderReviewList();
  var filters = state.review.filters;
  WorkbenchAPI.get('/api/v1/reviews', {
    entity_id:state.entityId,
    status:filters.status,
    platform:filters.platform,
    confidence:filters.confidence,
    limit:filters.limit,
    cursor:cursor || ''
  }, {noCache:true}).then(function(payload){
    var items = append ? state.review.items.concat(payload.data.items || []) : (payload.data.items || []);
    state.review.status = 'success';
    state.review.data = payload.data;
    state.review.meta = payload.meta;
    state.review.error = '';
    state.review.items = items;
    state.review.nextCursor = payload.data.next_cursor;
    renderReviewFilters();
    renderReviewList();
    renderChuanweiPanel();
    renderNav();
  }).catch(function(error){
    state.review.status = 'error';
    state.review.error = error;
    renderReviewList();
    renderChuanweiPanel();
  });
}

function loadMoreReviews(){
  if(!state.review.nextCursor || state.review.status==='loading') return;
  loadReviews(false, state.review.nextCursor, true);
}

function filteredReviewItems(){
  return state.review.items || [];
}

function reviewReasonBadges(item){
  return (item.queue_reasons || []).map(function(reason){
    return '<span class="badge badge-outline">' + esc(REVIEW_REASON_LABELS[reason] || reason) + '</span>';
  }).join('');
}

function reviewSafeHref(value){
  if(!value) return '';
  try{
    var parsed = new URL(value, window.location.origin);
    return parsed.protocol==='http:' || parsed.protocol==='https:' ? parsed.href : '';
  }catch(ignore){ return ''; }
}

function renderChuanweiPanel(){
  var items = filteredReviewItems();
  var counts = {low_confidence:0, irony:0, high_risk:0, model_disagreement:0};
  items.forEach(function(item){
    (item.queue_reasons || []).forEach(function(reason){ if(counts[reason]!==undefined) counts[reason]++; });
  });
  var summary = Object.keys(counts).filter(function(key){return counts[key]>0;}).map(function(key){
    return REVIEW_REASON_LABELS[key] + ' ' + counts[key] + ' 条';
  }).join(' · ');
  var quality = state.review.meta ? qualityNotice(state.review.meta) : '';
  $('#chuanweiPanel').innerHTML = quality
    + '<div class="panel-title">复核队列说明 <small>结论写入 SQLite，刷新或重启后仍保留</small></div>'
    + '<div class="hint-bar">' + esc(summary || '当前页暂无待解释的入队原因')
    + '。标记“串味”只记录质检结论，关键词库联动将在监控配置接口接通后开放。</div>';
}

function renderReviewList(){
  if(state.review.status==='idle'){
    state.review.filters = reviewFilterValues();
    loadReviews(false, null, false);
    return;
  }
  if(state.review.status==='loading' && !state.review.items.length){
    $('#filterResultCount').textContent = '';
    $('#reviewCardList').innerHTML = overviewStatePanel('loading', '正在读取真实复核队列…');
    updateSelectedCount();
    return;
  }
  if(state.review.status==='error'){
    var error = state.review.error || {};
    var action = WorkbenchAPI.isAuthError(error)
      ? '<a class="btn btn-primary" href="/auth/login?next=%2Fv2">重新登录</a>'
      : '<button class="btn btn-primary" onclick="loadReviews(true,null,false)">重试</button>';
    $('#filterResultCount').textContent = '';
    $('#reviewCardList').innerHTML = overviewStatePanel('error', esc(error.message || '复核队列加载失败'), action);
    updateSelectedCount();
    return;
  }

  var items = filteredReviewItems();
  var total = state.review.data ? state.review.data.total : items.length;
  $('#filterResultCount').textContent = '已加载 ' + items.length + ' / 共 ' + total + ' 条';
  if(!items.length){
    $('#reviewCardList').innerHTML = overviewStatePanel('empty', '当前筛选条件下没有复核内容');
    updateSelectedCount();
    return;
  }
  var disabled = state.review.mutating ? ' disabled' : '';
  var html = items.map(function(item){
    var id = item.doc_id;
    var cls = 'review-card' + (id===state.activeReviewId ? ' active-focus' : '');
    if(item.status==='approved') cls += ' status-approved';
    if(item.status==='rejected') cls += ' status-rejected';
    var source = reviewSafeHref(item.url);
    var sourceLink = source
      ? '<a class="btn btn-sm" href="' + esc(source) + '" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">原帖</a>'
      : '';
    return '<div class="' + cls + '" id="rc-' + esc(id) + '" onclick="setActiveReviewCard(\'' + id + '\')">'
      + '<input type="checkbox" onclick="event.stopPropagation();toggleReviewSelect(\'' + id + '\')" '
      + (state.reviewSelected[id]?'checked':'') + disabled + '>'
      + '<div class="rc-body"><div class="rc-tags">' + polarityBadge(item.machine_polarity)
      + statusBadge(item.status) + reviewReasonBadges(item)
      + '<span class="muted" style="font-size:11px;">' + esc((window.PLATFORM_LABELS || {})[item.platform] || item.platform)
      + ' · ' + esc(item.author || '匿名') + ' · 风险 ' + item.risk.toFixed(1) + '</span></div>'
      + '<div class="rc-text">' + esc(item.text) + '</div>'
      + '<div class="rc-conf"><span class="muted" style="font-size:11px;">机器置信度</span>'
      + '<div class="progress-track"><div class="progress-fill" style="width:' + Math.round(item.confidence*100)
      + '%;background:' + confColor(item.confidence) + ';"></div></div><span style="font-size:11px;font-weight:700;">'
      + item.confidence.toFixed(2) + '</span></div>'
      + (item.verdict ? '<div class="hint-bar">最近结论：' + esc(item.verdict_label) + ' · ' + esc(item.actor || '未知操作人')
        + (item.note ? ' · ' + esc(item.note) : '') + '</div>' : '')
      + '<div class="rc-actions"><button class="btn btn-green btn-sm"' + disabled
      + ' onclick="event.stopPropagation();approveReview(\'' + id + '\')">✓ 通过</button>'
      + '<button class="btn btn-danger btn-sm"' + disabled
      + ' onclick="event.stopPropagation();rejectReview(\'' + id + '\')">✕ 拒绝</button>'
      + '<button class="btn btn-sm"' + disabled + ' onclick="event.stopPropagation();labelReview(\'' + id + '\',\'irony\')">标反讽</button>'
      + '<button class="btn btn-sm"' + disabled + ' onclick="event.stopPropagation();labelReview(\'' + id + '\',\'spam\')">标水军</button>'
      + '<button class="btn btn-sm"' + disabled + ' onclick="event.stopPropagation();labelReview(\'' + id + '\',\'irrelevant\')">标串味</button>'
      + sourceLink + '</div></div></div>';
  }).join('');
  if(state.review.nextCursor){
    html += '<button class="btn btn-block" style="margin-top:10px;" ' + (state.review.status==='loading'?'disabled':'')
      + ' onclick="loadMoreReviews()">加载更多</button>';
  }
  $('#reviewCardList').innerHTML = html;
  updateSelectedCount();
}

function setActiveReviewCard(id){
  state.activeReviewId = id;
  renderReviewList();
}

function toggleReviewSelect(id){
  state.reviewSelected[id] = !state.reviewSelected[id];
  updateSelectedCount();
}

function updateSelectedCount(){
  var count = Object.keys(state.reviewSelected).filter(function(id){return state.reviewSelected[id];}).length;
  $('#selectedCount').textContent = count;
}

function toggleSelectAll(checked){
  filteredReviewItems().forEach(function(item){ state.reviewSelected[item.doc_id] = checked; });
  renderReviewList();
}

function reviewMutationFinished(message){
  state.review.mutating = false;
  state.reviewSelected = {};
  state.overview.status = 'idle';
  state.overview.data = null;
  WorkbenchAPI.invalidate('/api/v1/reviews');
  WorkbenchAPI.invalidate('/api/v1/overview');
  showToast(message, 'green');
  loadReviews(true, null, false);
}

function submitSingleReview(id, verdict, note){
  if(state.review.mutating) return;
  state.review.mutating = true;
  renderReviewList();
  WorkbenchAPI.post('/api/v1/reviews/' + encodeURIComponent(id), {
    entity_id:state.entityId, verdict:verdict, note:note || ''
  }).then(function(payload){
    reviewMutationFinished('已保存：' + payload.data.review.verdict_label);
  }).catch(function(error){
    state.review.mutating = false;
    showToast(error.message || '复核保存失败', 'red');
    renderReviewList();
  });
}

function submitBatchReview(verdict, note){
  if(state.review.mutating) return;
  var ids = Object.keys(state.reviewSelected).filter(function(id){return state.reviewSelected[id];});
  if(!ids.length){ showToast('请先勾选要批量处理的内容', 'red'); return; }
  state.review.mutating = true;
  renderReviewList();
  WorkbenchAPI.post('/api/v1/reviews/batch', {
    entity_id:state.entityId,
    items:ids.map(function(id){return {doc_id:id, verdict:verdict, note:note || ''};})
  }).then(function(payload){
    var data = payload.data;
    reviewMutationFinished('批量复核完成：成功 ' + data.succeeded + ' 条，失败 ' + data.failed + ' 条');
  }).catch(function(error){
    state.review.mutating = false;
    showToast(error.message || '批量复核失败', 'red');
    renderReviewList();
  });
}

function approveReview(id){ submitSingleReview(id, 'ok', ''); }
function rejectReview(id){
  var note = prompt('请输入拒绝原因（可留空）：', '');
  if(note!==null) submitSingleReview(id, 'reject', note);
}
function labelReview(id, verdict){ submitSingleReview(id, verdict, ''); }
function flagChuanwei(id){ submitSingleReview(id, 'irrelevant', ''); }
function batchApprove(){ submitBatchReview('ok', ''); }
function batchReject(){
  var note = prompt('请输入批量拒绝原因（可留空）：', '');
  if(note!==null) submitBatchReview('reject', note);
}
function refreshReviewBadges(){ renderNav(); }

function handleReviewShortcut(event){
  if(state.activeView!=='review' || !state.activeReviewId || state.review.mutating) return;
  var tag = (event.target.tagName || '').toLowerCase();
  if(tag==='input' || tag==='textarea' || tag==='select') return;
  var verdict = {
    '1':'correct_positive', '2':'correct_neutral', '3':'correct_negative',
    'i':'irony', 's':'spam', 'r':'irrelevant'
  }[event.key.toLowerCase()];
  if(verdict){ event.preventDefault(); submitSingleReview(state.activeReviewId, verdict, ''); }
  else if(event.key==='Enter'){ event.preventDefault(); approveReview(state.activeReviewId); }
  else if(event.key==='Escape'){ state.activeReviewId = null; renderReviewList(); }
}
