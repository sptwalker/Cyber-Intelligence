'use strict';

var state = {
  activeView:'overview',
  reviewSelected:{},
  activeReviewId:null,
  analysisPeriod:'week',
  activeReportId:'RPT-2026W29',
  reportsTab:'weekly',
  kbFilter:{category:null, subcategory:null, search:''},
  kbExpandedId:null,
  sidebarOpen:false,
  entityId:'',
  range:'7d',
  overview:{status:'idle', data:null, meta:null, error:''},
  collection:{status:'idle', data:null, meta:null, error:'', mutating:false, pollTimer:null},
  analysis:{status:'idle', data:null, meta:null, error:'', range:'7d'},
  incidents:{status:'idle', data:null, meta:null, error:'', active:null, mutating:false},
  backlog:{status:'idle', data:null, meta:null, error:'', range:'30d'}
};
