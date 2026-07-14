'use strict';

var VIEWS = [
  {id:'overview', group:'工作台', icon:'◆', label:'总览工作台'},
  {id:'collect', group:'工作台', icon:'⇩', label:'采集接入'},
  {id:'review', group:'工作台', icon:'☑', label:'数据质检工作台', badgeFn:'pendingReviewCount'},
  {id:'analysis', group:'分析洞察', icon:'◔', label:'情绪分析'},
  {id:'alerts', group:'分析洞察', icon:'▲', label:'预警中心'},
  {id:'backlog', group:'分析洞察', icon:'☷', label:'诉求管理'},
  {id:'reports', group:'资产沉淀', icon:'▤', label:'报告中心'},
  {id:'config', group:'系统管理', icon:'⌁', label:'监控配置'}
];

var VIEW_META = {
  overview:{title:'总览 Dashboard', sub:'全平台舆情概况 · 实时更新'},
  collect:{title:'采集接入', sub:'平台健康、登录状态与跑批控制'},
  review:{title:'数据质检工作台', sub:'人工复核 · 结论持久化'},
  analysis:{title:'情绪分析', sub:'ABSA、话题与 BHI 趋势'},
  alerts:{title:'预警中心', sub:'P0 / P1 分级处置看板'},
  backlog:{title:'诉求管理', sub:'用户诉求聚合与 CSV 导出'},
  reports:{title:'报告中心', sub:'确定性生成 · 查看 · 来源溯源'},
  config:{title:'监控配置', sub:'监控对象 · 平台 · 关键词 · 种子建议'}
};

function $(selector){ return document.querySelector(selector); }
function $all(selector){ return Array.prototype.slice.call(document.querySelectorAll(selector)); }

