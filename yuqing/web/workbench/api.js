'use strict';

(function(global){
  var DEFAULT_TIMEOUT = 12000;
  var CACHE_TTL = 30000;
  var cache = new Map();

  function APIError(message, options){
    options = options || {};
    this.name = 'APIError';
    this.message = message || '请求失败';
    this.status = options.status || 0;
    this.code = options.code || 'REQUEST_FAILED';
    this.payload = options.payload || null;
  }
  APIError.prototype = Object.create(Error.prototype);
  APIError.prototype.constructor = APIError;

  function queryString(params){
    var search = new URLSearchParams();
    Object.keys(params || {}).forEach(function(key){
      var value = params[key];
      if(value!==undefined && value!==null && value!=='') search.set(key, value);
    });
    var text = search.toString();
    return text ? '?' + text : '';
  }

  async function request(path, options){
    options = options || {};
    var method = (options.method || 'GET').toUpperCase();
    var url = path + queryString(options.params);
    var cacheKey = method + ':' + url;
    var cached = cache.get(cacheKey);
    if(method==='GET' && !options.noCache && cached && Date.now()-cached.at<CACHE_TTL){
      return cached.value;
    }

    var controller = new AbortController();
    var timer = setTimeout(function(){ controller.abort(); }, options.timeout || DEFAULT_TIMEOUT);
    var headers = Object.assign({'Accept':'application/json'}, options.headers || {});
    var body;
    if(options.body!==undefined){
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.body);
    }
    try{
      var response = await fetch(url, {
        method:method,
        headers:headers,
        body:body,
        signal:controller.signal,
        credentials:'same-origin'
      });
      var payload = null;
      try{ payload = await response.json(); }catch(ignore){}
      if(!response.ok || !payload || payload.success!==true){
        var error = payload && payload.error ? payload.error : {};
        throw new APIError(error.message || ('请求失败（HTTP ' + response.status + '）'), {
          status:response.status,
          code:error.code || (response.status===401 ? 'UNAUTHORIZED' : response.status===403 ? 'FORBIDDEN' : 'REQUEST_FAILED'),
          payload:payload
        });
      }
      if(method==='GET') cache.set(cacheKey, {at:Date.now(), value:payload});
      return payload;
    }catch(error){
      if(error instanceof APIError) throw error;
      if(error && error.name==='AbortError'){
        throw new APIError('请求超时，请稍后重试', {code:'TIMEOUT'});
      }
      throw new APIError('网络连接失败，请检查服务状态', {code:'NETWORK_ERROR'});
    }finally{
      clearTimeout(timer);
    }
  }

  function invalidate(prefix){
    Array.from(cache.keys()).forEach(function(key){
      if(!prefix || key.indexOf(prefix)!==-1) cache.delete(key);
    });
  }

  global.WorkbenchAPI = {
    APIError:APIError,
    get:function(path, params, options){
      return request(path, Object.assign({}, options || {}, {method:'GET', params:params || {}}));
    },
    post:function(path, body, options){
      return request(path, Object.assign({}, options || {}, {method:'POST', body:body || {}}));
    },
    put:function(path, body, options){
      return request(path, Object.assign({}, options || {}, {method:'PUT', body:body || {}}));
    },
    invalidate:invalidate,
    isAuthError:function(error){ return error && (error.status===401 || error.status===403); }
  };
})(window);
