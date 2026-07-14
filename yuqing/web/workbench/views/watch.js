'use strict';

function loadWatchConfig(force){
  if(state.watch.status==='loading') return;
  if(force){
    WorkbenchAPI.invalidate('/api/v1/watch');
    WorkbenchAPI.invalidate('/api/v1/keywords');
    WorkbenchAPI.invalidate('/api/v1/seeds');
  }
  state.watch.status = 'loading';
  state.watch.error = '';
  renderConfigView();
  Promise.all([
    WorkbenchAPI.get('/api/v1/watch', {entity_id:state.entityId}, {noCache:!!force}),
    WorkbenchAPI.get('/api/v1/keywords', {entity_id:state.entityId}, {noCache:!!force}),
    WorkbenchAPI.get('/api/v1/seeds', {entity_id:state.entityId}, {noCache:!!force})
  ]).then(function(payloads){
    state.watch.status = 'success';
    state.watch.data = payloads[0].data;
    state.watch.keywords = payloads[1].data;
    state.watch.seeds = payloads[2].data;
    state.watch.error = '';
    renderConfigView();
  }).catch(function(error){
    state.watch.status = 'error';
    state.watch.error = error;
    renderConfigView();
  });
}

function renderConfigView(){
  var save = $('#saveWatchButton');
  if(save){
    save.disabled = state.watch.mutating || state.watch.status!=='success';
    save.textContent = state.watch.mutating ? '正在保存…' : '保存监控配置';
  }
  if(state.watch.status==='idle'){
    loadWatchConfig(false);
    return;
  }
  if(state.watch.status==='loading' && !state.watch.data){
    $('#watchQuality').innerHTML = '';
    $('#entityConfigList').innerHTML = overviewStatePanel('loading', '正在读取监控配置…');
    $('#platformScheduleBody').innerHTML = '';
    $('#keywordList').innerHTML = overviewStatePanel('loading', '正在读取关键词…');
    $('#keywordSuggestionList').innerHTML = '';
    $('#seedSuggestionList').innerHTML = '';
    return;
  }
  if(state.watch.status==='error'){
    var error = state.watch.error || {};
    $('#watchQuality').innerHTML = overviewStatePanel('error', esc(error.message || '监控配置加载失败'), '<button class="btn btn-primary" onclick="loadWatchConfig(true)">重试</button>');
    $('#entityConfigList').innerHTML = '';
    $('#platformScheduleBody').innerHTML = '';
    $('#keywordList').innerHTML = '';
    $('#keywordSuggestionList').innerHTML = '';
    $('#seedSuggestionList').innerHTML = '';
    return;
  }
  $('#watchQuality').innerHTML = '';
  renderEntityConfig();
  renderPlatformSchedule();
  renderKeywordConfig();
  renderSeedSuggestions();
}

function watchEntityById(id){
  var entities = state.watch.data ? state.watch.data.entities || [] : [];
  return entities.find(function(item){return item.id===id;}) || null;
}

function watchField(title, entity, field, tone){
  var items = entity[field] || [];
  var chips = items.map(function(value, index){
    return '<span class="chip' + (tone?' ' + tone:'') + '">' + esc(value)
      + '<button class="chip-remove" data-entity="' + esc(entity.id) + '" data-field="' + field + '" data-index="' + index + '" onclick="removeWatchChip(this)">×</button></span>';
  }).join('');
  return '<div class="watch-field"><span class="watch-field-label">' + title + '</span><div class="chip-row">' + chips
    + '<button class="chip-add" data-entity="' + esc(entity.id) + '" data-field="' + field + '" onclick="addWatchChip(this)">+ 添加</button></div></div>';
}

function renderEntityConfig(){
  var entities = state.watch.data.entities || [];
  $('#entityConfigList').innerHTML = entities.map(function(entity){
    return '<section class="watch-entity"><div class="watch-entity-head"><b>' + esc(entity.name || entity.id) + '</b><span class="badge badge-outline">'
      + (entity.type==='competitor'?'竞品':'自有') + '</span></div>'
      + watchField('搜索别名', entity, 'aliases', '')
      + watchField('串味排除词', entity, 'must_not', 'chip-amber')
      + watchField('危机加权词', entity, 'crisis_boost', 'chip-red')
      + watchField('定向账号', entity, 'track_users', '') + '</section>';
  }).join('') || overviewStatePanel('empty', '尚未配置监控对象');
}