function esc(value){
  if(value===null || value===undefined) return '';
  return String(value)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function showToast(message, kind){
  var container = $('#toastContainer');
  if(!container) return;
  var element = document.createElement('div');
  element.className = 'toast' + (kind==='green'?' toast-green':kind==='red'?' toast-red':'');
  element.textContent = message;
  container.appendChild(element);
  setTimeout(function(){ if(element.parentNode) element.parentNode.removeChild(element); }, 3000);
}

function polarityBadge(polarity){
  if(polarity==='pos') return '<span class="badge badge-green">正面</span>';
  if(polarity==='neg') return '<span class="badge badge-red">负面</span>';
  return '<span class="badge badge-gray">中性</span>';
}

function levelBadge(level){
  return level==='P0'
    ? '<span class="badge badge-red">P0 危机</span>'
    : '<span class="badge badge-amber">P1 跟进</span>';
}

function statusBadge(status){
  var map = {pending:['待复核','badge-blue'], approved:['已通过','badge-green'], rejected:['已拒绝','badge-red']};
  var value = map[status] || ['未知','badge-gray'];
  return '<span class="badge ' + value[1] + '">' + value[0] + '</span>';
}

function healthDot(status){
  var cls = status==='ok' ? 'ok' : status==='suspect' ? 'suspect' : 'fail';
  return '<span class="health-dot ' + cls + '"></span>';
}

function confColor(confidence){
  if(confidence>0.8) return 'var(--green)';
  if(confidence>0.5) return 'var(--amber)';
  return 'var(--red)';
}

function buildLineChart(data, keyX, keyY, color, width, height){
  width = width || 560;
  height = height || 160;
  if(!data || !data.length) return '';
  var pad = 28;
  var values = data.map(function(item){return Number(item[keyY]) || 0;});
  var min = Math.min.apply(null, values);
  var max = Math.max.apply(null, values);
  if(min===max){ min -= 1; max += 1; }
  var step = (width-pad*2) / Math.max(1, data.length-1);
  var points = data.map(function(item,index){
    return {
      x:data.length===1 ? width/2 : pad+index*step,
      y:height-pad-((Number(item[keyY]) || 0)-min)/(max-min)*(height-pad*2),
      label:item[keyX], value:item[keyY]
    };
  });
  var path = points.map(function(point,index){return (index===0?'M':'L') + point.x.toFixed(1) + ',' + point.y.toFixed(1);}).join(' ');
  var svg = '<svg width="100%" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
  svg += '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="2.5"></path>';
  points.forEach(function(point){
    svg += '<circle cx="' + point.x.toFixed(1) + '" cy="' + point.y.toFixed(1) + '" r="3.2" fill="' + color + '"></circle>';
    svg += '<text x="' + point.x.toFixed(1) + '" y="' + (height-6) + '" font-size="10" fill="#667085" text-anchor="middle">' + esc(point.label) + '</text>';
    svg += '<text x="' + point.x.toFixed(1) + '" y="' + (point.y-8).toFixed(1) + '" font-size="10" fill="' + color + '" text-anchor="middle">' + esc(point.value) + '</text>';
  });
  return svg + '</svg>';
}

function buildSparkline(values, color, width, height){
  width = width || 96;
  height = height || 28;
  if(!values || values.length<2) return '';
  var min = Math.min.apply(null, values);
  var max = Math.max.apply(null, values);
  if(min===max){ min -= 1; max += 1; }
  var step = width/(values.length-1);
  var points = values.map(function(value,index){
    return {x:index*step,y:height-((value-min)/(max-min))*height};
  });
  var path = points.map(function(point,index){return (index===0?'M':'L')+point.x.toFixed(1)+','+point.y.toFixed(1);}).join(' ');
  var last = points[points.length-1];
  return '<svg width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '">'
    + '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="2"></path>'
    + '<circle cx="' + last.x.toFixed(1) + '" cy="' + last.y.toFixed(1) + '" r="2.4" fill="' + color + '"></circle></svg>';
}

function buildMultiLineChart(data, series, width, height){
  width = width || 620;
  height = height || 220;
  if(!data || !data.length) return '';
  var padLeft=38, padRight=16, padTop=16, padBottom=30;
  var values=[];
  series.forEach(function(item){ data.forEach(function(row){values.push(Number(row[item.key]) || 0);}); });
  var max=Math.max.apply(null,values);
  max=Math.ceil(max/10)*10 || 10;
  var plotWidth=width-padLeft-padRight, plotHeight=height-padTop-padBottom;
  var step=plotWidth/Math.max(1,data.length-1);
  var svg='<svg width="100%" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
  for(var grid=0;grid<=4;grid++){
    var y=padTop+plotHeight*grid/4;
    var label=Math.round(max*(1-grid/4));
    svg += '<line x1="' + padLeft + '" y1="' + y.toFixed(1) + '" x2="' + (width-padRight) + '" y2="' + y.toFixed(1) + '" stroke="#e5e7eb"></line>';
    svg += '<text x="' + (padLeft-7) + '" y="' + (y+3).toFixed(1) + '" font-size="10" fill="#98a2b3" text-anchor="end">' + label + '</text>';
  }
  series.forEach(function(item){
    var points=data.map(function(row,index){
      return {
        x:data.length===1 ? padLeft+plotWidth/2 : padLeft+index*step,
        y:padTop+plotHeight-(Number(row[item.key]) || 0)/max*plotHeight
      };
    });
    var path=points.map(function(point,index){return (index===0?'M':'L')+point.x.toFixed(1)+','+point.y.toFixed(1);}).join(' ');
    svg += '<path d="' + path + '" fill="none" stroke="' + item.color + '" stroke-width="2.4"></path>';
    points.forEach(function(point){svg += '<circle cx="' + point.x.toFixed(1) + '" cy="' + point.y.toFixed(1) + '" r="3" fill="' + item.color + '"></circle>';});
  });
  data.forEach(function(row,index){
    var x=data.length===1 ? padLeft+plotWidth/2 : padLeft+index*step;
    svg += '<text x="' + x.toFixed(1) + '" y="' + (height-8) + '" font-size="10" fill="#667085" text-anchor="middle">' + esc(row.date) + '</text>';
  });
  return svg + '</svg>';
}

function buildRadarChart(dimensions, series, width, height){
  width=width || 340;
  height=height || 340;
  if(!dimensions || !dimensions.length) return '';
  var cx=width/2, cy=height/2, radius=Math.min(width,height)/2-46, count=dimensions.length;
  var angle=function(index){return Math.PI*2*index/count-Math.PI/2;};
  var svg='<svg width="100%" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
  [0.25,0.5,0.75,1].forEach(function(scale){
    var ring=dimensions.map(function(_,index){var a=angle(index);return (cx+Math.cos(a)*radius*scale).toFixed(1)+','+(cy+Math.sin(a)*radius*scale).toFixed(1);});
    svg += '<polygon points="' + ring.join(' ') + '" fill="none" stroke="#e5e7eb"></polygon>';
  });
  var points=[];
  dimensions.forEach(function(label,index){
    var a=angle(index), x=cx+Math.cos(a)*radius, y=cy+Math.sin(a)*radius;
    svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) + '" stroke="#e5e7eb"></line>';
    svg += '<text x="' + (cx+Math.cos(a)*(radius+22)).toFixed(1) + '" y="' + (cy+Math.sin(a)*(radius+22)).toFixed(1) + '" font-size="11" fill="#667085" text-anchor="middle" dominant-baseline="middle">' + esc(label) + '</text>';
    var value=Number(series[index]);
    if(!Number.isFinite(value)) value=0;
    var normalized=Math.max(0,Math.min(1,(value+1)/2));
    points.push((cx+Math.cos(a)*radius*normalized).toFixed(1)+','+(cy+Math.sin(a)*radius*normalized).toFixed(1));
  });
  svg += '<polygon points="' + points.join(' ') + '" fill="#8b5cf6" fill-opacity="0.2" stroke="#7c3aed" stroke-width="2"></polygon>';
  return svg + '</svg>';
}

