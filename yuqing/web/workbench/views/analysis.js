'use strict';

function analysisRangeForPeriod(period){
  return period==='month' ? '30d' : period==='quarter' ? '90d' : '7d';
}

function renderAnalysisPeriodPills(){
  var periods = [{id:'week', label:'近 7 天'}, {id:'month', label:'近 30 天'}, {id:'quarter', label:'近 90 天'}];
  $('#analysisPeriodPills').innerHTML = periods.map(function(item){
    return '<button class="tab-pill' + (item.id===state.analysisPeriod?' active':'')
      + '" onclick="setAnalysisPeriod(\'' + item.id + '\')">' + item.label + '</button>';
  }).join('');
}

function setAnalysisPeriod(period){
  state.analysisPeriod = period;
  state.analysis.range = analysisRangeForPeriod(period);
  state.analysis.status = 'idle';
  renderAnalysisView();
}

function loadAnalysis(force){
  if(state.analysis.status==='loading') return;
  if(force) WorkbenchAPI.invalidate('/api/v1/analysis');
  state.analysis.status = 'loading';
  state.analysis.error = '';
  renderAnalysisView();
  WorkbenchAPI.get('/api/v1/analysis', {
    entity_id:state.entityId,
    range:state.analysis.range
  }, {noCache:!!force}).then(function(payload){
    state.analysis = {
      status:'success', data:payload.data, meta:payload.meta, error:'', range:payload.data.range
    };
    renderAnalysisView();
  }).catch(function(error){
    state.analysis.status = 'error';
    state.analysis.error = error;
    renderAnalysisView();
  });
}

function renderAnalysisView(){
  renderAnalysisPeriodPills();
  if(state.analysis.status==='idle'){
    state.analysis.range = analysisRangeForPeriod(state.analysisPeriod);
    loadAnalysis(false);
    return;
  }
  if(state.analysis.status==='loading' && !state.analysis.data){
    $('#radarChart').innerHTML = overviewStatePanel('loading', '正在计算 ABSA…');
    $('#wordcloud').innerHTML = overviewStatePanel('loading', '正在汇总话题…');
    $('#absaTableBody').innerHTML = '<tr><td colspan="5">加载中…</td></tr>';
    $('#bhiChart').innerHTML = overviewStatePanel('loading', '正在计算 BHI 趋势…');
    return;
  }
  if(state.analysis.status==='error'){
    var error = state.analysis.error || {};
    var panel = overviewStatePanel('error', esc(error.message || '分析数据加载失败'), '<button class="btn btn-primary" onclick="loadAnalysis(true)">重试</button>');
    $('#radarChart').innerHTML = panel;
    $('#wordcloud').innerHTML = '';
    $('#absaTableBody').innerHTML = '';
    $('#bhiChart').innerHTML = '';
    return;
  }
  var data = state.analysis.data;
  var aspects = data.aspects || [];
  if(aspects.length){
    $('#radarChart').innerHTML = qualityNotice(state.analysis.meta)
      + buildRadarChart(aspects.map(function(item){return item.aspect;}), aspects.map(function(item){return item.net_sentiment;}));
  }else{
    $('#radarChart').innerHTML = qualityNotice(state.analysis.meta) + overviewStatePanel('empty', '暂无方面级分析数据');
  }

  var topics = data.topics || [];
  var sample = '<div class="hint-bar">' + esc(data.sample.note) + '</div>';
  if(topics.length){
    var max = topics[0].count || 1;
    sample += topics.map(function(item){
      var size = 12 + (item.count/max)*10;
      return '<span style="font-size:' + size + 'px;">' + esc(item.topic) + ' <small style="opacity:.6;font-size:11px;">' + item.count + '</small></span>';
    }).join('');
  }else{
    sample += overviewStatePanel('empty', '暂无负面话题聚合');
  }
  $('#wordcloud').innerHTML = sample;

  $('#absaTableBody').innerHTML = aspects.length ? aspects.map(function(item){
    var net = item.net_sentiment;
    return '<tr><td><b>' + esc(item.aspect) + '</b></td><td>' + item.pos + '</td><td>' + item.neg + '</td>'
      + '<td style="color:' + (net>=0?'var(--green)':'var(--red)') + ';font-weight:700;">' + (net>=0?'+':'') + net.toFixed(2) + '</td>'
      + '<td>' + (item.neg_ratio*100).toFixed(1) + '%</td></tr>';
  }).join('') : '<tr><td colspan="5"><div class="empty-hint">暂无 ABSA 样本</div></td></tr>';

  var bhi = (data.bhi_trend || []).map(function(item){ return {date:item.day.slice(5), score:item.bhi}; });
  $('#bhiChart').innerHTML = bhi.length>1
    ? buildLineChart(bhi, 'date', 'score', '#8b5cf6', 620, 180)
    : overviewStatePanel('empty', '所选时间范围内暂无 BHI 趋势');
}
