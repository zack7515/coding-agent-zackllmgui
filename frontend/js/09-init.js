/* ══════════════════════ 啟動 ══════════════════════ */
function init() {
  // 靜態圖示
  $('newChatIcon').innerHTML = ico('plus', 15, 2);
  $('searchIcon').innerHTML = ico('search', 14, 2);
  $('settingsBtn').innerHTML = ico('gear', 17, 1.7);
  $('moreBtn').innerHTML = ico('more', 16);
  $('modelChev').innerHTML = ico('chevDown', 14, 2);
  $('attachBtn').innerHTML = ico('clip', 15);
  $('compactBtn').innerHTML = ico('compress', 15, 2);
  $('featBtn').innerHTML = ico('wrench', 15, 2);
  $('writeBtn').innerHTML = ico('pencil', 15, 2);
  $('sendBtn').innerHTML = ico('send', 17, 2.4);
  $('resetIcon').innerHTML = ico('retry', 13);
  $('thinkSecIcon').innerHTML = ico('sparkle', 13, 2);
  $('retryBtn').innerHTML = ico('retry', 11, 2.2) + '重試';

  buildSliders();

  // 讀設定
  const conf = lsGet(LS_CONF) || {};
  S.host = normalizeHost(conf.host || DEFAULT_HOST);
  S.model = conf.model || '';
  S.theme = conf.theme === 'light' ? 'light' : 'dark';
  S.think = conf.think !== undefined ? conf.think : false;
  S.showThink = conf.showThink !== false;
  S.params = Object.assign({}, DEFAULTS, conf.params || {});
  // 舊版的預設值是 4096，太容易爆；沒動過的人直接跟著升上來
  if (String(S.params.num_ctx) === '4096' && conf.paramsVersion !== 2) S.params.num_ctx = DEFAULTS.num_ctx;
  // v3 把 num_ctx 的單位從 token 改成 K。舊設定存的是 65536 這種數字，
  // 不換算的話會變成 65536K —— 直接把記憶體吃爆。
  if (conf.paramsVersion !== 3 && +S.params.num_ctx >= 1024) {
    S.params.num_ctx = Math.round(+S.params.num_ctx / 1024);
  }
  S.tools = conf.tools !== false;      // 預設開啟，關過才記住關著
  S.presets = lsGet(LS_PRESETS) || [];
  S.provider = conf.provider === 'openai' ? 'openai' : 'ollama';
  S.tab = TABS.indexOf(conf.tab) >= 0 ? conf.tab : 'params';
  S.auto = ['off', 'read', 'edit', 'full'].indexOf(conf.auto) >= 0 ? conf.auto : 'off';
  S.fontScale = +conf.fontScale > 0 ? +conf.fontScale : 1;
  S.userName = conf.userName || '';
  renderUser();
  applyFontScale();
  if (conf.hideSidebar) document.body.classList.add('hide-sidebar');
  if (conf.hideParams) document.body.classList.add('hide-params');
  applySideWidth('--side-w', $('sidebar'), conf.sideW);
  applySideWidth('--params-w', $('params'), conf.paramsW);
  wireResizer($('sideResize'), $('sidebar'), '--side-w', 'right');
  wireResizer($('paramResize'), $('params'), '--params-w', 'left');
  S.oa = Object.assign({ base: 'https://api.openai.com/v1', key: '' }, conf.oa || {});
  applyTheme();
  paramsToUi();
  renderFeatBtn();

  const chats = lsGet(LS_CHATS);
  S.chats = (chats && chats.length) ? chats : [];
  if (!S.chats.length) S.chats.push({ id: uid(), title: '新對話', model: '', messages: [] });
  S.currentId = S.chats[0].id;

  renderChatList();
  renderThread();
  renderThinkSeg();
  renderCompactBtns();
  showTab(S.tab);
  renderToggles();
  setConn('idle');

  // 事件
  $('newChatBtn').addEventListener('click', function () { newChat(); });
  $('searchBox').addEventListener('input', renderChatList);
  $('settingsBtn').addEventListener('click', function (e) {
    e.stopPropagation();
    openSettingsMenu();
  });
  $('statusPill').addEventListener('click', function (e) {
    if (e.target.closest('#retryBtn')) { refreshModels(); return; }
    openHostDialog();
  });
  $('retryBtn').addEventListener('click', function (e) { e.stopPropagation(); refreshModels(); });

  $('modelBtn').addEventListener('click', function (e) {
    e.stopPropagation();
    const items = S.models.map(function (m) {
      const bits = [];
      if (m.details && m.details.parameter_size) bits.push(m.details.parameter_size);
      if (m.size) bits.push(humanSize(m.size));
      return { label: m.name, meta: bits.join(' · '), checked: m.name === S.model,
        action: function () { selectModel(m.name); } };
    });
    if (!items.length) items.push({ label: '（沒有可用模型）', action: function () {} });
    items.push('-', { label: '重新整理模型清單', action: function () { refreshModels(); } });
    showMenu($('modelBtn'), items);
  });

  $('moreBtn').addEventListener('click', function (e) {
    e.stopPropagation();
    const items = [
      { label: '從結尾分支出新對話', action: function () { forkChat(-1); } },
      '-',
      { label: '匯出對話為 HTML', meta: '可直接開', action: function () { exportChat('html'); } },
      { label: '匯出對話為 Markdown', action: function () { exportChat('md'); } },
      { label: '匯出對話為 JSON', action: function () { exportChat('json'); } },
      { label: '還原檔案到某個時間點…', action: openRewind },
      '-',
      { label: '設定…', action: function () { openSettingsMenu($('moreBtn')); } }
    ];
    // 壓縮本身在輸入框旁邊有按鈕，這裡只留「還原」—— 那個沒有別的入口
    if (current() && current().preCompact) {
      items.unshift({ label: '還原上次壓縮', action: uncompact });
    }
    showMenu($('moreBtn'), items);
  });

  $('avatarBtn').addEventListener('click', renameUser);
  $('hostHelp').addEventListener('click', openHostHelp);
  $('rlAdd').addEventListener('click', addRuleFromDialog);
  $('rlClose').addEventListener('click', function () {
    $('rlOverlay').classList.add('hidden');
  });
  $('rlPattern').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); addRuleFromDialog(); }
  });
  $('rlHelp').addEventListener('click', openRulesHelp);
  $('thinkHelp').addEventListener('click', openThinkHelp);
  $('rwHelp').addEventListener('click', openRewindHelp);
  $('fsHelp').addEventListener('click', openFontHelp);
  $('pullHelp').addEventListener('click', openPullHelp);
  $('cmpHelp').addEventListener('click', openCompareHelp);
  $('sendBtn').addEventListener('click', send);
  $('attachBtn').addEventListener('click', pickFiles);
  $('attachClear').addEventListener('click', function () {
    S.images = []; S.files = []; renderAttach();
  });

  // 拖進視窗就當附件
  ['dragover', 'drop'].forEach(function (type) {
    document.addEventListener(type, function (e) {
      e.preventDefault();
      if (type !== 'drop') return;
      Array.prototype.slice.call((e.dataTransfer || {}).files || []).forEach(function (f) {
        addFile(f).catch(function (err) { toast('讀取 ' + f.name + ' 失敗：' + err.message); });
      });
    });
  });

  $('presetBtn').addEventListener('click', function (e) { e.stopPropagation(); openPresetMenu(); });
  $('presetSave').addEventListener('click', savePreset);
  $('sideCollapse').addEventListener('click', function () { togglePanel('sidebar'); });
  $('paramCollapse').addEventListener('click', function () { togglePanel('params'); });
  $('sideRail').addEventListener('click', function () { togglePanel('sidebar'); });
  $('paramRail').addEventListener('click', function () { togglePanel('params'); });
  $('tabParams').addEventListener('click', function () { showTab('params'); });
  $('tabFile').addEventListener('click', function () { showTab('file'); });
  $('tabHist').addEventListener('click', function () { showTab('hist'); });
  $('fvDiff').addEventListener('click', function () {
    if (!S.fv) return;
    S.fv.mode = S.fv.mode === 'diff' ? 'text' : 'diff';
    renderFileView();
  });
  $('fvReload').addEventListener('click', function () {
    if (S.fv) openFile(S.fv.path, S.fv.backup);
  });

  $('wsPick').addEventListener('click', openBrowser);
  $('fvBack').addEventListener('click', showTreeView);
  $('writeBtn').addEventListener('click', toggleWrite);

  $('brCancel').addEventListener('click', function () {
    $('brOverlay').classList.add('hidden');
  });
  $('rwReload').addEventListener('click', loadHistory);
  $('brUp').addEventListener('click', function () {
    if (S.browse && S.browse.parent) browseTo(S.browse.parent);
  });
  $('brHome').addEventListener('click', function () {
    browseTo((S.browse && S.browse.home) || '~');
  });
  $('brGo').addEventListener('click', function () { browseTo($('brPath').value.trim()); });
  $('brPath').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); browseTo($('brPath').value.trim()); }
  });
  $('brPickHere').addEventListener('click', function () {
    const pick = S.browse && S.browse.path;
    if (!pick) return;
    applyWorkspace(pick).then(function () {
      $('brOverlay').classList.add('hidden');
    }, function (e) { toast(e.message); });
  });

  $('paramHelp').addEventListener('click', openParamHelp);
  $('wsHelp').addEventListener('click', openWorkspaceHelp);
  $('fsMinus').addEventListener('click', function () { stepFont(-1); });
  $('fsPlus').addEventListener('click', function () { stepFont(1); });
  $('fsReset').addEventListener('click', function () {
    S.fontScale = 1; applyFontScale(); saveConfig();
  });
  $('fsValue').addEventListener('change', function () { setFont($('fsValue').value); });
  $('fsValue').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); setFont($('fsValue').value); }
    if (e.key === 'ArrowUp') { e.preventDefault(); stepFont(1); }
    if (e.key === 'ArrowDown') { e.preventDefault(); stepFont(-1); }
  });
  $('fsValue').addEventListener('blur', applyFontScale);
  $('fsClose').addEventListener('click', function () {
    $('fsOverlay').classList.add('hidden');
  });
  $('phClose').addEventListener('click', function () {
    $('phOverlay').classList.add('hidden');
  });

  $('fileSave').addEventListener('click', saveFileEditor);
  $('fileCancel').addEventListener('click', function () { $('fileOverlay').classList.add('hidden'); });
  $('fileDelete').addEventListener('click', function () {
    S.files.splice(S.editing, 1);
    $('fileOverlay').classList.add('hidden');
    renderAttach();
  });

  $('compactBtn').addEventListener('click', compactChat);
  $('featBtn').addEventListener('click', function (e) { e.stopPropagation(); openFeatureMenu(); });
  $('ctxRow').addEventListener('click', function () {
    if ($('ctxRow').style.cursor === 'pointer') compactChat();
  });

  // 貼上一大段文字就收成附件，不要把輸入框撐爆
  $('input').addEventListener('paste', function (e) {
    const dt = e.clipboardData;
    if (!dt) return;
    const files = Array.prototype.slice.call(dt.files || []);
    if (files.length) {
      e.preventDefault();
      files.forEach(function (f) {
        addFile(f).catch(function (err) { toast('讀取失敗：' + err.message); });
      });
      return;
    }
    const text = dt.getData('text') || '';
    if (text.length < PASTE_LIMIT) return;         // 短的照常貼進輸入框
    e.preventDefault();
    attachPasted(text);
  });

  $('mmClose').addEventListener('click', function () { $('modelsOverlay').classList.add('hidden'); });
  $('mmRefresh').addEventListener('click', refreshModelManager);
  $('pullBtn').addEventListener('click', pullModel);
  $('pullCancel').addEventListener('click', function () { if (S.pullAbort) S.pullAbort.abort(); });
  $('pullInput').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') pullModel();
  });
  $('cmpClose').addEventListener('click', function () { $('cmpOverlay').classList.add('hidden'); });
  $('cmpRun').addEventListener('click', runCompare);
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      copyText($('cmpBody' + btn.getAttribute('data-copy')).textContent);
    });
  });
  $('showThink').addEventListener('change', function () {
    S.showThink = $('showThink').checked;
    renderThread(); saveConfig();
  });
  $('resetBtn').addEventListener('click', function () {
    S.params = Object.assign({}, DEFAULTS);
    paramsToUi(); saveConfig();
  });

  FIELDS.forEach(function (k) {
    $(k).addEventListener('change', function () {
      S.params[k] = $(k).value;
      clampField(k);                 // num_gpu / num_thread 超過上限就夾回去
      saveConfig(); updateCtx();
    });
  });
  $('system').addEventListener('change', function () {
    S.params.system = $('system').value; saveConfig();
  });
  $('system').addEventListener('input', updateCtx);

  $('input').addEventListener('input', function () {
    autoGrow(); updateCtx(); updateSlash();
  });
  $('input').addEventListener('keydown', function (e) {
    // 斜線功能表開著時，上下與 Enter 是在選項目，不是在編輯訊息
    if (S.slash.items.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveSlash(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); moveSlash(-1); return; }
      if (e.key === 'Tab') { e.preventDefault(); moveSlash(e.shiftKey ? -1 : 1); return; }
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault(); runSlash(); return;
      }
      if (e.key === 'Escape') { e.preventDefault(); $('input').value = ''; updateSlash(); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault(); submitFromInput();          // 跑到一半會排隊，不是送出
    }
  });

  $('scroll').addEventListener('scroll', function () {
    const s = $('scroll');
    S.stick = s.scrollHeight - s.scrollTop - s.clientHeight < 40;
  });

  $('thread').addEventListener('click', function (e) {
    const ref = e.target.closest('.file-ref');
    if (ref) {
      openFile(ref.getAttribute('data-path'), '', ref.getAttribute('data-line'));
      return;
    }
    const btn = e.target.closest('.code-copy');
    if (btn) copyText(btn.getAttribute('data-code'));
  });

  $('hostTest').addEventListener('click', testHost);
  $('hostCancel').addEventListener('click', function () { $('hostOverlay').classList.add('hidden'); });
  ['hostOverlay', 'modelsOverlay', 'cmpOverlay', 'fileOverlay'].forEach(function (id) {
    $(id).addEventListener('click', function (e) {
      if (e.target === $(id)) $(id).classList.add('hidden');
    });
  });
  $('hostSave').addEventListener('click', function () {
    S.provider = dialogMode();
    S.host = normalizeHost($('hostInput').value);
    S.oa = { base: normalizeBase($('oaBase').value), key: $('oaKey').value.trim() };
    S.caps = {};
    S.model = '';                       // 換了後端，舊模型名多半不存在
    $('hostOverlay').classList.add('hidden');
    saveConfig();
    loadUpstream().then(function () { refreshModels(); loadSkills(); });
  });
  ['hostInput', 'oaBase', 'oaKey'].forEach(function (id) {
    $(id).addEventListener('keydown', function (e) {
      if (e.key === 'Enter') $('hostSave').click();
    });
  });

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey && (e.key === 'n' || e.key === 'N')) { e.preventDefault(); newChat(); }
    if (e.ctrlKey && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); openModels(); }
    if (e.key === 'F5' && !e.ctrlKey) { e.preventDefault(); refreshModels(); }
    if (e.key === 'Escape') {
      closeMenu();
      ['hostOverlay', 'modelsOverlay', 'cmpOverlay', 'fileOverlay',
       'phOverlay', 'fsOverlay', 'brOverlay', 'rlOverlay'].forEach(function (id) {
        $(id).classList.add('hidden');
      });
    }
  });

  window.addEventListener('beforeunload', function () { saveChats(); saveConfig(); });

  loadUpstream().then(function () {
    refreshModels();
    restoreServerState(conf);      // 工作區與四個開關都在 serve.py 的行程裡，重啟就沒了
  });
  updateCtx();
  setInterval(function () { if (!S.streaming) refreshModels(true); }, 30000);
  setInterval(checkSourceChanged, 30000);   // serve.py 改過就自己重開＋重整
  $('resumeBtn').addEventListener('click', resumeRun);
  $('queueDrop').addEventListener('click', function () {
    S.queued = []; renderQueue(); toast('排隊的話取消了');
  });
  $('input').focus();

  // 給測試用
  window.__app = { S: S, refreshModels: refreshModels, send: send, renderMarkdown: renderMarkdown,
    buildOptions: buildOptions, thinkValue: thinkValue,
    fenceFor: fenceFor, estTokens: estTokens, updateCtx: updateCtx, forkChat: forkChat,
    toolsReady: toolsReady, openModels: openModels, openCompare: openCompare,
    allPresets: allPresets, apiMessages: apiMessages, guessExt: guessExt,
    compactChat: compactChat, uncompact: uncompact, oaMsg: oaMsg,
    normalizePull: normalizePull, toolsReason: toolsReason, FEATURES: FEATURES,
    toolDefs: toolDefs, agentRules: agentRules, renderDiff: renderDiff,
    codeLines: codeLines, diffLines: diffLines, openFile: openFile, togglePanel: togglePanel,
    renderTodos: renderTodos, askUser: askUser, autoApprove: autoApprove,
    runStreamed: runStreamed, renderRunBar: renderRunBar,
    renderWsGit: renderWsGit, applyAgentState: applyAgentState,
    AUTO_MODES: AUTO_MODES,
    normalizeBase: normalizeBase, PRESETS: PRESETS };
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