function pendingReviewCount(){
  if(state.review.data && state.review.filters.status==='pending') return state.review.data.total || 0;
  return state.overview.data ? state.overview.data.pending_review_count || 0 : 0;
}

function renderNav(){
  var groups=[];
  var grouped={};
  VIEWS.forEach(function(view){
    if(!grouped[view.group]){ grouped[view.group]=[]; groups.push(view.group); }
    grouped[view.group].push(view);
  });
  var html='';
  groups.forEach(function(group){
    html += '<div class="side-group"><div class="side-group-title">' + esc(group) + '</div>';
    grouped[group].forEach(function(view){
      var count=view.badgeFn && window[view.badgeFn] ? window[view.badgeFn]() : 0;
      html += '<button class="side-item' + (view.id===state.activeView?' active':'') + '" data-view="' + view.id + '" onclick="switchView(\'' + view.id + '\')">'
        + '<span class="side-icon">' + view.icon + '</span>' + esc(view.label)
        + (count>0?'<span class="side-badge">'+count+'</span>':'') + '</button>';
    });
    html += '</div>';
  });
  html += '<div class="sidebar-footer"><b>Cyber-Intelligence</b>真实数据工作台</div>';
  $('#sidebar').innerHTML=html;
}

function switchView(id){
  if(!VIEW_META[id]) id='overview';
  if(state.activeView==='collect' && id!=='collect' && window.stopCollectionPolling) stopCollectionPolling();
  state.activeView=id;
  $all('.view').forEach(function(view){view.classList.remove('active');});
  var target=$('#view-'+id);
  if(target) target.classList.add('active');
  $('#viewTitle').textContent=VIEW_META[id].title;
  $('#viewSubtitle').textContent=VIEW_META[id].sub;
  renderNav();
  if(window.innerWidth<=960){ state.sidebarOpen=false; $('#sidebar').classList.remove('open'); }
  if(id==='overview') renderOverview();
  else if(id==='collect') renderCollectTable();
  else if(id==='review'){ renderReviewFilters(); applyReviewFilters(); renderChuanweiPanel(); }
  else if(id==='analysis') renderAnalysisView();
  else if(id==='alerts'){ renderAlertsKanban(); renderCooldownList(); }
  else if(id==='backlog') renderBacklogTable();
  else if(id==='reports') renderReportsTab();
  else if(id==='config') renderConfigView();
}