function addWatchChip(button){
  if(state.watch.mutating) return;
  var value = prompt('请输入词条：', '');
  if(!value || !value.trim()) return;
  var entity = watchEntityById(button.dataset.entity);
  var field = button.dataset.field;
  if(!entity || !Array.isArray(entity[field])) return;
  value = value.trim();
  if(entity[field].indexOf(value)===-1) entity[field].push(value);
  renderEntityConfig();
}

function removeWatchChip(button){
  if(state.watch.mutating) return;
  var entity = watchEntityById(button.dataset.entity);
  var field = button.dataset.field;
  var index = parseInt(button.dataset.index, 10);
  if(!entity || !Array.isArray(entity[field]) || index<0) return;
  if(field==='aliases' && entity[field].length<=1){ showToast('每个监控对象至少保留一个别名', 'red'); return; }
  entity[field].splice(index, 1);
  renderEntityConfig();
}

function renderPlatformSchedule(){
  var platforms = state.watch.data.platforms || [];
  $('#platformScheduleBody').innerHTML = platforms.map(function(item){
    return '<label class="platform-toggle"><input type="checkbox" data-platform="' + esc(item.id) + '" '
      + (item.enabled?'checked':'') + ' onchange="toggleWatchPlatform(this)"><span><b>' + esc(item.name) + '</b><small>' + esc(item.id) + '</small></span></label>';
  }).join('');
}

function toggleWatchPlatform(input){
  var item = (state.watch.data.platforms || []).find(function(row){return row.id===input.dataset.platform;});
  if(item) item.enabled = input.checked;
}

function saveWatchConfig(){
  if(state.watch.mutating || !state.watch.data) return;
  var platforms = (state.watch.data.platforms || []).filter(function(item){return item.enabled;}).map(function(item){return item.id;});
  if(!platforms.length){ showToast('至少启用一个采集平台', 'red'); return; }
  state.watch.mutating = true;
  renderConfigView();
  WorkbenchAPI.put('/api/v1/watch', {
    entity_id:state.entityId,
    platforms:platforms,
    entities:(state.watch.data.entities || []).map(function(entity){
      return {id:entity.id, aliases:entity.aliases, must_not:entity.must_not, crisis_boost:entity.crisis_boost, track_users:entity.track_users};
    })
  }).then(function(payload){
    state.watch.mutating = false;
    state.watch.data = payload.data;
    WorkbenchAPI.invalidate();
    state.context.status = 'idle';
    showToast('监控配置已保存，下轮采集生效', 'green');
    renderConfigView();
    renderContextControls();
  }).catch(function(error){
    state.watch.mutating = false;
    showToast(error.message || '监控配置保存失败', 'red');
    renderConfigView();
  });
}

function renderKeywordConfig(){
  var data = state.watch.keywords || {items:[], suggestions:[], tags:[]};
  var tagSelect = $('#keywordTag');
  if(tagSelect){
    var selected = tagSelect.value;
    tagSelect.innerHTML = (data.tags || []).map(function(tag){return '<option value="' + esc(tag.value) + '">' + esc(tag.label) + '</option>';}).join('');
    if(selected) tagSelect.value = selected;
  }
  $('#keywordList').innerHTML = (data.items || []).map(function(item){
    var label = (data.tags || []).find(function(tag){return tag.value===item.tag;});
    return '<div class="config-row"><span><b>' + esc(item.word) + '</b><small>' + esc(label ? label.label : item.tag) + ' · 权重 ' + item.weight + '</small></span>'
      + '<button class="icon-btn" title="删除关键词" data-word="' + esc(item.word) + '" data-tag="' + esc(item.tag) + '" onclick="deleteKeyword(this)">×</button></div>';
  }).join('') || overviewStatePanel('empty', '当前监控对象还没有关键词');
  $('#keywordSuggestionList').innerHTML = (data.suggestions || []).map(function(item){
    return '<div class="config-row"><span><b>' + esc(item.word) + '</b><small>' + esc(item.suggested_tag) + ' · ' + esc(item.reason || '系统建议') + '</small></span>'
      + '<div><button class="btn btn-sm" onclick="reviewKeywordSuggestion(' + item.id + ',\'approve\')">批准</button> '
      + '<button class="btn btn-sm" onclick="reviewKeywordSuggestion(' + item.id + ',\'reject\')">忽略</button></div></div>';
  }).join('') || overviewStatePanel('empty', '暂无待审核关键词建议');
}

