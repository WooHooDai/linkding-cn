/* 客户端 bookmarklet：从浏览器端捕获页面标题和描述。
   通过 include 内联到 href='javascript:...' 中。
   约束：必须单行（换行破坏 href）、双引号用 HTML 实体（裸双引号截断 href）。
   逻辑：读取 title/description。服务器下发 buffer-size 预算时（生产 uWSGI），
   按预算截断：URL 不截断、优先截断 description、description 至少保留 200 字符、
   剩余空间给 title。uWSGI 的 buffer-size 限制请求行+请求头整体，需预留头部
   空间（HEADER_RESERVE=2048），否则截断后的 URL 仍会超限。开发环境无该限制
   （预算为 null）时完整保留元数据。 */
void(function(){var u=window.location.href,t=document.querySelector('title')?.textContent||document.querySelector('meta[property=&quot;og:title&quot;]')?.getAttribute('content')||'',d=document.querySelector('meta[name=&quot;description&quot;]')?.getAttribute('content')||document.querySelector('meta[property=&quot;og:description&quot;]')?.getAttribute('content')||'';var cut=function(s,b){if(b<=0)return'';for(var i=s.length;i>0;i--){var e=encodeURIComponent(s.slice(0,i));if(e.length<=b)return e}return''};var eu=encodeURIComponent(u);var limit={{ bookmarklet_budget|default_if_none:"null" }};if(limit===null){var et=encodeURIComponent(t);var ed=encodeURIComponent(d)}else{var base=('{{ application_url }}?url='+eu+'&title=&description=&from_client=1&auto_close').length;var budget=limit-base-2048;var ed=cut(d,Math.min(200,budget));var et=cut(t,budget-ed.length)}window.open('{{ application_url }}?url='+eu+'&title='+et+'&description='+ed+'&from_client=1&auto_close')})();