function toggleSidebar(){
  state.sidebarOpen=!state.sidebarOpen;
  $('#sidebar').classList.toggle('open',state.sidebarOpen);
}

function loadContext(){
  if(state.context.status==='loading') return;
  state.context.status='loading';
  renderContextControls();
  WorkbenchAPI.get('/api/v1/context',{entity_id:state.entityId},{noCache:true}).then(function(payload){
    state.context={status:'success',data:payload.data,error:''};
    state.entityId=payload.data.entity.id;
    renderContextControls();
    renderCurrentUser();
  }).catch(function(error){
    state.context={status:'error',data:null,error:error};
    renderContextControls();
  });
}

function renderContextControls(){
  var root=$('#contextControls');
  if(!root) return;
  if(state.context.status==='idle'){ loadContext(); return; }
  if(state.context.status==='loading'){ root.innerHTML='<span class="muted">正在加载筛选项…</span>'; return; }
  if(state.context.status==='error'){ root.innerHTML='<button class="btn btn-sm" onclick="loadContext()">筛选项加载失败，重试</button>'; return; }
  var context=state.context.data;
  root.innerHTML='<label class="context-field"><span>监控对象</span><select id="globalEntitySelect" onchange="changeEntity(this.value)">'
    +(context.entities || []).map(function(item){return '<option value="'+esc(item.id)+'"'+(item.id===state.entityId?' selected':'')+'>'+esc(item.name)+(item.type==='competitor'?'（竞品）':'')+'</option>';}).join('')
    +'</select></label><label class="context-field"><span>分析范围</span><select id="globalRangeSelect" onchange="changeGlobalRange(this.value)">'
    +(context.ranges || []).map(function(item){return '<option value="'+esc(item.value)+'"'+(item.value===state.range?' selected':'')+'>'+esc(item.label)+'</option>';}).join('')+'</select></label>';
}

function renderCurrentUser(){
  var root=$('#currentUserChip');
  if(!root || !state.context.data) return;
  var user=state.context.data.user || {};
  var name=user.name || user.open_id || '当前用户';
  root.innerHTML='<span class="avatar-dot">'+esc(name.slice(0,1))+'</span>'+esc(name);
}

function invalidateEntityData(){
  WorkbenchAPI.invalidate();
  state.overview={status:'idle',data:null,meta:null,error:''};
  state.collection={status:'idle',data:null,meta:null,error:'',mutating:false,pollTimer:null};
  state.review.status='idle'; state.review.data=null; state.review.items=[]; state.review.nextCursor=null;
  state.analysis.status='idle'; state.analysis.data=null;
  state.incidents.status='idle'; state.incidents.data=null; state.incidents.active=null;
  state.backlog.status='idle'; state.backlog.data=null;
  state.reports.status='idle'; state.reports.data=null; state.reports.active=null; state.reports.detailStatus='idle'; state.activeReportId=null;
  state.watch.status='idle'; state.watch.data=null; state.watch.keywords=null; state.watch.seeds=null;
}

function changeEntity(entityId){
  if(!entityId || entityId===state.entityId) return;
  if(state.activeView==='collect' && window.stopCollectionPolling) stopCollectionPolling();
  state.entityId=entityId;
  invalidateEntityData();
  switchView(state.activeView);
}

function changeGlobalRange(range){
  if(!range || range===state.range) return;
  state.range=range;
  state.analysis.range=range;
  state.analysisPeriod=range==='30d'?'month':range==='90d'?'quarter':'week';
  state.backlog.range=range;
  WorkbenchAPI.invalidate('/api/v1/overview');
  WorkbenchAPI.invalidate('/api/v1/analysis');
  WorkbenchAPI.invalidate('/api/v1/backlog');
  state.overview.status='idle'; state.overview.data=null;
  state.analysis.status='idle'; state.analysis.data=null;
  state.backlog.status='idle'; state.backlog.data=null;
  switchView(state.activeView);
}