function keywordMutation(body, successMessage){
  if(state.watch.mutating) return;
  state.watch.mutating = true;
  renderConfigView();
  body.entity_id = state.entityId;
  WorkbenchAPI.post('/api/v1/keywords', body).then(function(){
    state.watch.mutating = false;
    WorkbenchAPI.invalidate('/api/v1/keywords');
    showToast(successMessage, 'green');
    loadWatchConfig(true);
  }).catch(function(error){
    state.watch.mutating = false;
    showToast(error.message || '关键词操作失败', 'red');
    renderConfigView();
  });
}

function addKeyword(){
  var word = $('#keywordWord').value.trim();
  if(!word){ showToast('请输入关键词', 'red'); return; }
  keywordMutation({action:'add', word:word, tag:$('#keywordTag').value, weight:parseFloat($('#keywordWeight').value || '1')}, '关键词已添加');
  $('#keywordWord').value = '';
}

function deleteKeyword(button){
  keywordMutation({action:'delete', word:button.dataset.word, tag:button.dataset.tag}, '关键词已删除');
}

function reviewKeywordSuggestion(id, action){
  keywordMutation({action:action, id:id}, action==='approve'?'建议已加入关键词库':'建议已忽略');
}

function renderSeedSuggestions(){
  var items = state.watch.seeds ? state.watch.seeds.items || [] : [];
  $('#seedSuggestionList').innerHTML = items.map(function(item){
    return '<div class="config-row"><span><b>' + esc(item.word) + '</b><small>置信度 ' + Number(item.score || 0).toFixed(2) + ' · ' + esc(item.reason || '系统建议') + '</small></span>'
      + '<div><button class="btn btn-sm" onclick="seedAction(' + item.id + ',\'approve\')">加入别名</button> '
      + '<button class="btn btn-sm" onclick="seedAction(' + item.id + ',\'reject\')">忽略</button></div></div>';
  }).join('') || overviewStatePanel('empty', '暂无种子建议');
}

function seedAction(id, action){
  if(state.watch.mutating) return;
  state.watch.mutating = true;
  renderConfigView();
  WorkbenchAPI.post('/api/v1/seeds', {entity_id:state.entityId, action:action, id:id}).then(function(){
    state.watch.mutating = false;
    WorkbenchAPI.invalidate();
    state.context.status = 'idle';
    showToast(action==='approve'?'种子已加入监控别名':'种子建议已忽略', 'green');
    loadWatchConfig(true);
    renderContextControls();
  }).catch(function(error){
    state.watch.mutating = false;
    showToast(error.message || '种子操作失败', 'red');
    renderConfigView();
  });
}

function mineSeeds(){
  if(state.watch.mutating) return;
  state.watch.mutating = true;
  renderConfigView();
  WorkbenchAPI.post('/api/v1/seeds', {entity_id:state.entityId, action:'mine'}).then(function(payload){
    state.watch.mutating = false;
    var queued = payload.data.result.queued || {};
    showToast('建议生成完成：种子 ' + (queued.seed || 0) + '，关键词 ' + (queued.feature || 0), 'green');
    loadWatchConfig(true);
  }).catch(function(error){
    state.watch.mutating = false;
    showToast(error.message || '建议生成失败', 'red');
    renderConfigView();
  });
}
