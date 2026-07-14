'use strict';

var state = {
  activeView:'overview',
  reviewSelected:{},
  activeReviewId:null,
  analysisPeriod:'week',
  activeReportId:null,
  reportsTab:'weekly',
  kbFilter:{category:null, subcategory:null, search:''},
  kbExpandedId:null,
  sidebarOpen:false,
  entityId:'',
  range:'7d',
  context:{status:'idle', data:null, error:''},
  overview:{status:'idle', data:null, meta:null, error:''},
  collection:{status:'idle', data:null, meta:null, error:'', mutating:false, pollTimer:null},
  review:{
    status:'idle', data:null, meta:null, error:'', items:[], nextCursor:null,
    mutating:false, filters:{status:'pending', platform:'', confidence:'all', limit:20}
  },
  analysis:{status:'idle', data:null, meta:null, error:'', range:'7d'},
  incidents:{status:'idle', data:null, meta:null, error:'', active:null, mutating:false},
  backlog:{status:'idle', data:null, meta:null, error:'', range:'30d'},
  reports:{status:'idle', data:null, meta:null, error:'', active:null, detailStatus:'idle', mutating:false},
  watch:{status:'idle', data:null, keywords:null, seeds:null, error:'', mutating:false}
};