function renderHealthStrip(){
  var platforms=state.collection.data ? state.collection.data.platforms : state.overview.data ? state.overview.data.collection_health : null;
  if(!platforms){ $('#healthStrip').innerHTML='<span class="health-pill"><span class="health-dot suspect"></span>数据状态加载中</span>'; return; }
  $('#healthStrip').innerHTML=platforms.map(function(item){
    return '<span class="health-pill">'+healthDot(item.health)+esc((window.PLATFORM_LABELS || {})[item.platform] || item.platform)+'</span>';
  }).join('');
}

function openDrawer(title,bodyHtml){
  $('#drawerTitle').textContent=title;
  $('#drawerBody').innerHTML=bodyHtml;
  $('#drawerOverlay').classList.remove('hidden');
}

function closeDrawer(){
  $('#drawerOverlay').classList.add('hidden');
  state.currentDrawerAlertId=null;
}

var SEARCH_INDEX=[];
function buildSearchIndex(){
  var items=[];
  var incidents=state.incidents.data ? state.incidents.data.items || [] : [];
  incidents.forEach(function(item){items.push({type:'预警',title:item.summary || item.incident_id,meta:item.level+' · '+item.status,view:'alerts',id:item.incident_id});});
  var backlog=state.backlog.data ? state.backlog.data.items || [] : [];
  backlog.forEach(function(item){items.push({type:'诉求',title:item.topic,meta:item.kind+' · '+item.count+' 条',view:'backlog'});});
  var reports=state.reports.data ? state.reports.data.items || [] : [];
  reports.forEach(function(item){items.push({type:'报告',title:item.title || item.run_id,meta:overviewFormatTime(item.created_at),view:'reports',id:item.run_id});});
  return items;
}

function openSearch(){
  SEARCH_INDEX=buildSearchIndex();
  $('#searchOverlay').classList.remove('hidden');
  $('#searchInput').value='';
  $('#searchResults').innerHTML='<div class="empty-hint">输入至少 2 个字开始搜索</div>';
  setTimeout(function(){$('#searchInput').focus();},30);
}

function closeSearch(){ $('#searchOverlay').classList.add('hidden'); }

function handleSearchInput(){
  var keyword=$('#searchInput').value.trim();
  if(keyword.length<2){ $('#searchResults').innerHTML='<div class="empty-hint">输入至少 2 个字开始搜索</div>'; return; }
  var results=SEARCH_INDEX.filter(function(item){return item.title.indexOf(keyword)!==-1;}).slice(0,30);
  state.currentSearchResults=results;
  if(!results.length){ $('#searchResults').innerHTML='<div class="empty-hint">未找到相关结果</div>'; return; }
  $('#searchResults').innerHTML=results.map(function(item,index){return '<button class="search-result-item" onclick="runSearchResult('+index+')"><span class="badge badge-outline">'+esc(item.type)+'</span> '+esc(item.title)+'<span class="sr-meta">'+esc(item.meta)+'</span></button>';}).join('');
}

function runSearchResult(index){
  var result=state.currentSearchResults && state.currentSearchResults[index];
  if(!result) return;
  closeSearch();
  switchView(result.view);
  if(result.view==='alerts' && result.id) openAlertDrawer(result.id);
  if(result.view==='reports' && result.id) selectReport(result.id);
}

function confirmSearch(){
  if(state.currentSearchResults && state.currentSearchResults.length) runSearchResult(0);
}

document.addEventListener('keydown',function(event){
  if(event.key==='Escape'){
    if(!$('#drawerOverlay').classList.contains('hidden')){closeDrawer();return;}
    if(!$('#searchOverlay').classList.contains('hidden')){closeSearch();return;}
  }
  if((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==='k'){
    event.preventDefault(); openSearch(); return;
  }
  if((event.ctrlKey || event.metaKey) && /^[0-9]$/.test(event.key)){
    var index=event.key==='0'?9:parseInt(event.key,10)-1;
    if(VIEWS[index]){event.preventDefault();switchView(VIEWS[index].id);}
    return;
  }
  if(window.handleReviewShortcut) handleReviewShortcut(event);
});

function init(){
  renderNav();
  renderContextControls();
  renderHealthStrip();
  switchView('overview');
}

document.addEventListener('DOMContentLoaded',init);
