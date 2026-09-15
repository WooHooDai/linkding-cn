import Sortable from 'sortablejs';
(function () {
  'use strict';

  function gettext(s) { return typeof window.gettext === 'function' ? window.gettext(s) : s; }
  function esc(s) { return s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') : ''; }

  var csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  var csrfToken = csrf ? csrf.value : '';
  var urls = window.__ld_urls || {};
  var TAB_KEY = "ld:site-adapters-tab";
var MODE = (function () { try { return localStorage.getItem(TAB_KEY) || "subscriptions"; } catch (e) { return "subscriptions"; } })();

  function apiPost(url, data) {
    var fd = new FormData();
    for (var k in data) {
      if (!data.hasOwnProperty(k)) continue;
      var v = data[k];
      if (Array.isArray(v)) {
        for (var i = 0; i < v.length; i++) { fd.append(k, v[i]); }
      } else {
        fd.append(k, v);
      }
    }
    return fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body: fd }).then(function (r) { return r.json(); });
  }
  function apiGet(url) {
    return fetch(url, { headers: { 'X-CSRFToken': csrfToken } }).then(function (r) { return r.json(); });
  }
  // 使用 main 分支统一 toast 系统
  function toast(msg, tone) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, { tone: tone || 'info' });
    }
  }

  // ===== Mode =====
  function switchMode(mode) {
    MODE = mode;
    try { localStorage.setItem(TAB_KEY, mode); } catch (e) {}
    document.querySelectorAll('[name="view-mode"]').forEach(function (r) { r.checked = r.value === mode; });
    document.getElementById('view-subscriptions').hidden = mode !== 'subscriptions';
    document.getElementById('view-domains').hidden = mode !== 'domains';
    var credView = document.getElementById('view-credentials');
    if (credView) credView.hidden = mode !== 'credentials';
    if (mode === 'subscriptions') loadSubscriptions();
    if (mode === 'domains') restoreSearchResults();
    // credentials handled by adapters.js
  }
  document.querySelectorAll('[name="view-mode"]').forEach(function (r) {
    r.addEventListener('change', function () { switchMode(this.value); });
  });

  // ===== Domain list =====
  var domainEntries = [];
  var domainResultsVisible = false;
  var expandedDomains = {};  // rowId → true
  var configCache = {};  // domain_key → config object
  var _domainViewMode = 'structured';  // 'structured' or 'raw'
  var SEARCH_KEY = 'ld:site-adapters-domain-search';
  var RESULTS_KEY = 'ld:site-adapters-domain-results';

  function getStoredSearch() {
    try { return localStorage.getItem(SEARCH_KEY) || ''; } catch (e) { return ''; }
  }
  function setStoredSearch(val) {
    try { localStorage.setItem(SEARCH_KEY, val || ''); } catch (e) {}
  }
  function getStoredResults() {
    try { var r = localStorage.getItem(RESULTS_KEY); return r ? JSON.parse(r) : null; } catch (e) { return null; }
  }
  function setStoredResults(data) {
    try { localStorage.setItem(RESULTS_KEY, JSON.stringify(data)); } catch (e) {}
  }
  function clearStoredResults() {
    try { localStorage.removeItem(RESULTS_KEY); } catch (e) {}
  }

  function restoreSearchResults() {
    var stored = getStoredResults();
    var searchTerm = getStoredSearch();
    if (stored && stored.length > 0) {
      domainEntries = stored.map(function (item) {
        var rowId = (item.domain_key || '') + '::' + (item.adapter || '');
        item.rowId = rowId;
        item.sections = item.sections || [];
        if (configCache[item.domain_key]) item.config = configCache[item.domain_key];
        if (!item.config) item.config = null;
        return item;
      });
      var searchInput = document.getElementById('domain-search');
      if (searchInput) searchInput.value = searchTerm;
      domainResultsVisible = true;
      var clearBtn = document.getElementById('btn-clear-domain-search');
      if (clearBtn && searchTerm) clearBtn.removeAttribute('hidden');
      else if (clearBtn) clearBtn.setAttribute('hidden', '');
      renderDomainResults();
    }
  }

  function performSearch(query) {
    var searchInput = document.getElementById('domain-search');
    query = query || (searchInput ? searchInput.value.trim() : '');
    setStoredSearch(query);

    // Build URL with optional query
    var url = urls.domainsAll;
    if (query) url += '?q=' + encodeURIComponent(query);

    apiGet(url).then(function (d) {
      domainEntries = (d.domain_files || []).map(function (item) {
        var rowId = (item.domain_key || '') + '::' + (item.adapter || '');
        return {
          rowId: rowId,
          domain_key: item.domain_key || '',
          adapter: item.adapter || '',
          is_alias: item.is_alias || false,
          target: item.target || '',
          requires_cookie: item.requires_cookie || false,
          has_cookie: item.has_cookie || false,
          disabled: item.disabled || false,
          shadowed: item.shadowed || false,
          shadowed_by: item.shadowed_by || '',
          sections: item.sections || [],
          config: configCache[item.domain_key] || null
        };
      });
      domainResultsVisible = true;
      var searchInput2 = document.getElementById('domain-search');
      var clearBtn2b = document.getElementById('btn-clear-domain-search');
      if (clearBtn2b) clearBtn2b.removeAttribute('hidden');
      setStoredResults(domainEntries.map(function (e) {
        // Don't store config in localStorage (could be large)
        var o = {};
        for (var k in e) { if (k !== 'config') o[k] = e[k]; }
        o.sections = e.sections || [];
        return o;
      }));
      renderDomainResults();
    }).catch(function () {
      toast(gettext('Search failed'), 'error');
    });
  }

  function renderDomainResults() {
    var wrap = document.getElementById('domain-results-wrap');
    var tbody = document.getElementById('domain-table-body');
    var viewToggle = document.getElementById('domain-view-toggle');
    var clearBtn = document.getElementById('btn-clear-domain-search');
    if (!wrap || !tbody) return;

    if (domainResultsVisible) {
      wrap.removeAttribute('hidden');
    } else {
      wrap.setAttribute('hidden', '');
    }
    if (!domainResultsVisible) {
      if (viewToggle) viewToggle.setAttribute('hidden', '');
      return;
    }



    // Update clear button visibility
    var searchInput = document.getElementById('domain-search');
    if (clearBtn) { if (domainResultsVisible) clearBtn.removeAttribute('hidden'); else clearBtn.setAttribute('hidden', ''); }

    var h = '';
    domainEntries.forEach(function (d) {
      var tags = buildDomainTags(d);
      var rowCls = 'wa-domain-row' + (d.shadowed ? ' wa-domain-row-shadowed' : '') + (d.disabled && !d.shadowed ? ' wa-domain-disabled' : '') + (expandedDomains[d.rowId] ? ' wa-domain-row-expanded' : '');
      h += '<tr class="' + rowCls + '" data-row-id="' + esc(d.rowId) + '" data-adapter="' + esc(d.adapter) + '"' + (d.shadowed_by ? ' title="' + esc(gettext('Overridden by')) + ' ' + esc(d.shadowed_by) + '"' : '') + '>';
      // Column 1: Toggle
      h += '<td class="wa-col-toggle"><label class="form-switch"><input type="checkbox" ' + (d.disabled ? '' : 'checked') + ' data-local-domain="' + esc(d.domain_key) + '"><i class="form-icon"></i></label></td>';
      // Column 2: Domain + tags
      h += '<td class="wa-col-domain">';
      h += '<div class="wa-domain-main"><code>' + esc(d.domain_key) + '</code>';
      if (d.is_alias && d.target) h += ' <span class="wa-alias-badge">&rarr; ' + esc(d.target) + '</span>';
      h += '</div>';
      if (tags.length > 0) {
        h += '<div class="wa-domain-tags">';
        tags.forEach(function (tag) {
          h += '<span class="wa-tag wa-tag-' + esc(tag.cls) + '"' + (tag.title ? ' title="' + esc(tag.title) + '"' : '') + '>' + esc(tag.label) + '</span>';
        });
        h += '</div>';
      }
      h += '</td>';
      // Column 3: Source
      h += '<td class="wa-col-source"><span class="wa-source-badge">' + esc(d.adapter) + '</span></td>';
      h += '</tr>';
      if (expandedDomains[d.rowId]) {
        h += renderDetailRow(d);
      }
    });
    tbody.innerHTML = h;

    // Row click → expand / collapse
    tbody.querySelectorAll('.wa-domain-row').forEach(function (tr) {
      tr.addEventListener('click', function (e) {
        if (e.target.closest('input, label')) return;
        toggleDomainDetail(this.dataset.rowId);
      });
    });
    // Toggle switches
    tbody.querySelectorAll('input[data-local-domain]').forEach(function (cb) {
      cb.addEventListener('change', function () {
        var domainKey = this.dataset.localDomain;
        var enabled = this.checked;
        apiPost(urls.localDomainToggle, { domain: domainKey, enabled: enabled ? '1' : '0' })
          .then(function (r) {
            if (r.error) { toast(r.error, 'error'); return; }
            var entry = domainEntries.find(function (e) { return e.domain_key === domainKey; });
            if (entry) { entry.disabled = !enabled; }
            setStoredResults(domainEntries.map(function (e) {
              var o = {};
              for (var k in e) {
                if (k !== 'config' && k !== 'sections') o[k] = e[k];
              }
              o.sections = e.sections || [];
              return o;
            }));
            renderDomainResults();
          });
      });
    });
  }

  function buildDomainTags(d) {
    var tags = [];
    // Section tags (from metadata or config)
    var sections = d.sections || [];
    if (sections.length === 0 && d.config) {
      sections = Object.keys(d.config).filter(function (k) { return d.config[k] !== null && typeof d.config[k] === 'object'; });
    }
    sections.forEach(function (s) {
      tags.push({ label: s, cls: 'section' });
    });
    // Shadowed tag
    if (d.shadowed) {
      tags.push({ label: gettext('overridden'), cls: 'shadowed', title: gettext('Overridden by') + ' ' + (d.shadowed_by || '') });
    }
    // Cookie needed — show per-section if section_cookie_needs is available
    var sectionCookieNeeds = d.section_cookie_needs || {};
    var cookieNeededKeys = Object.keys(sectionCookieNeeds);
    if (cookieNeededKeys.length > 0 && !d.has_cookie) {
      if (cookieNeededKeys.length === 1 && cookieNeededKeys[0] === '') {
        tags.push({ label: gettext('cookie needed'), cls: 'warn' });
      } else {
        cookieNeededKeys.forEach(function (sec) {
          if (sec) {
            tags.push({ label: sec + ': ' + gettext('cookie needed'), cls: 'warn' });
          } else {
            tags.push({ label: gettext('cookie needed'), cls: 'warn' });
          }
        });
      }
    }
    return tags;
  }

  function renderDetailRow(d) {
    if (d.config) {
      return buildDetailHtml(d);
    }
    return '<tr class="wa-domain-detail" data-row-id="' + esc(d.rowId) + '"><td colspan="3"><div class="wa-detail-loading">' + gettext('Loading...') + '</div></td></tr>';
  }

  function buildDetailHtml(d) {
    var config = d.config || {};
    var h = '<tr class="wa-domain-detail" data-row-id="' + esc(d.rowId) + '"><td colspan="3"><div class="wa-detail-inner">';
    h += '<div class="wa-detail-header">';
    h += '<span class="wa-detail-title">' + esc(d.domain_key) + '</span>';
    h += '<span class="wa-detail-source">' + gettext('Source') + ': ' + esc(d.adapter || '—') + '</span>';
    h += '</div><div class="wa-detail-body">';

    if (_domainViewMode === 'raw') {
      h += '<pre class="wa-detail-code">' + esc(JSON.stringify(config, null, 2)) + '</pre>';
    } else {
      var sections = Object.keys(config);
      if (sections.length === 0) {
        h += '<pre class="wa-detail-code">' + esc(JSON.stringify(config, null, 2)) + '</pre>';
      } else {
        sections.forEach(function (section) {
          var val = config[section];
          var isObj = val !== null && typeof val === 'object';
          var keyCount = isObj ? Object.keys(val).length : 0;
          h += '<details class="wa-detail-section" open>';
          h += '<summary class="wa-detail-section-heading"><span>' + esc(section) + '</span>';
          if (isObj && keyCount > 0) h += ' <span class="wa-detail-section-count">' + keyCount + ' keys</span>';
          h += '</summary>';
          h += '<pre class="wa-detail-code">' + esc(JSON.stringify(val, null, 2)) + '</pre>';
          h += '</details>';
        });
      }
    }

    h += '</div></div></td></tr>';
    return h;
  }

  function toggleDomainDetail(rowId) {
    if (expandedDomains[rowId]) {
      delete expandedDomains[rowId];
      renderDomainResults();
      return;
    }
    var entry = domainEntries.find(function (e) { return e.rowId === rowId; });
    if (!entry) return;
    var domainKey = entry.domain_key;

    expandedDomains[rowId] = true;
    // Render immediately with loading state
    renderDomainResults();

    // If config is already cached, re-render with detail
    if (entry.config) {
      renderDomainResults();
      return;
    }

    // Fetch config from server
    var readUrl = urls.domainRead + '?domain_key=' + encodeURIComponent(domainKey);
    if (entry.adapter) readUrl += '&adapter=' + encodeURIComponent(entry.adapter);
    apiGet(readUrl).then(function (d) {
      var cfg = d.config || {};
      configCache[domainKey] = cfg;
      // Update config for ALL entries with this domain_key (including shadowed)
      domainEntries.forEach(function (e) {
        if (e.domain_key === domainKey) e.config = cfg;
      });
      if (expandedDomains[rowId]) {
        renderDomainResults();
      }
    }).catch(function () {
      domainEntries.forEach(function (e) {
        if (e.domain_key === domainKey) e.config = {};
      });
      if (expandedDomains[rowId]) {
        renderDomainResults();
      }
    });
  }

  // ===== Event bindings =====
  // Search button
  document.getElementById('btn-search-domains').addEventListener('click', function () {
    var q = document.getElementById('domain-search').value.trim();
    if (!q) {
      // Show confirmation modal
      var overlay = document.getElementById('domain-confirm-overlay');
      var msg = document.getElementById('domain-confirm-msg');
      msg.textContent = gettext('This will list all domains from enabled subscriptions. This may consume significant data. Continue?');
      overlay.hidden = false;
    } else {
      performSearch(q);
    }
  });

  // Enter key in search input
  document.getElementById('domain-search').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') document.getElementById('btn-search-domains').click();
  });

  // Clear button
  var searchInputEl2 = document.getElementById('domain-search');
  var clearBtn2 = document.getElementById('btn-clear-domain-search');
  searchInputEl2.addEventListener('input', function () {
    if (clearBtn2) clearBtn2.hidden = !this.value;
  });
  if (clearBtn2) {
    clearBtn2.addEventListener('click', function () {
      searchInputEl2.value = '';
      setStoredSearch('');
      clearStoredResults();
      domainEntries = [];
      domainResultsVisible = false;
      expandedDomains = {};
      renderDomainResults();
      clearBtn2.hidden = true;
      searchInputEl2.focus();
    });
  }

  // View mode toggle
  document.querySelectorAll('[name="domain-view-mode"]').forEach(function (r) {
    r.addEventListener('change', function () {
      _domainViewMode = this.value;
      if (Object.keys(expandedDomains).length > 0) renderDomainResults();
    });
  });

  // Confirm modal
  document.getElementById('btn-domain-confirm-ok').addEventListener('click', function () {
    document.getElementById('domain-confirm-overlay').hidden = true;
    performSearch('');
  });
  document.getElementById('btn-domain-confirm-cancel').addEventListener('click', function () {
    document.getElementById('domain-confirm-overlay').hidden = true;
  });
  document.getElementById('domain-confirm-overlay').addEventListener('mousedown', function (e) {
    if (e.target === this) this.hidden = true;
  });

  // ===== Subscriptions =====
  var subData = [];
  var subSortable = null;

  function formatTimeAgo(ts) {
    if (!ts) return gettext('never');
    var diff = (Date.now() / 1000) - ts;
    if (diff < 0) return gettext('just now');
    if (diff < 60) return Math.floor(diff) + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function formatNextFetch(lastFetch, interval) {
    if (!lastFetch) return gettext('now');
    var next = lastFetch + (interval || 86400);
    var diff = next - (Date.now() / 1000);
    if (diff <= 0) return gettext('now');
    if (diff < 3600) return Math.ceil(diff / 60) + 'm';
    if (diff < 86400) return Math.ceil(diff / 3600) + 'h';
    return Math.ceil(diff / 86400) + 'd';
  }

  function loadSubscriptions() {
    apiGet(urls.subscriptionManage).then(function (d) {
      subData = d.adapters || [];
      renderSubscriptions();
    }).catch(function (e) { console.error('loadSubscriptions:', e); });
  }

  function renderSubscriptions() {
    var list = document.getElementById('subscription-list'); if (!list) return;
    if (!subData.length) {
      list.innerHTML = '<p class="text-gray" style="padding:16px">' + gettext('No adapters.') + '</p>';
      return;
    }
    var html = '';
    subData.forEach(function (s, i) {
      var label = s.display_name || s.name || s.source_name || (s.id ? s.id : '');
      var remote = s.source && (s.source.startsWith('https://') || s.source.startsWith('http://'));
      var enabled = s.enabled !== false;
      var version = s.version || '';
      var lastFetch = s.last_fetch || 0;
      var interval = s.update_interval || 86400;
      var isDefaults = s.id === 'defaults' && s.name === 'defaults';
      var rowCls = 'wa-sub-item' + (enabled ? '' : ' wa-sub-disabled');
      html += '<div class="' + rowCls + '" data-index="' + i + '" draggable="true">';
      html += '<div class="wa-sub-drag" title="' + gettext('Drag to reorder') + '"><svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-grip"></use></svg></div>';
      html += '<label class="form-switch wa-sub-toggle">';
      html += '<input type="checkbox" ' + (enabled ? 'checked' : '') + ' data-sub-toggle="' + i + '">';
      html += '<i class="form-icon"></i>';
      html += '</label>';
      html += '<div class="wa-sub-body">';
      html += '<div class="wa-sub-view">';
      html += '<span class="wa-sub-name">' + esc(label) + '</span>';
      if (s.cache_missing) html += ' <span class="wa-badge wa-badge-warn" title="' + gettext('Cache file missing. Click Update to re-download.') + '">' + gettext('missing') + '</span>';
      html += '<span class="wa-sub-meta">';
      html += '<span>' + (s.domain_count || 0) + ' ' + gettext('domains') + '</span>';
      html += '<span class="wa-sub-sep">·</span>';
      html += '<span>' + formatTimeAgo(lastFetch) + '</span>';
      if (version) { html += '<span class="wa-sub-sep">·</span><span>v' + esc(version) + '</span>'; }
      html += '</span>';
      html += '</div>';
      html += '</div>';
      if (!enabled) html += '<span class="wa-badge wa-badge-warn wa-sub-disabled-badge">' + gettext('disabled') + '</span>';
      html += '<div class="wa-sub-actions">';
      if (remote) html += '<button class="btn btn-sm js-sub-update" data-index="' + i + '" title="' + gettext('Update') + '" aria-label="' + gettext('Update') + '"><svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-refresh"></use></svg></button>';
      if (!isDefaults) html += '<button class="btn btn-sm js-sub-edit" data-index="' + i + '" title="' + gettext('Edit') + '" aria-label="' + gettext('Edit') + '"><svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-edit"></use></svg></button>';
      if (!isDefaults) html += '<button class="btn btn-sm btn-error js-sub-delete" data-index="' + i + '" title="' + gettext('Delete') + '" aria-label="' + gettext('Delete') + '" ld-confirm-question="' + gettext('Delete') + ' ' + esc(label) + '?" ld-confirm-danger=""><svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-delete"></use></svg></button>';
      html += '<button class="btn btn-sm js-sub-expand" data-index="' + i + '" title="' + gettext('Details') + '" aria-label="' + gettext('Details') + '"><svg width="16" height="16" aria-hidden="true" class="wa-sub-chevron"><use href="#ld-icon-chevron-down"></use></svg></button>';
      html += '</div>';
      html += '<div class="wa-sub-detail" hidden>';
      html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Source') + ':</span><code>' + esc(s.source || '-') + '</code></div>';
      if (s.id) { html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">ID:</span><code>' + esc(s.id) + '</code></div>'; }
      if (s.source_name) { html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Name') + ':</span><span>' + esc(s.source_name) + '</span></div>'; }
      if (s.description) { html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Description') + ':</span><span>' + esc(s.description) + '</span></div>'; }
      html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Interval') + ':</span><span>' + esc(String(interval)) + 's (' + formatNextFetch(lastFetch, interval) + ')</span></div>';
      html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Last fetch') + ':</span><span>' + (lastFetch ? new Date(lastFetch * 1000).toLocaleString() : '-') + '</span></div>';
      if (version) { html += '<div class="wa-sub-detail-row"><span class="wa-sub-detail-label">' + gettext('Version') + ':</span><span>v' + esc(version) + '</span></div>'; }
      html += '</div>';
      html += '</div>';
    });
    list.innerHTML = html;

    var allContainers = [list];
    allContainers.forEach(function (container) {
      container.querySelectorAll('[data-sub-toggle]').forEach(function (cb) {
        cb.addEventListener('change', function () {
          var idx = parseInt(this.dataset.subToggle);
          var action = this.checked ? 'enable' : 'disable';
          apiPost(urls.subscriptionManage, { action: action, index: idx }).then(function (r) {
            if (r.error) { toast(r.error, 'error'); loadSubscriptions(); }
            else { subData = r.adapters || []; renderSubscriptions(); }
          });
        });
      });
      container.querySelectorAll('.js-sub-edit').forEach(function (btn) {
        btn.addEventListener('click', function () { openModal('edit', parseInt(this.dataset.index)); });
      });
      container.querySelectorAll('.js-sub-update').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var idx = parseInt(this.dataset.index);
          var btnEl = this;
          btnEl.disabled = true;
          var origHTML = btnEl.innerHTML;
          btnEl.innerHTML = '<svg width="16" height="16" aria-hidden="true" class="wa-spin"><use href="#ld-icon-loader"></use></svg>';
          apiPost(urls.subscriptionManage, { action: 'update', index: idx, force: 1 }).then(function (r) {
            if (r.error) { toast(r.error, 'error'); }
            else {
              subData = r.adapters || []; renderSubscriptions();
              if (r.warning) { toast(r.warning, 'warning'); }
              else { toast(gettext('Updated'), 'success'); }
            }
          }).catch(function () { toast(gettext('Update failed'), 'error'); })
          .finally(function () { btnEl.disabled = false; btnEl.innerHTML = origHTML; });
        });
      });
      container.querySelectorAll('.js-sub-delete').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault(); e.stopPropagation();
          var idx = parseInt(this.dataset.index);
          var popup = document.createElement('ld-confirm-popup');
          popup._button = this;
          popup._onConfirm = function () {
            apiPost(urls.subscriptionManage, { action: 'delete', index: idx }).then(function (r) {
              if (r.error) { toast(r.error, 'error'); }
              else { subData = r.adapters || []; renderSubscriptions(); }
            });
          };
          document.body.appendChild(popup);
        });
      });
      container.querySelectorAll('.js-sub-expand').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var row = btn.closest('.wa-sub-item');
          var detail = row.querySelector(".wa-sub-detail");
          var chevron = btn.querySelector('.wa-sub-chevron use');
          if (detail.hidden) {
            detail.hidden = false;
            chevron.parentElement.classList.add('wa-sub-chevron-up');
          } else {
            detail.hidden = true;
            chevron.parentElement.classList.remove('wa-sub-chevron-up');
          }
        });
      });
    });

    if (subSortable) subSortable.destroy();
    if (typeof Sortable !== 'undefined') {
      subSortable = new Sortable(list, {
        handle: '.wa-sub-drag',
        animation: 150,
        ghostClass: 'wa-sub-ghost',
        dragClass: 'wa-sub-dragging',
        onEnd: function () {
          var indices = [];
          list.querySelectorAll('.wa-sub-item').forEach(function (el) {
            indices.push(el.dataset.index);
          });
          apiPost(urls.subscriptionManage, { action: 'reorder', indices: indices }).then(function (r) {
            if (!r.error) { subData = r.adapters || []; renderSubscriptions(); }
            else { toast(r.error, 'error'); loadSubscriptions(); }
          });
        }
      });
    }
  }
  // ===== Modal =====
  function openModal(mode, index) {
    var overlay = document.getElementById('sub-modal-overlay');
    var form = document.getElementById('sub-modal-form');
    var title = document.getElementById('sub-modal-title');
    overlay.hidden = false;
    if (mode === 'edit' && index !== undefined && subData[index]) {
      title.textContent = gettext('Edit Subscription');
      form.elements['action'].value = 'save';
      form.elements['index'].value = index;
      form.elements['id'].value = subData[index].id || '';
      form.elements['display_name'].value = subData[index].display_name || '';
      form.elements['name'].value = subData[index].name || '';
      form.elements['source'].value = subData[index].source || '';
      form.elements['update_interval'].value = subData[index].update_interval || 86400;
      // display_name placeholder 显示订阅源原始 name
      var dispEl = form.elements['display_name'];
      var canonicalName = subData[index].source_name || subData[index].name || '';
      if (canonicalName && !dispEl.value) {
        dispEl.placeholder = canonicalName;
      }
      // 本地源隐藏 Update Interval
      var sourceVal = form.elements['source'].value;
      var intervalGroup = document.getElementById('sub-interval-group');
      intervalGroup.hidden = sourceVal && !(sourceVal.startsWith('https://') || sourceVal.startsWith('http://'));
      // 显示订阅源描述
      var descEl = document.getElementById('sub-modal-description');
      var descText = document.getElementById('sub-modal-desc-text');
      if (subData[index].description) {
        descText.textContent = subData[index].description;
        descEl.hidden = false;
      } else {
        descEl.hidden = true;
      }
    } else {
      title.textContent = gettext('Add Subscription');
      form.elements['action'].value = 'add';
      form.index.value = '';
      form.reset();
      form.elements['update_interval'].value = 86400;
      form.elements['display_name'].placeholder = '';
      document.getElementById('sub-modal-description').hidden = true;
      document.getElementById('sub-interval-group').hidden = false;
    }
  }

  function closeModal() {
    document.getElementById('sub-modal-overlay').hidden = true;
  }

  function saveModal() {
    var form = document.getElementById('sub-modal-form');
    var btn = document.getElementById('btn-modal-save');
    var origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '...';
    var data = {
      action: form.elements['action'].value,
      source: form.elements['source'].value.trim(),
      update_interval: form.elements['update_interval'].value
    };
    var idVal = form.elements['id'].value.trim();
    if (idVal) data.id = idVal;
    var nameVal = form.elements['name'].value.trim();
    if (nameVal) data.name = nameVal;
    var displayNameVal = form.elements['display_name'].value.trim();
    data.display_name = displayNameVal;
    if (form.elements['index'].value) data.index = form.elements['index'].value;

    apiPost(urls.subscriptionManage, data).then(function (r) {
      if (r.error) { toast(r.error, 'error'); btn.disabled = false; btn.textContent = origText; return; }
      subData = r.adapters || [];
      renderSubscriptions();
      closeModal();
      toast(gettext('Saved'), 'success');
    }).catch(function () {
      toast(gettext('Save failed'), 'error');
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = origText;
    });
  }

  // Auto-detect id/name from source on blur in add mode
  document.getElementById('sub-source').addEventListener('blur', function () {
    if ('add' === document.getElementById('sub-modal-form').elements.action.value) {
      var src = this.value.trim();
      var nameEl = document.getElementById('sub-name');
      var idEl = document.getElementById('sub-id');
      var displayNameEl = document.getElementById('sub-display-name');
      if (src) {
        var parts = src.replace(/\\/g, '/').replace(/\/$/, '').split('/');
        var fname = (parts[parts.length - 1] || '').replace(/\.(jsonc|json)$/i, '');
        if (!fname || fname === 'adapters') fname = parts.length > 1 ? parts[parts.length - 2] : '';
       if (fname) {
         displayNameEl.placeholder = fname;
       } else { displayNameEl.placeholder = ''; }
        // 远程源与本地源都向后端 detect_id 请求自动探测 id/name
        apiGet(urls.subscriptionManage + '?action=detect_id&source=' + encodeURIComponent(src)).then(function (r) {
          if (r) {
            if (r.id) { idEl.value = r.id; }
            if (r.name) {
              nameEl.value = r.name;
              // _meta.name is the canonical display name; prefer it over the
              // path-derived placeholder set above.
              displayNameEl.placeholder = r.name;
            }
          }
        }).catch(function () {});
     } else { displayNameEl.placeholder = ''; idEl.value = ''; nameEl.value = ''; }
      // 根据 source 是否为远程切换 interval 显示
      var intervalGroup = document.getElementById('sub-interval-group');
      intervalGroup.hidden = src && !(src.startsWith('https://') || src.startsWith('http://'));
    }
  });
  document.getElementById('btn-add-subscription').addEventListener('click', function () { openModal('add'); });
  document.getElementById('btn-modal-close').addEventListener('click', closeModal);
  document.getElementById('btn-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('btn-modal-save').addEventListener('click', saveModal);
  document.getElementById('sub-modal-overlay').addEventListener('mousedown', function (e) { if (e.target === this) closeModal(); });

  // ESC to close modal
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !document.getElementById('sub-modal-overlay').hidden) closeModal(); });

  // Update All
  document.getElementById('btn-update-all').addEventListener('click', function () {
    var remoteSubs = [];
    subData.forEach(function (s, i) {
      if (s.enabled !== false && s.source && (s.source.startsWith('https://') || s.source.startsWith('http://'))) {
        remoteSubs.push(i);
      }
    });
    if (!remoteSubs.length) { toast(gettext('No remote adapters to update'), 'info'); return; }

    var btn = document.getElementById('btn-update-all');
    btn.disabled = true;
    var origHTML = btn.innerHTML;
    var completed = 0;
    var errors = 0;

    function updateNext() {
      if (completed + errors >= remoteSubs.length) {
        btn.disabled = false;
        btn.innerHTML = origHTML;
        loadSubscriptions();
        toast(gettext('Updated') + ': ' + completed + '/' + remoteSubs.length + (errors ? ' (' + errors + ' ' + gettext('errors') + ')' : ''), errors ? 'warning' : 'success');
        return;
      }
      var idx = remoteSubs[completed + errors];
      btn.innerHTML = '<svg width="14" height="14" aria-hidden="true" class="wa-spin"><use href="#ld-icon-loader"></use></svg>';
      apiPost(urls.subscriptionManage, { action: 'update', index: idx, force: 1 }).then(function (r) {
        if (r.error) errors++; else completed++;
        updateNext();
      }).catch(function () { errors++; updateNext(); });
    }
    updateNext();
  });

  // ===== URL Test =====
  var URL_HISTORY_KEY = 'ld:test-urls', RESULT_KEY = 'ld:test-result';
  var testHistory = [];
  try { testHistory = JSON.parse(localStorage.getItem(URL_HISTORY_KEY) || '[]'); } catch (e) {}
  var TEST_PREFS_KEY = 'ld:test-prefs';
  var testUrlInput = document.getElementById('test-url');
  var testFormEl = document.getElementById('test-form');
  var testDropdown = document.getElementById('wa-url-dropdown');
  var resultsSection = document.getElementById('test-results');
  var testOutput = document.getElementById('test-output');
  var testStatus = document.getElementById('test-status');

  function restoreResult() {
    try { var s = JSON.parse(localStorage.getItem(RESULT_KEY)); if (s && s.data) { resultsSection.removeAttribute('hidden'); document.getElementById('test-bar').removeAttribute('hidden'); renderTestResult(s.data, s.elapsed || 0, s.testType || ''); } } catch (e) {}
  }
  function saveResult(data, elapsed, testType) { try { localStorage.setItem(RESULT_KEY, JSON.stringify({ data: data, elapsed: elapsed, testType: testType })); } catch (e) {} }
  function renderTestFailure(message, elapsed, testType) {
    var errorResult = { type: testType || 'test', error: String(message || gettext('Request failed')) };
    try {
      renderTestResult(errorResult, elapsed || 0, errorResult.type);
    } catch (e) {
      testStatus.innerHTML = '<span class="wa-status-tag wa-status-tag-error">' + esc(errorResult.type) + '</span> ' + gettext('Error');
      testStatus.style.color = 'var(--ld-error,#dc2626)';
      testOutput.innerHTML = '<div class="wa-result-section"><pre class="wa-result-raw">' + esc(errorResult.error) + '</pre></div>';
    }
    saveResult(errorResult, elapsed || 0, errorResult.type);
  }

  function restoreTestPrefs() {
    try {
      var p = JSON.parse(localStorage.getItem(TEST_PREFS_KEY));
      if (p) {
        if (p.url && testUrlInput) testUrlInput.value = p.url;
        if (p.test_type && testFormEl) testFormEl.test_type.value = p.test_type;
        if (p.test_username && testFormEl) testFormEl.test_username.value = p.test_username;
        var clearBtn = document.querySelector('[data-action="clear-url"]');
        if (clearBtn && p.url) clearBtn.hidden = false;
      }
    } catch (e) {}
  }
  function saveTestPrefs() {
    try { localStorage.setItem(TEST_PREFS_KEY, JSON.stringify({ url: testUrlInput ? testUrlInput.value.trim() : '', test_type: testFormEl ? testFormEl.test_type.value : '', test_username: testFormEl ? testFormEl.test_username.value.trim() : '' })); } catch (e) {}
  }
  if (testUrlInput) { testUrlInput.addEventListener('input', saveTestPrefs); testUrlInput.addEventListener('change', saveTestPrefs); }

  var blurTimer = null;
  function updateDropdown(filter) {
    testDropdown.innerHTML = ''; var q = (filter || '').toLowerCase();
    var matches = testHistory.filter(function (u) { return u.toLowerCase().indexOf(q) >= 0; });
    if (!matches.length) { testDropdown.classList.remove('open'); return; }
    var listDiv = document.createElement('div'); listDiv.className = 'wa-url-dropdown-list';
    matches.forEach(function (url) { var div = document.createElement('div'); div.className = 'wa-url-dropdown-item'; div.innerHTML = '<span>' + esc(url) + '</span><button type="button" class="wa-url-dropdown-del" title="' + gettext('Delete') + '" aria-label="' + gettext('Delete') + '">&times;</button>'; div.querySelector('span').addEventListener('mousedown', function (e) { e.preventDefault(); testUrlInput.value = url; var cb = document.querySelector('[data-action="clear-url"]'); if (cb) cb.hidden = false; testDropdown.classList.remove('open'); }); div.querySelector('.wa-url-dropdown-del').addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); removeHistoryItem(url); updateDropdown(testUrlInput.value); }); listDiv.appendChild(div); });
    testDropdown.appendChild(listDiv);
    if (testHistory.length > 0) { var clearDiv = document.createElement('div'); clearDiv.className = 'wa-url-dropdown-clear'; clearDiv.textContent = gettext('Clear all history'); clearDiv.addEventListener('mousedown', function (e) { e.preventDefault(); testHistory = []; try { localStorage.removeItem(URL_HISTORY_KEY); } catch (ex) {} testDropdown.innerHTML = ''; testDropdown.classList.remove('open'); }); testDropdown.appendChild(clearDiv); }
    testDropdown.classList.add('open');
  }
  function removeHistoryItem(url) { testHistory = testHistory.filter(function (u) { return u !== url; }); try { localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(testHistory)); } catch (e) {} }
  testUrlInput.addEventListener('input', function () {
    updateDropdown(this.value);
    var clearBtn = document.querySelector('[data-action="clear-url"]');
    if (clearBtn) clearBtn.hidden = !this.value;
  });
  testUrlInput.addEventListener('focus', function () { if (blurTimer) { clearTimeout(blurTimer); blurTimer = null; } updateDropdown(this.value); });
  testUrlInput.addEventListener('blur', function () { blurTimer = setTimeout(function () { testDropdown.classList.remove('open'); }, 200); });
  document.querySelector('[data-action="clear-url"]').addEventListener('click', function () {
    testUrlInput.value = '';
    saveTestPrefs();
    this.hidden = true;
    if (blurTimer) { clearTimeout(blurTimer); blurTimer = null; }
    testUrlInput.focus();
  });
  document.getElementById('test-form').addEventListener('submit', function (e) {
    e.preventDefault(); var url = testUrlInput.value.trim(); if (!url) return;
    testHistory = testHistory.filter(function (u) { return u !== url; }); testHistory.unshift(url); if (testHistory.length > 50) testHistory.length = 50;
    try { localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(testHistory)); } catch (ex) {}
    var type = this.test_type.value, username = this.test_username.value.trim();
    var startTime = Date.now();
    saveTestPrefs();
    try { localStorage.removeItem(RESULT_KEY); } catch (ex) {}
    resultsSection.removeAttribute('hidden'); document.getElementById('test-bar').removeAttribute('hidden'); testStatus.innerHTML = '<span class="wa-status-tag wa-status-tag-running">' + esc(type) + '</span> ' + gettext('Running\u2026'); testStatus.style.color = ''; testOutput.innerHTML = '';
    apiPost(urls.action, { action: 'test', url: url, test_type: type, test_username: username || '' })
      .then(function (r) {
        var elapsed = Date.now() - startTime;
        try {
          renderTestResult(r, elapsed, type);
          saveResult(r, elapsed, type);
        } catch (err) {
          renderTestFailure(err && err.message ? err.message : String(err), elapsed, type);
        }
      })
      .catch(function (err) {
        var elapsed = Date.now() - startTime;
        renderTestFailure(err && err.message ? err.message : String(err), elapsed, type);
      });
  });
  document.getElementById('btn-show-details').addEventListener('click', function () {
    var raw = document.getElementById('test-raw');
    var showingRaw = raw.hasAttribute('hidden');
    if (showingRaw) { raw.removeAttribute('hidden'); testOutput.setAttribute('hidden', ''); this.textContent = gettext('Show output'); }
    else { raw.setAttribute('hidden', ''); testOutput.removeAttribute('hidden'); this.textContent = gettext('Raw JSON'); }
  });
  document.getElementById('btn-clear-test').addEventListener('click', function () {
    testOutput.innerHTML = ''; document.getElementById('test-raw').textContent = '';
    testStatus.innerHTML = ''; document.getElementById('test-bar').setAttribute('hidden', '');
    resultsSection.setAttribute('hidden', ''); try { localStorage.removeItem(RESULT_KEY); } catch (e) {}
  });
  if (testFormEl) { testFormEl.test_type.addEventListener('change', saveTestPrefs); testFormEl.test_username.addEventListener('input', saveTestPrefs); }
  document.getElementById('btn-clean-test-files').addEventListener('click', function () { apiPost(urls.action, { action: 'clean_test_files' }).then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Test files cleaned'), 'success'); }); });

  // ===== Test Result Renderers =====
  function formatBytes(bytes) {
    if (bytes == null || bytes === '') return '-';
    var n = Number(bytes);
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function formatDuration(ms) {
    if (ms == null) return '-';
    if (ms < 1000) return ms + 'ms';
    if (ms < 10000) return (ms / 1000).toFixed(2) + 's';
    return (ms / 1000).toFixed(1) + 's';
  }

  function urlLink(url) {
    if (!url) return '-';
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + '</a>';
  }

  function renderValue(key, value) {
    if (value == null || value === '') return '<span class="wa-result-empty">-</span>';
    var s = String(value);
    if (key === 'preview_image' || key === 'image') {
      var proxyUrl = urls.previewImageProxy
        ? urls.previewImageProxy + '?url=' + encodeURIComponent(s)
        : '';
      var fallbackHandler = "if(this.dataset.proxied){this.style.display='none'}else if(this.dataset.proxySrc){this.dataset.proxied='1';this.src=this.dataset.proxySrc}else{this.style.display='none'}";
      return '<div><a href="' + esc(s) + '" target="_blank" rel="noopener" class="wa-result-link">' + esc(s) + '</a></div>' +
             '<img src="' + esc(s) + '" class="wa-preview-img" loading="lazy" referrerpolicy="no-referrer" data-proxy-src="' + esc(proxyUrl) + '" onerror="' + esc(fallbackHandler) + '" alt="">';
    }
    if (/^https?:\/\//.test(s)) return urlLink(s);
    if (key === 'size' || key === 'html_size' || key === 'snapshot_size') return formatBytes(value);
    if (key === 'word_count') return Number(value).toLocaleString();
    if (key === 'has_cookie') return value ? gettext('Yes') : gettext('No');
    if (key === 'refreshed') return value ? gettext('Yes') : gettext('No');
    return esc(s);
  }

  function renderSummaryRows(items) {
    var h = '';
    items.forEach(function (item) {
      if (item.value == null || item.value === '') return;
      h += '<div class="wa-result-row">';
      h += '<span class="wa-result-label">' + esc(item.label) + '</span>';
      h += '<span class="wa-result-value">' + (item.link ? urlLink(item.value) : renderValue(item.label, item.value)) + '</span>';
      h += '</div>';
    });
    return h;
  }

  function renderResultRows(fields, handlers) {
    handlers = handlers || {};
    var h = '';
    var keys = Object.keys(fields);
    keys.forEach(function (key) {
      var val = fields[key];
      if (val == null || val === '') return;
      h += '<div class="wa-result-row">';
      h += '<span class="wa-result-label">' + esc(key) + '</span>';
      h += '<span class="wa-result-value">';
      if (handlers[key]) { h += handlers[key](val); }
      else { h += renderValue(key, val); }
      h += '</span>';
      h += '</div>';
    });
    return h;
  }

  function renderCollapsible(title, contentHTML, open) {
    return '<details class="wa-result-collapse"' + (open ? ' open' : '') + '>' +
      '<summary>' + esc(title) + '</summary>' +
      '<div class="wa-result-collapse-body">' + contentHTML + '</div>' +
      '</details>';
  }

  function renderConfigJSON(config) {
    if (!config || !Object.keys(config).length) return '';
    return '<pre class="wa-result-code">' + esc(JSON.stringify(config, null, 2)) + '</pre>';
  }

  function extractViewFilename(viewUrl) {
    if (!viewUrl) return '';
    var m = viewUrl.match(/[?&]file=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : viewUrl.split('/').pop();
  }
  function filterExecutions(executions, steps) {
    if (!executions || !executions.length) return [];
    if (typeof steps === 'string') steps = [steps];
    return executions.filter(function (e) { return steps.indexOf(e.step) >= 0; });
  }

  function renderCommandInfo(executions) {
    if (!executions || !executions.length) return '';
    var items = [];
    executions.forEach(function (e) {
      if (e.cmd && e.cmd.length) {
        items.push({type: 'cmd', cmd: e.cmd});
      }
      if (e.stdin) {
        items.push({type: 'stdin', text: e.stdin});
      }
    });
    if (!items.length) return '';
    var firstLabel = true;
    var h = '';
    items.forEach(function (item, i) {
      var detailId = 'wa-summary-detail-' + i;
      if (item.type === 'cmd') {
        var cmd = item.cmd;
        var fullCmd;
        if (cmd.length > 1) {
          var last = cmd.length - 1;
          fullCmd = cmd.map(function(a, j) {
            var arg;
            if (a.indexOf(' ') < 0) {
              arg = a;
            } else {
              var eq = a.indexOf('=');
              arg = eq >= 0 ? a.substring(0, eq + 1) + '"' + a.substring(eq + 1) + '"' : '"' + a + '"';
            }
            return j < last ? arg + ' \\' : arg;
          }).join('\n  ');
        } else {
          fullCmd = cmd[0];
        }
        h += '<div class="wa-result-row">';
        h += '<span class="wa-result-label">' + (firstLabel ? gettext('command') : '') + '</span>';
        h += '<span class="wa-result-value">';
        h += '<span class="wa-cmd-toggle" onclick="var d=document.getElementById(\'' + detailId + '\');var s=this.querySelector(\'.wa-cmd-arrow\');if(d.hidden){d.hidden=false;s.textContent=\'\\u25BC\';}else{d.hidden=true;s.textContent=\'\\u25B6\';}">';
        h += '<span class="wa-cmd-arrow">\u25B6</span> ' + esc(cmd[0]) + '</span>';
        h += '<div id="' + detailId + '" class="wa-cmd-detail" hidden>';
        h += '<code>' + esc(fullCmd) + '</code>';
        h += '</div>';
        h += '</span>';
        h += '</div>';
      } else {
        var stdinText = item.text;
        try { stdinText = JSON.stringify(JSON.parse(stdinText), null, 2); } catch (e) {}
        h += '<div class="wa-result-row">';
        h += '<span class="wa-result-label">' + gettext('stdin') + '</span>';
        h += '<span class="wa-result-value">';
        h += '<span class="wa-cmd-toggle" onclick="var d=document.getElementById(\'' + detailId + '\');var s=this.querySelector(\'.wa-cmd-arrow\');if(d.hidden){d.hidden=false;s.textContent=\'\\u25BC\';}else{d.hidden=true;s.textContent=\'\\u25B6\';}">';
        h += '<span class="wa-cmd-arrow">\u25B6</span> ' + esc(stdinText.substring(0, 80)) + (stdinText.length > 80 ? '...' : '') + '</span>';
        h += '<div id="' + detailId + '" class="wa-cmd-detail" hidden>';
        h += '<pre class="wa-stdin-pre">' + esc(stdinText) + '</pre>';
        h += '</div>';
        h += '</span>';
        h += '</div>';
      }
      firstLabel = false;
    });
    return h;
  }

  function renderExecutionLog(executions) {
    if (!executions || !executions.length) return '';
    var h = '<table class="wa-execution-table"><thead><tr>' +
      '<th>' + gettext('Step') + '</th>' +
      '<th>' + gettext('Domain') + '</th>' +
      '<th>' + gettext('Return') + '</th>' +
      '<th>' + gettext('Duration') + '</th>' +
      '</tr></thead><tbody>';
    executions.forEach(function (e) {
      h += '<tr>';
      h += '<td>' + esc(e.step || '-') + '</td>';
      h += '<td><code>' + esc(e.domain_key || '-') + '</code></td>';
      h += '<td>' + (e.returncode === 0 ? '<span class="wa-badge wa-badge-ok">0</span>' : '<span class="wa-badge wa-badge-warn">' + esc(String(e.returncode)) + '</span>') + '</td>';
      h += '<td>' + formatDuration(e.duration_ms) + '</td>';
      h += '</tr>';
    });
    h += '</tbody></table>';
    return h;
  }

  function renderStatusBar(type, isError, elapsed, extra) {
    var tagClass = isError ? 'wa-status-tag-error' : 'wa-status-tag-ok';
    var tagText = isError ? gettext('Error') : gettext('Completed');
    var h = '<span class="wa-status-tag ' + tagClass + '">' + esc(type) + '</span> ';
    h += tagText;
    if (extra) h += ' <span class="wa-status-extra">' + esc(extra) + '</span>';
    if (elapsed) h += ' <span class="wa-status-time">' + formatDuration(elapsed) + '</span>';
    return h;
  }

  function renderTestResult(r, elapsed, testType) {
    var isFatalError = !!r.error;
    var isFailed = isFatalError || !!r.failed;
    testStatus.innerHTML = renderStatusBar(testType || r.type, isFailed, elapsed, '');
    testStatus.style.color = '';

    document.getElementById('test-raw').textContent = JSON.stringify(r, null, 2);

    if (isFatalError) {
      testOutput.innerHTML = '<div class="wa-result-section"><div class="wa-result-error">' + esc(r.error) + '</div></div>';
      return;
    }

    var handlers = {
      'config': renderConfigResult,
      'metadata': renderMetadataResult,
      'snapshot': renderSnapshotResult,
      'reader': renderReaderResult,
      'credential': renderCredentialResult,
      'pipeline': renderPipelineResult
    };
    var fn = handlers[r.type];
    if (fn) {
      testOutput.innerHTML = fn(r);
    } else {
      testOutput.innerHTML = '<div class="wa-result-section"><pre class="wa-result-raw">' + esc(JSON.stringify(r, null, 2)) + '</pre></div>';
    }
  }


  function renderMatchedConfig(r) {
    var matched = r.matched !== false;
    var domainKey = r.domain_key || '';
    var routeKey = r.route_key || '';
    var adapter = r.adapter || null;
    var config = r.raw_config || r.config;

    var summaryValue;
    if (matched && domainKey) {
      var name = (adapter && adapter.name) ? ' (' + esc(adapter.name) + ')' : '';
      summaryValue = esc(domainKey) + esc(name);
      if (routeKey) {
        summaryValue += ' \u2192 route: ' + esc(routeKey);
      }
    } else {
      summaryValue = gettext('Not matched');
    }

    var hasAdapterInfo = matched && adapter;
    var hasConfigContent = false;
    if (config && Object.keys(config).length) {
      var visibleKeys = Object.keys(config).filter(function(k) { return !k.startsWith('_'); });
      if (visibleKeys.length) hasConfigContent = true;
    }
    var hasDetail = hasAdapterInfo || hasConfigContent;

    var detailId = hasDetail ? ('wa-match-detail-' + Math.random().toString(36).slice(2, 8)) : '';
    var h = '';
    h += '<div class="wa-result-row">';
    h += '<span class="wa-result-label">' + esc(gettext('matched_config')) + '</span>';
    h += '<span class="wa-result-value">';

    if (hasDetail) {
      h += '<span class="wa-cmd-toggle" onclick="var d=document.getElementById(' + "'" + detailId + "'" + ');var s=this.querySelector(' + "'" + '.wa-cmd-arrow' + "'" + ');if(d.hidden){d.hidden=false;s.textContent=' + "'" + '\u25BC' + "'" + ';}else{d.hidden=true;s.textContent=' + "'" + '\u25B6' + "'" + ';}">';
      h += '<span class="wa-cmd-arrow">\u25B6</span> ';
    }

    h += summaryValue;

    if (hasDetail) {
      h += '</span>';
      h += '<div id="' + detailId + '" class="wa-match-detail" hidden>';
    }

    if (hasAdapterInfo) {
      h += '<h4 class="wa-match-section-title">' + gettext('Adapter Info') + '</h4>';
      h += '<table class="wa-match-table"><tbody>';
      [
        {label: 'id', value: adapter.id},
        {label: 'name', value: adapter.name},
        {label: 'description', value: adapter.description},
        {label: 'source', value: adapter.source},
        {label: 'local_path', value: adapter.local_path}
      ].forEach(function(row) {
        if (!row.value) return;
        h += '<tr><td class="wa-match-label">' + esc(row.label) + '</td><td class="wa-match-value">' + esc(row.value) + '</td></tr>';
      });
      h += '</tbody></table>';
    }

    if (hasConfigContent) {
      var displayConfig = {};
      Object.keys(config).forEach(function(k) {
        if (!k.startsWith('_')) displayConfig[k] = config[k];
      });
      if (Object.keys(displayConfig).length) {
        h += '<h4 class="wa-match-section-title">' + gettext('Config Content') + '</h4>';
        h += '<pre class="wa-result-code">' + esc(JSON.stringify(displayConfig, null, 2)) + '</pre>';
      }
    }

    if (hasDetail) {
      h += '</div>';
      h += '</span>';
    }
    h += '</div>';
    return h;
  }

  function renderIssues(issues) {
    if (!issues || !issues.length) return '';
    var errors = issues.filter(function(i) { return i.level === 'error'; });
    var warnings = issues.filter(function(i) { return i.level === 'warning'; });
    var infos = issues.filter(function(i) { return i.level === 'info'; });
    var h = '';
    var seq = 0;
    function renderGroup(items, cls) {
      if (!items.length) return '';
      var g = '<div class="wa-issues-group ' + cls + '">';
      items.forEach(function(i) { g += renderIssueItem(i, ++seq); });
      g += '</div>';
      return g;
    }
    h += renderGroup(errors, 'wa-issues-errors');
    h += renderGroup(warnings, 'wa-issues-warnings');
    h += renderGroup(infos, 'wa-issues-infos');
    return h;
  }

  var ISSUE_LEVEL_LABEL = {
    error: gettext('Error'),
    warning: gettext('Warning'),
    info: gettext('Info'),
  };

  function renderIssueItem(i, num) {
    var h = '<div class="wa-issue-item">';
    h += '<span class="wa-issue-index" data-level="' + esc(i.level || 'error') + '">' + num + '</span>';
    h += '<div class="wa-issue-body">';
    // Row 1: Source (file / adapter)
    var source = i.file || i.adapter;
    if (source) {
      h += '<div class="wa-issue-line wa-issue-line-source">';
      h += '<span class="wa-issue-label">' + esc(gettext('Source')) + '</span>';
      h += '<code class="wa-issue-value">' + esc(source) + '</code>';
      h += '</div>';
    }
    // Row 2: Type (level + code)
    h += '<div class="wa-issue-line wa-issue-line-type">';
    h += '<span class="wa-issue-label">' + esc(gettext('Type')) + '</span>';
    var lvlKey = i.level || 'error';
    h += '<span class="wa-issue-level wa-issue-level-' + esc(lvlKey) + '">' + esc(ISSUE_LEVEL_LABEL[lvlKey] || lvlKey) + '</span>';
    h += '<code class="wa-issue-value wa-issue-code">' + esc(i.code || '') + '</code>';
    h += '</div>';
    // Row 3: Field (path) — only if present
    if (i.path) {
      h += '<div class="wa-issue-line wa-issue-line-field">';
      h += '<span class="wa-issue-label">' + esc(gettext('Field')) + '</span>';
      h += '<code class="wa-issue-value">' + esc(i.path) + '</code>';
      h += '</div>';
    }
    // Row 4: Detail (message)
    h += '<div class="wa-issue-line wa-issue-line-detail">';
    h += '<span class="wa-issue-label">' + esc(gettext('Detail')) + '</span>';
    h += '<span class="wa-issue-value wa-issue-message">' + esc(i.message || '') + '</span>';
    h += '</div>';
    h += '</div>';
    h += '</div>';
    return h;
  }

  function renderConfigResult(r) {
    var result = r.result || {};
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    // url, domain, matched config
    h += renderSummaryRows([
      {label: 'url', value: result.url, link: true},
      {label: 'domain', value: result.domain}
    ]);
    var matchObj = {
      matched: result.matched !== false,
      domain_key: result.domain_key || '',
      adapter: result.adapter || null,
      route_key: result.route_key || '',
      raw_config: result.raw_config
    };
    h += renderMatchedConfig(matchObj);
    h += '</div>';
    if (result.merged && Object.keys(result.merged).length) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Merged Config') + '</h3>';
      h += renderConfigJSON(result.merged);
      h += '</div>';
    }
    if (r.issues && r.issues.length) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Issues') + '</h3>';
      h += renderIssues(r.issues);
      h += '</div>';
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderMetadataResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    // original URL, request URL (if rewritten), matched config, command
    var metaUrlRows = [{label: 'original_url', value: r.original_url, link: true}];
    if (r.request_url && r.request_url !== r.original_url) {
      metaUrlRows.push({label: 'request_url', value: r.request_url, link: true});
    }
    h += renderSummaryRows(metaUrlRows);
    h += renderMatchedConfig(r);
    h += renderCredentialSources(r.credential_sources);
    h += renderCommandInfo(filterExecutions(r.executions, ['metadata', 'metadata_script', 'before_hook', 'replace_hook', 'after_hook']));
    h += '</div>';
    if (r.result) {
      var fields = r.result;
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      if (r.metadata_error) {
        h += '<div class="wa-result-error">' + esc(r.metadata_error) + '</div>';
      }
      var orderedKeys = ['title', 'description', 'preview_image'];
      var restKeys = fields ? Object.keys(fields).filter(function (k) { return orderedKeys.indexOf(k) < 0 && k !== 'url'; }) : [];
      var allKeys = orderedKeys.concat(restKeys);
      var orderedFields = {};
      allKeys.forEach(function (k) { if (fields && k in fields) orderedFields[k] = fields[k]; });
      var fieldRows = renderResultRows(orderedFields);
      if (fieldRows) {
        h += fieldRows;
      } else if (!r.metadata_error) {
        h += '<div class="wa-result-empty">' + gettext('No metadata extracted') + '</div>';
      }
      h += '</div>';
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Merged Config'), renderConfigJSON(r.merged_config), false);
    }
    if (r.default_config && Object.keys(r.default_config).length) {
      h += renderCollapsible(gettext('Built-in Default Engine Config'), renderConfigJSON(r.default_config), true);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderSnapshotResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    // original URL, request URL (if rewritten), matched config, command
    var snapUrlRows = [{label: 'original_url', value: r.original_url, link: true}];
    if (r.request_url && r.request_url !== r.original_url) {
      snapUrlRows.push({label: 'request_url', value: r.request_url, link: true});
    }
    h += renderSummaryRows(snapUrlRows);
    h += renderMatchedConfig(r);
    h += renderCredentialSources(r.credential_sources);
    h += renderCommandInfo(filterExecutions(r.executions, ['snapshot', 'snapshot_script']));
    h += '</div>';
    if (r.disabled) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      h += '<div class="wa-result-disabled-hint">' + gettext('Snapshots are disabled (snapshot.enabled is false)') + '</div>';
      h += '</div>';
    } else if (r.result) {
      var fields = r.result;
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      var snapFields = {};
      Object.keys(fields).forEach(function (k) {
        if (k !== 'view_url' && k !== 'size') snapFields[k] = fields[k];
      });
      h += renderResultRows(snapFields, {
        'file': function (val) { return '<a href="' + esc(fields.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.size) + ')'; }
      });
      h += '</div>';
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Merged Config'), renderConfigJSON(r.merged_config), false);
    }
    if (r.default_config && Object.keys(r.default_config).length) {
      h += renderCollapsible(gettext('Built-in Default Engine Config'), renderConfigJSON(r.default_config), true);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderReaderResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    // original URL, request URL (if rewritten), matched config, command
    var readerUrlRows = [{label: 'original_url', value: r.original_url, link: true}];
    if (r.request_url && r.request_url !== r.original_url) {
      readerUrlRows.push({label: 'request_url', value: r.request_url, link: true});
    }
    h += renderSummaryRows(readerUrlRows);
    h += renderMatchedConfig(r);
    h += renderCredentialSources(r.credential_sources);
    h += renderCommandInfo(filterExecutions(r.executions, ['reader']));
    h += '</div>';
    if (r.result) {
      var fields = r.result;
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      h += renderResultRows({
        'title': fields.title,
        'word_count': fields.word_count,
        'reader_view': fields.reader_view ? extractViewFilename(fields.reader_view) : null,
        'reader_file': fields.view_url ? extractViewFilename(fields.view_url) : null,
        'snapshot_file': fields.snapshot_view_url ? extractViewFilename(fields.snapshot_view_url) : null
      }, {
        'title': function (val) { return esc(val); },
        'word_count': function (val) { return Number(val).toLocaleString(); },
        'reader_view': function (val) { return '<a href="' + esc(fields.reader_view) + '" target="_blank">' + esc(val) + '</a>'; },
        'reader_file': function (val) { return '<a href="' + esc(fields.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.html_size) + ')'; },
        'snapshot_file': function (val) { return '<a href="' + esc(fields.snapshot_view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.snapshot_size) + ')'; }
      });
      h += '</div>';
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Merged Config'), renderConfigJSON(r.merged_config), false);
    }
    if (r.default_config && Object.keys(r.default_config).length) {
      h += renderCollapsible(gettext('Built-in Default Engine Config'), renderConfigJSON(r.default_config), true);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderCredentialSources(cs) {
    if (!cs || !Object.keys(cs).length) return '';
    var parts = [];
    if (cs.cookie) parts.push('cookie · ' + cs.cookie.source + ' · ' + cs.cookie.status);
    if (cs.headers) parts.push('headers · ' + cs.headers.source + ' · ' + cs.headers.status);
    if (cs.oauth2) parts.push('oauth2 · ' + cs.oauth2.source + ' · ' + cs.oauth2.status);
    return renderSummaryRows([{label: 'credential_sources', value: parts.join('; ')}]);
  }

  function renderCredentialResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'domain_key', value: r.domain_key}
    ]);
    if (r.cookie) {
      var c = r.cookie;
      var label = c.source + ' \u00b7 ' + c.status;
      if (c.cookie_type === 'auto') label += ' (auto)';
      if (c.status === 'invalid' && c.expired_at) label += ' (' + c.expired_at.slice(0, 10) + ')';
      h += renderSummaryRows([{label: 'cookie', value: label}]);
    }
    if (r.headers) {
      h += renderSummaryRows([{label: 'headers', value: r.headers.source + ' \u00b7 ' + r.headers.status}]);
    }
    if (r.oauth2) {
      h += renderSummaryRows([{label: 'oauth2', value: r.token.source + ' \u00b7 ' + r.token.status}]);
    }
    h += renderCommandInfo(filterExecutions(r.executions, ['cookie_refresh', 'cookie_verify']));
    h += '</div>';
    // Cookie detail
    if (r.cookie) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">Cookie</h3>';
      var cookieRows = {
        'source': r.cookie.source,
        'status': r.cookie.status,
        'has_value': r.cookie.has_value,
        'preview': r.cookie.preview
      };
      if (r.cookie.status === 'invalid' && r.cookie.expired_at) {
        cookieRows['expired_at'] = r.cookie.expired_at.slice(0, 10);
      }
      h += renderResultRows(cookieRows);
      h += '</div>';
    }
    // Headers detail
    if (r.headers && r.headers.headers) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">Headers</h3>';
      var hrows = {};
      r.headers.headers.forEach(function(hdr) {
        hrows[hdr.name] = (hdr.has_value ? (hdr.source + ' \u00b7 existing') : 'none');
      });
      h += renderResultRows(hrows);
      h += '</div>';
    }
    // Token detail
    if (r.oauth2) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">OAuth2</h3>';
      h += renderResultRows({
        'source': r.token.source,
        'status': r.token.status,
        'has_value': r.token.has_value
      });
      h += '</div>';
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderPipelineResult(r) {
    var h = '<div class="wa-result-section">';
    if (r.config) {
      var cfg = r.config;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">1</span> ' + gettext('Config') + '</h3>';
      // url, domain, matched config
      h += renderSummaryRows([
        {label: 'url', value: cfg.url, link: true},
        {label: 'domain', value: cfg.domain}
      ]);
      var pipeCfgMatchObj = {
        matched: cfg.matched !== false,
        domain_key: cfg.domain_key || '',
        adapter: cfg.adapter || null,
        route_key: cfg.route_key || '',
        raw_config: cfg.raw_config
      };
      h += renderMatchedConfig(pipeCfgMatchObj);
      h += '</div>';
    }
    if (r.metadata) {
      var m = r.metadata;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">2</span> ' + gettext('Metadata') + '</h3>';
      // original URL, request URL (if rewritten), matched config
      var pipeMetaUrlRows = [{label: 'original_url', value: m.original_url, link: true}];
      if (m.request_url && m.request_url !== m.original_url) {
        pipeMetaUrlRows.push({label: 'request_url', value: m.request_url, link: true});
      }
      h += renderSummaryRows(pipeMetaUrlRows);
      h += renderMatchedConfig(m);
      h += renderCredentialSources(m.credential_sources);
      if (m.metadata_error) {
        h += '<div class="wa-result-error">' + esc(m.metadata_error) + '</div>';
      }
      if (m.result) {
        var orderedKeys = ['title', 'description', 'preview_image'];
        var restKeys = Object.keys(m.result).filter(function (k) { return orderedKeys.indexOf(k) < 0 && k !== 'url'; });
        var allKeys = orderedKeys.concat(restKeys);
        var orderedFields = {};
        allKeys.forEach(function (k) { if (k in m.result) orderedFields[k] = m.result[k]; });
        var fieldRows = renderResultRows(orderedFields);
        if (fieldRows) {
          h += fieldRows;
        } else if (!m.metadata_error) {
          h += '<div class="wa-result-empty">' + gettext('No metadata extracted') + '</div>';
        }
      }
      h += renderCommandInfo(filterExecutions(r.executions, ['metadata', 'metadata_script', 'before_hook', 'replace_hook', 'after_hook']));
      h += '</div>';
    }
    if (r.snapshot) {
      var s = r.snapshot;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">3</span> ' + gettext('Snapshot') + '</h3>';
      // original URL, request URL (if rewritten), matched config
      var pipeSnapUrlRows = [{label: 'original_url', value: s.original_url, link: true}];
      if (s.request_url && s.request_url !== s.original_url) {
        pipeSnapUrlRows.push({label: 'request_url', value: s.request_url, link: true});
      }
      h += renderSummaryRows(pipeSnapUrlRows);
      h += renderMatchedConfig(s);
      h += renderCredentialSources(s.credential_sources);
      if (s.disabled) {
        h += '<div class="wa-result-disabled-hint">' + gettext('Snapshots are disabled (snapshot.enabled is false)') + '</div>';
      } else if (s.result) {
        var pipeSnapFields = {};
        Object.keys(s.result).forEach(function (k) {
          if (k !== 'view_url' && k !== 'size') pipeSnapFields[k] = s.result[k];
        });
        h += renderResultRows(pipeSnapFields, {
          'file': function (val) { return '<a href="' + esc(s.result.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(s.result.size) + ')'; }
        });
      }
      h += renderCommandInfo(filterExecutions(r.executions, ['snapshot', 'snapshot_script']));
      h += '</div>';
    }
    if (r.reader) {
      var rd = r.reader;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">4</span> ' + gettext('Reader') + '</h3>';
      // original URL, request URL (if rewritten), matched config
      var pipeReaderUrlRows = [{label: 'original_url', value: rd.original_url, link: true}];
      if (rd.request_url && rd.request_url !== rd.original_url) {
        pipeReaderUrlRows.push({label: 'request_url', value: rd.request_url, link: true});
      }
      h += renderSummaryRows(pipeReaderUrlRows);
      h += renderMatchedConfig(rd);
      h += renderCredentialSources(rd.credential_sources);
      if (rd.result) {
        h += renderResultRows({
          'title': rd.result.title,
          'word_count': rd.result.word_count,
          'reader_view': rd.result.reader_view ? extractViewFilename(rd.result.reader_view) : null,
          'reader_file': rd.result.view_url ? extractViewFilename(rd.result.view_url) : null,
          'snapshot_file': rd.result.snapshot_view_url ? extractViewFilename(rd.result.snapshot_view_url) : null
        }, {
          'title': function (val) { return esc(val); },
          'word_count': function (val) { return Number(val).toLocaleString(); },
          'reader_view': function (val) { return '<a href="' + esc(rd.result.reader_view) + '" target="_blank">' + esc(val) + '</a>'; },
          'reader_file': function (val) { return '<a href="' + esc(rd.result.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(rd.result.html_size) + ')'; },
          'snapshot_file': function (val) { return '<a href="' + esc(rd.result.snapshot_view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(rd.result.snapshot_size) + ')'; }
        });
      }
      h += '</div>';
    }
    if (r.config && r.config.merged && Object.keys(r.config.merged).length) {
      h += renderCollapsible(gettext('Merged Config'), renderConfigJSON(r.config.merged), false);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  restoreTestPrefs();
  restoreResult();
  switchMode(MODE);

})();
