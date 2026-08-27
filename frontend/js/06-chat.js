/* ══════════════════════ 對話資料 ══════════════════════ */
function current() {
  for (let i = 0; i < S.chats.length; i++) if (S.chats[i].id === S.currentId) return S.chats[i];
  return S.chats[0];
}
function newChat(focus) {
  const c = { id: uid(), title: '新對話', model: S.model, messages: [], created: Date.now() };
  S.chats.unshift(c);
  S.currentId = c.id;
  renderChatList();
  renderThread();
  saveChats();
  if (S.tab === 'hist') loadHistory();       // 新對話還沒改過任何檔案
  if (focus !== false) $('input').focus();
}
function autoTitle(c) {
  if (c.renamed) return;                 // 使用者取過名字就不要再蓋掉
  for (let i = 0; i < c.messages.length; i++) {
    if (c.messages[i].role === 'user') {
      const m = c.messages[i];
      const t = String(m.text !== undefined ? m.text : m.content).replace(/\s+/g, ' ').trim();
      c.title = t.length > 26 ? t.slice(0, 26) + '…' : (t || '新對話');
      return;
    }
  }
  c.title = '新對話';
}
// 存不進去就講。只講一次：工具迴圈裡每次呼叫都會存一次，每次都 toast 等於洗版。
let saveWarned = false;
let useIdb = true;          // IndexedDB 開不起來（file:// 的 Firefox、無痕）就退回 localStorage

// saveChats() 有二十一個呼叫點，全部是「改完就叫一次」的同步寫法，而 IndexedDB
// 是非同步的。所以這裡不改呼叫端：標記哪幾則髒了，收成一批在下一個空檔寫。
// 400ms 是因為串流時一秒會叫好幾次 —— 每次都開一個交易只是在浪費。
const dirtyChats = new Set();
let flushTimer = null;

// 開場只做一次。舊版存在 localStorage，搬進 IndexedDB 之後把那 5–10MB 讓出來 ——
// 留著的話它只是佔位子，而且下次載入還要判斷該信哪一份。
async function loadChats() {
  const old = lsGet(LS_CHATS);
  try {
    let list = await chatsLoad();
    if (!list.length && old && old.length) {
      // 原本的順序就是「新的在前」，用遞減的時間戳把它保住
      let t = Date.now();
      old.forEach(function (c) { if (!c.created) c.created = t--; });
      await Promise.all(old.map(chatPut));
      list = old;
      try { localStorage.removeItem(LS_CHATS); } catch (e) { /* 沒搬掉也不影響 */ }
      toast('對話已搬進 IndexedDB（' + list.length + ' 則），不再卡在 5MB');
    }
    return list;
  } catch (e) {
    useIdb = false;
    return old || [];
  }
}

function saveChats(chat) {
  const c = chat || current();
  if (c) dirtyChats.add(c.id);
  if (!useIdb) { lsSave(); return; }
  if (flushTimer === null) flushTimer = setTimeout(flushChats, 400);
}

function lsSave() {
  if (lsSet(LS_CHATS, S.chats.slice(0, 100))) { saveWarned = false; return; }
  if (saveWarned) return;
  saveWarned = true;
  toast('對話存不進瀏覽器了（空間滿了）——'
    + '請先匯出這個對話，或刪掉幾個舊對話，否則重整就會不見');
}

// 寫失敗**一定要說**：靜靜掉一整場任務是這裡最不能接受的失敗方式。
function flushChats() {
  clearTimeout(flushTimer);
  flushTimer = null;
  if (!dirtyChats.size) return Promise.resolve();
  const ids = Array.from(dirtyChats);
  dirtyChats.clear();
  const jobs = ids.map(function (id) {
    const c = S.chats.filter(function (x) { return x.id === id; })[0];
    return c ? chatPut(c) : chatDrop(id);
  });
  return Promise.all(jobs).then(function () { saveWarned = false; }, function (e) {
    useIdb = false;                       // 退回去，至少還存得下最近幾場
    lsSave();
    if (saveWarned) return;
    saveWarned = true;
    toast('對話存不進 IndexedDB（' + e.message + '），已改用瀏覽器的小空間，請盡快匯出');
  });
}

// 一輪結束（最外層的 runStream 回來了）。有跑過工具才通知 ——
// 一句話問答不會讓人跑去別的分頁等。
function finishTurn() {
  const r = S.run || {};
  if (!r.calls) return;
  notifyBg('跑完了：' + r.rounds + ' 輪 · ' + r.calls + ' 次工具 · '
    + fmtTokens(r.tokens) + ' tokens');
}

// 有東西在等人回答（確認卡、ask_user_question）就把分頁標題改掉。
// 通知有可能被瀏覽器擋掉或使用者沒給權限，**標題一定看得到** ——
// 而且切回來之前，分頁列上那個 ● 就是「這裡卡住了」唯一的線索。
const BASE_TITLE = (typeof document !== 'undefined' && document.title) || 'ZackLLMGUI';
let waitingN = 0;

function waitBadge(on) {
  waitingN = Math.max(0, waitingN + (on ? 1 : -1));
  document.title = (waitingN ? '● 等你回應 · ' : '') + BASE_TITLE;
}

// 工具跑起來動輒好幾分鐘，人早就切到別的分頁去了。回來才發現它十分鐘前
// 就停在一張確認卡上，那段時間是白等的。
// 只在**看不到這個分頁**的時候發 —— 人在看的話通知只是吵。
function notifyBg(text) {
  try {
    if (!document.hidden || typeof Notification === 'undefined') return;
    if (Notification.permission === 'granted') {
      new Notification('ZackLLMGUI', { body: text, tag: 'zackllmgui' });
    } else if (Notification.permission !== 'denied') {
      // 第一次才問。不主動在開頁時問 —— 那種彈窗沒有人想看
      Notification.requestPermission().then(function (p) {
        if (p === 'granted') new Notification('ZackLLMGUI', { body: text, tag: 'zackllmgui' });
      });
    }
  } catch (e) { /* 不支援就算了，這是加分不是必要 */ }
}
function saveConfig() {
  lsSet(LS_CONF, {
    host: S.host, model: S.model, theme: S.theme, think: S.think, fontScale: S.fontScale,
    userName: S.userName,
    showThink: S.showThink, params: S.params, tools: S.tools,
    provider: S.provider, oa: S.oa, paramsVersion: 3, tab: S.tab, auto: S.auto,
    sysChips: S.sysChips,
    hideSidebar: document.body.classList.contains('hide-sidebar'),
    hideParams: document.body.classList.contains('hide-params'),
    sideW: savedSideWidth('--side-w'), paramsW: savedSideWidth('--params-w'),
    // 工作區與這四個開關都是 serve.py 的**行程全域**，重啟就沒了。
    // 自動模式卻存在瀏覽器裡，所以不存這些的話會出現「全放開了但改不動檔案」。
    wsPath: S.ws.path || '',
    srv: { tools: !!S.srv.tools, write: !!S.ws.write,
           browser: !!S.srv.browser, sandbox: !!S.srv.sandbox }
  });
}

/* ══════════════════════ 版面渲染 ══════════════════════ */
function renderChatList() {
  const list = $('chatList');
  const needle = $('searchBox').value.trim().toLowerCase();
  list.innerHTML = '';
  S.chats.forEach(function (c) {
    let hit = '';
    if (needle && c.title.toLowerCase().indexOf(needle) < 0) {
      // 標題沒中就翻內文，翻到了把命中的片段放進 tooltip
      for (let i = 0; i < c.messages.length && !hit; i++) {
        const body = String(c.messages[i].content || '');
        const at = body.toLowerCase().indexOf(needle);
        if (at >= 0) hit = '…' + body.slice(Math.max(0, at - 30), at + 60).replace(/\s+/g, ' ') + '…';
      }
      if (!hit) return;
    }
    const item = document.createElement('button');
    item.className = 'chat-item' + (c.id === S.currentId ? ' active' : '');
    item.title = hit || '雙擊可以改名';
    item.innerHTML = '<span class="t"></span>'
      + '<span class="ren" title="重新命名">' + ico('pencil', 14) + '</span>'
      + '<span class="del" title="刪除">' + ico('trash', 14) + '</span>';
    item.querySelector('.t').textContent = c.title;
    item.addEventListener('click', function (e) {
      if (e.target.closest('.ren')) { renameChat(c.id); return; }
      if (e.target.closest('.del')) { deleteChat(c.id); return; }
      if (S.streaming || c.id === S.currentId) return;
      S.currentId = c.id;
      if (c.ws && c.ws !== S.ws.path) {          // 工作區跟著對話走
        applyWorkspace(c.ws).catch(function (e) { toast('切不過去：' + e.message); });
      }
      if (c.model && S.models.some(function (m) { return m.name === c.model; })) selectModel(c.model);
      renderChatList();
      renderThread();
      updateCtx();
      if (S.tab === 'hist') loadHistory();     // 還原點是跟著對話走的
    });
    item.addEventListener('dblclick', function (e) {
      e.preventDefault();
      renameChat(c.id);
    });
    list.appendChild(item);
  });
}

function deleteChat(id) {
  if (S.streaming) return;
  const c = S.chats.filter(function (x) { return x.id === id; })[0];
  if (!c || !confirm('確定刪除「' + c.title + '」？')) return;
  S.chats = S.chats.filter(function (x) { return x.id !== id; });
  if (!S.chats.length) { newChat(false); return; }
  if (S.currentId === id) {
    S.currentId = S.chats[0].id;
    renderThread();
    if (S.tab === 'hist') loadHistory();
  }
  renderChatList();
  if (useIdb) chatDrop(id).catch(function () { });   // 陣列裡沒了，資料庫裡也要沒
  saveChats();
}

function renderModelBtn() {
  const info = S.models.filter(function (m) { return m.name === S.model; })[0] || {};
  $('modelName').textContent = S.model || '尚未選擇模型';
  const bits = [];
  if (info.details && info.details.parameter_size) bits.push(info.details.parameter_size);
  if (info.size) bits.push(humanSize(info.size));
  const chip = $('modelSize');
  chip.hidden = !bits.length;
  chip.textContent = bits.join(' · ');
}

function selectModel(name) {
  S.model = name;
  const c = current();
  if (c) c.model = name;
  renderModelBtn();
  ensureCaps(name);
  saveConfig();
}

function msgEl(role) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  return el;
}

function buildUserMsg(m) {
  const el = msgEl('user');
  if (m.compacted || m.nudge || m.queued) el.classList.add('compacted');
  const b = document.createElement('div');
  b.className = 'bubble';
  b.textContent = m.text !== undefined ? m.text : m.content;
  el.appendChild(b);
  const bits = [];
  if (m.files && m.files.length) bits.push('附加檔案：' + m.files.join('、'));
  if (m.images && m.images.length) bits.push('附加 ' + m.images.length + ' 張圖片');
  if (bits.length) {
    const note = document.createElement('div');
    note.className = 'stats';
    note.style.alignSelf = 'flex-end';
    note.textContent = bits.join(' · ');
    el.appendChild(note);
  }
  return el;
}

function buildAssistantMsg(m, idx) {
  const el = msgEl('assistant');
  el.innerHTML =
    '<div class="msg-avatar">' + ico('node', 14, 2) + '</div>' +
    '<div class="msg-col">' +
      '<div class="msg-name"></div>' +
      '<div class="think" hidden>' +
        '<button class="think-head">' + ico('sparkle', 12, 2) +
          '<span class="tt">思考中</span>' +
          '<span class="think-dots"><i></i><i></i><i></i></span>' +
          '<span class="chev">' + ico('chevDown', 12, 2.2) + '</span>' +
        '</button>' +
        '<div class="think-body"></div>' +
      '</div>' +
      '<div class="msg-body"></div>' +
      '<div class="msg-foot" hidden><span class="stats"></span>' +
        '<span class="msg-actions">' +
          '<button class="icon-btn act-copy" title="複製回覆">' + ico('copy', 15) + '</button>' +
          '<button class="icon-btn act-regen" title="重新產生">' + ico('refresh', 15) + '</button>' +
          '<button class="icon-btn act-fork" title="從這裡分支出新對話">' + ico('branch', 15) + '</button>' +
        '</span>' +
      '</div>' +
    '</div>';
  el.querySelector('.msg-name').textContent = m.model || S.model || 'assistant';

  const think = el.querySelector('.think');
  think.querySelector('.think-head').addEventListener('click', function () {
    think.classList.toggle('open');
    think.querySelector('.chev').innerHTML =
      ico(think.classList.contains('open') ? 'chevDown' : 'chevRight', 12, 2.2);
  });
  el.querySelector('.act-copy').addEventListener('click', function () {
    copyText(el._content || '');
  });
  el.querySelector('.act-regen').addEventListener('click', regenerate);
  el.querySelector('.act-fork').addEventListener('click', function () {
    forkChat(idx === undefined ? -1 : idx);
  });
  return el;
}

// 工具結果在對話裡的樣子（確認卡按下之後就變成這個）
function buildToolMsg(m) {
  const el = msgEl('assistant');
  el.innerHTML =
    '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
    '<div class="msg-col"><div class="tool-card done">' +
      '<div class="th">工具 <span class="nm"></span><span class="st"></span>' +
      '<button class="fold" title="收折／展開">▼</button></div><pre></pre>' +
      '<div class="ta" hidden><button class="mini" data-undo>還原這個檔案</button>' +
      '<span class="st bk"></span></div>' +
    '</div></div>';
  // 工具輸出常常是幾十行，一頁塞三四張就看不到對話了。收折狀態存在訊息上，
  // 重新整理之後還在（saveChats 會把它一起存下去）。
  const card = el.querySelector('.tool-card');
  const fold = el.querySelector('.fold');
  const paint = function () {
    card.classList.toggle('fold', !!m.folded);
    fold.textContent = m.folded ? '▶' : '▼';
  };
  fold.addEventListener('click', function (e) {
    e.stopPropagation();
    m.folded = !m.folded;
    paint();
    saveChats();
  });
  paint();
  el.querySelector('.nm').textContent = m.tool_name || '';
  el.querySelector('.st').textContent = m.denied ? '· 已略過'
    : (m.failed ? '· 失敗' : (m.auto ? '· 自動執行' : '· 已執行'));
  el.querySelector('pre').textContent =
    (m.args ? JSON.stringify(m.args) + '\n\n' : '') + (m.content || '');
  // 有動到檔案的話，檔名做成連結，點了就在右邊打開（改過的直接進差異檢視）
  const path = (m.args || {}).path;
  if (path && !m.denied) {
    const link = document.createElement('button');
    link.className = 'mini';
    link.textContent = (m.backup ? '看差異 ' : '開啟 ') + path;
    link.addEventListener('click', function () {
      openFile(path, m.backup || '');
      if (m.backup) { S.fv.mode = 'diff'; renderFileView(); }
    });
    el.querySelector('.th').appendChild(link);
  }
  if (m.backup) {
    const bar = el.querySelector('.ta');
    bar.hidden = false;
    bar.querySelector('.bk').textContent = m.backup;
    bar.querySelector('[data-undo]').addEventListener('click', function (e) {
      restoreBackup(m.backup, e.target);
    });
  }
  return el;
}

async function restoreBackup(mark, btn) {
  btn.disabled = true;
  try {
    const res = await fetch(apiUrl('/restore'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ backup: mark })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    toast('已還原 ' + data.restored);
  } catch (e) {
    toast('還原失敗：' + e.message);
    btn.disabled = false;
  }
}

function fillAssistant(el, m) {
  el._content = m.content || '';
  const think = el.querySelector('.think');
  if (m.thinking && S.showThink) {
    think.hidden = false;
    think.classList.remove('open');
    think.querySelector('.tt').textContent = m.seconds ? '已思考 ' + m.seconds.toFixed(1) + ' 秒' : '思考過程';
    think.querySelector('.think-dots').style.display = 'none';
    think.querySelector('.chev').innerHTML = ico('chevRight', 12, 2.2);
    think.querySelector('.think-body').textContent = m.thinking.trim();
  } else {
    think.hidden = true;
  }
  let html = renderMarkdown(m.content || '');
  if (m.tool_calls && m.tool_calls.length && !(m.content || '').trim()) {
    html = '<p class="stats">要求執行 ' + m.tool_calls.map(function (t) {
      return esc(((t || {}).function || {}).name || '?');
    }).join('、') + '</p>';
  }
  // 模型若真的回傳圖片（Ollama 目前的文字模型不會，但介面先接住）
  if (m.images && m.images.length) {
    html += m.images.map(function (b64) {
      return '<img src="data:image/png;base64,' + b64 + '" style="max-width:100%; border-radius:10px; margin-top:8px;">';
    }).join('');
  }
  el.querySelector('.msg-body').innerHTML = html;
  linkifyFileRefs(el.querySelector('.msg-body'));
  if (m.stats) {
    el.querySelector('.msg-foot').hidden = false;
    el.querySelector('.stats').textContent = m.stats;
  }
  paintTurn(el, m.turn);
}

// 一輪的總計。掛在訊息的 footer 上而不是另外開一塊 —— 它是那一輪的結尾，
// 位置就該在那裡。重整頁面之後也要還在，所以存在訊息上。
function paintTurn(el, turn) {
  let box = el.querySelector('.turn');
  if (!turn) { if (box) box.remove(); return; }
  const foot = el.querySelector('.msg-foot');
  foot.hidden = false;
  if (!box) {
    box = document.createElement('span');
    box.className = 'turn';
    foot.appendChild(box);
  }
  box.textContent = turnLine(turn);
}

// 一輪真的結束了：把總計記到最後一則助理訊息上。
// **只在有跑過工具的時候記** —— 純聊天那一則的統計本來就寫了總計幾秒，
// 再加一行是重複的。長任務才是這一行存在的理由。
function markTurnDone(c) {
  const r = S.run;
  if (!c || !r || !r.t0 || !r.rounds) return;
  const turn = { ms: Math.round(performance.now() - r.t0),
                 rounds: r.rounds, calls: r.calls, tokens: r.tokens };
  r.t0 = 0;                                   // 只記一次
  stopRunTicker();                            // 沒有秒數要走了，別讓它每秒空轉
  for (let i = c.messages.length - 1; i >= 0; i--) {
    if (c.messages[i].role !== 'assistant') continue;
    c.messages[i].turn = turn;
    saveChats();
    const els = $('thread').querySelectorAll('.msg.assistant');
    if (els.length) paintTurn(els[els.length - 1], turn);
    return;
  }
}

function renderThread() {
  const t = $('thread');
  t.innerHTML = '';
  const c = current();
  if (!c || !c.messages.length) {
    t.innerHTML = '<div class="empty">開始新的對話<br>在下方輸入訊息，按 Enter 送出</div>';
    return;
  }
  c.messages.forEach(function (m, i) {
    if (m.role === 'user') t.appendChild(buildUserMsg(m));
    else if (m.role === 'tool') t.appendChild(buildToolMsg(m));
    else { const el = buildAssistantMsg(m, i); fillAssistant(el, m); t.appendChild(el); }
  });
  S.stick = true;
  renderCompactBtns();
  renderResumeBar();          // 換對話、重整頁面之後也要看得到
  renderQueue();
  pin();
}

function pin() {
  if (S.stick) { const s = $('scroll'); s.scrollTop = s.scrollHeight; }
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { toast('已複製到剪貼簿'); },
      function () { toast('複製失敗'); });
  } else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast('已複製到剪貼簿'); } catch (e) { toast('複製失敗'); }
    document.body.removeChild(ta);
  }
}

/* ══════════════════════ 送出與串流 ══════════════════════ */
// Ollama 的串流都是 NDJSON。onObj 回傳 false 就收工，呼叫端不必自己拆行。
async function streamNdjson(path, payload, signal, onObj) {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: signal || undefined
  });
  if (!res.ok) {
    const txt = await res.text();
    let e = txt;
    try { e = JSON.parse(txt).error || txt; } catch (x) { /* 非 JSON */ }
    throw new Error(e);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buf += decoder.decode(chunk.value, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let obj;
      try { obj = JSON.parse(line); } catch (e) { continue; }
      if (obj.error) throw new Error(obj.error);
      if (onObj(obj) === false) {
        try { reader.cancel(); } catch (e) { /* 已經關了 */ }
        return;
      }
    }
  }
}

// context 快滿的時候，把較早的工具輸出換成一行。它們佔最多、重讀價值最低 ——
// 模型真的需要可以再呼叫一次，對話本身則一個字都不動。
//
// 為什麼要自動做：工具跑起來是連續二十幾輪沒人在看用量條的情境，滿了之後
// Ollama 會靜靜地從最前面截掉，症狀是「模型突然忘記自己在幹嘛」，不會報錯。
// 這是最會浪費時間的失敗模式，因為它看起來像模型變笨。
//
// 只動送出去的那一份（apiMessages 本來就是重建的），所以對話紀錄、還原點、
// 匯出的內容全都保留原樣。
const SQUEEZE_AT = 0.8;         // 用到這個比例才開始省
const SQUEEZE_TO = 0.7;         // 省到掉回這裡就停，不必一次清光
const SQUEEZE_KEEP = 3;         // 最後幾則工具輸出留原文（剛做完的事還要用）
const SQUEEZE_MIN = 400;        // 比這個短的輸出，省下來也沒差

function squeezeTools(msgs) {
  const limit = ctxLimit();
  const cost = function (m) {
    return estTokens(m.content) + (m.images ? 800 * m.images.length : 0);
  };
  let used = Math.round(msgs.reduce(function (n, m) { return n + cost(m); }, 0) * S.ctxRatio);
  S.run.squeezed = 0;
  if (used < limit * SQUEEZE_AT) return msgs;

  const idx = [];
  msgs.forEach(function (m, i) { if (m.role === 'tool') idx.push(i); });
  for (let k = 0; k < idx.length - SQUEEZE_KEEP && used > limit * SQUEEZE_TO; k++) {
    const m = msgs[idx[k]];
    const body = String(m.content || '');
    if (body.length < SQUEEZE_MIN) continue;
    const stub = '（較早的 ' + (m.tool_name || '工具') + ' 輸出共 ' + body.length +
      ' 字，為了留下 context 已省略。需要的話再呼叫一次。）';
    used -= Math.round((estTokens(body) - estTokens(stub)) * S.ctxRatio);
    msgs[idx[k]] = Object.assign({}, m, { content: stub });
    S.run.squeezed += 1;
  }
  if (S.run.squeezed) renderRunBar();
  return msgs;
}

function apiMessages(c) {
  const out = [];
  const sys = [($('system').value || '').trim(), agentRules()]
    .filter(Boolean).join('\n\n');
  if (sys) out.push({ role: 'system', content: sys });
  c.messages.forEach(function (m, i) {
    const item = { role: m.role, content: m.content };
    if (m.images && m.images.length) item.images = m.images;
    // 後面沒有接工具結果的 tool_calls 就別送（例如關掉分頁前剛好停在確認卡），
    // 不然 Ollama 會抱怨 tool call 沒有回應
    const answered = (c.messages[i + 1] || {}).role === 'tool';
    if (m.tool_calls && m.tool_calls.length && answered) item.tool_calls = m.tool_calls;
    if (m.role === 'tool' && m.tool_name) item.tool_name = m.tool_name;
    out.push(item);
  });
  return squeezeTools(out);
}

// Enter 走這裡：跑到一半就排隊，沒在跑就正常送出。
// **不能直接呼叫 send()** —— send() 在串流中是「停止」，Enter 會變成按停止鍵。
function submitFromInput() {
  const text = $('input').value.trim();
  if (S.streaming || S.blocked === RUNNING_HINT) {
    if (!text) return;
    queueMessage(text);
    $('input').value = '';
    autoGrow();
    return;
  }
  send();
}

async function send() {
  if (S.streaming) { stopStream(); return; }
  const text = $('input').value.trim();
  // 「連不上」放行 —— 讓重試迴圈去處理，它會顯示倒數也可以按停止放棄
  if (!text || (S.blocked && S.blocked !== CONN_HINT)) return;
  if (!S.model) { toast('請先連線並選擇一個模型'); return; }

  const c = current();
  const msg = { role: 'user', content: text };
  if (S.files.length) {
    // 檔案內容放前面、問題放後面，模型比較不會忘記問題是什麼
    msg.content = S.files.map(function (f) { return fenceFor(f.name, f.text); }).join('\n\n') +
      '\n\n' + text;
    msg.text = text;                                        // 泡泡只顯示打的字
    msg.files = S.files.map(function (f) { return f.name + ' · ' + f.text.length + ' 字'; });
  }
  if (S.images.length) msg.images = S.images.map(function (i) { return i.data; });

  // 空白提示還在的話得整個重畫（那次重畫已經包含剛推進去的訊息），
  // 否則只要接一則上去就好——兩邊都做會讓第一則訊息出現兩次。
  const wasEmpty = !!$('thread').querySelector('.empty');
  c.messages.push(msg);
  if (wasEmpty) renderThread();
  else $('thread').appendChild(buildUserMsg(msg));

  $('input').value = '';
  S.run = { rounds: 0, calls: 0, tokens: 0, squeezed: 0, fails: {},
            wroteTests: '', ranTests: false, nagged: false,
            t0: performance.now() };
  delete c.stopWhy;              // 上一輪停在哪裡，跟這一輪沒關係了
  S.queued = [];                 // 新的一輪，上一輪沒送出的插話不留著
  renderRunBar();
  autoGrow();
  S.images = [];
  S.files = [];
  renderAttach();
  if (c.messages.length === 1) { autoTitle(c); renderChatList(); }
  S.stick = true;
  pin();
  await runStream(c);
  finishTurn();
}

async function regenerate() {
  if (S.streaming) return;
  const c = current();
  while (c.messages.length && c.messages[c.messages.length - 1].role !== 'user') c.messages.pop();
  if (!c.messages.length) return;
  renderThread();
  await runStream(c);
  finishTurn();
}

function setStreaming(on) {
  S.streaming = on;
  const btn = $('sendBtn');
  btn.classList.toggle('stop', on);
  btn.innerHTML = ico(on ? 'stop' : 'send', 17, on ? 2 : 2.4);
  btn.title = on ? '停止產生' : '送出';
  btn.disabled = on ? false : !!S.blocked;
  $('hint').textContent = on ? '產生中…' : (S.blocked || '');
  renderResumeBar();          // 開始跑就收起來，停下來就自己冒出來
}

function stopStream() { if (S.abort) S.abort.abort(); }

// 重試前的倒數。畫在訊息本體裡，因為那是使用者正在看的地方；
// 只寫一行「重試中」而不倒數的話，跟當掉看起來一模一樣。
function countdown(bodyEl, ms, attempt, why) {
  return new Promise(function (resolve, reject) {
    const until = Date.now() + ms;
    const tick = function () {
      if (S.abort && S.abort.signal.aborted) {
        clearInterval(timer);
        reject(Object.assign(new Error('已停止'), { name: 'AbortError' }));
        return;
      }
      const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
      bodyEl.innerHTML = '<div class="err-card"><span class="ic">' + ico('alert', 18, 2) + '</span>'
        + '<div><div class="t"></div><div class="d"></div></div></div>';
      bodyEl.querySelector('.t').textContent = why;
      bodyEl.querySelector('.d').textContent =
        left + ' 秒後重試（第 ' + attempt + '/' + (RETRY_MAX - 1) + ' 次）· 按停止可以放棄';
      if (left <= 0) { clearInterval(timer); resolve(); }
    };
    const timer = setInterval(tick, 250);
    tick();
  });
}

// 跑到一半使用者又打字：不中斷，排進佇列，**下一次送模型時夾帶過去**。
//
// 這是 Claude Code 的做法（binary 裡的 queuedMessages 與那句寫死的
// "The user sent a new message while you were working:"）—— 它把插話當成
// 「邊跑邊修方向」而不是打斷，所以已經跑到一半的工具不會白費。
//
// 另外兩條路都比較差：鎖住輸入框（原本的做法）等於長任務跑十分鐘只能乾等；
// 打字就立刻中斷則會浪費正在跑的工具。
function queueMessage(text) {
  S.queued = S.queued || [];
  S.queued.push(text);
  renderQueue();
  toast('已排隊，這一輪跑完就送出');
}

function renderQueue() {
  const q = S.queued || [];
  $('queueBar').hidden = !q.length;
  if (q.length) {
    $('queueWhy').textContent = '排隊中'
      + (q.length > 1 ? '（' + q.length + ' 則）' : '') + '：' + q.join(' / ');
  }
}

// 把排隊的話接進對話裡。回傳有沒有東西可送。
// 標成 queued 之後畫面上會用跟壓縮摘要一樣的灰底，看得出來那是插話。
function flushQueue(c) {
  const q = S.queued || [];
  if (!q.length || !c) return false;
  const text = q.join('\n');
  S.queued = [];
  renderQueue();
  const msg = { role: 'user', queued: true, text: '（跑到一半補充）' + text, content: text };
  c.messages.push(msg);
  saveChats();
  $('thread').appendChild(buildUserMsg(msg));
  pin();
  return true;
}

// 中斷之後要不要給「繼續」。回傳一句說明，或空字串代表沒得續。
//
// 能續是因為**中斷的東西都留在對話裡**：finishStream 會把已經吐出來的半截
// 內容寫進 c.messages，工具結果本來就一則一則存著。所以「繼續」不是什麼
// 特殊的 resume —— 就是拿同一份 messages 再送一次，模型會從最後那則接下去。
// （實測 Ollama 收到結尾是半截 assistant 訊息時會續寫，不是重講一遍。）
function resumeReason(c) {
  if (!c || S.streaming || !c.messages.length) return '';
  if (c.stopWhy) return c.stopWhy;        // 輪數或預算用完，按「繼續」就重新算一段
  const last = c.messages[c.messages.length - 1];
  if (last.role === 'tool') return '工具跑完就停住了 —— 模型還沒接話';
  if (last.role === 'assistant' && String(last.stats || '').indexOf('已停止') >= 0) {
    return last.content || last.thinking ? '上次寫到一半被停下來' : '上次還沒開始寫就被停下來';
  }
  return '';
}

function renderResumeBar() {
  const why = resumeReason(current());
  $('resumeBar').hidden = !why;
  if (why) $('resumeWhy').textContent = why;
}

async function resumeRun() {
  const c = current();
  if (!resumeReason(c)) return;
  $('resumeBar').hidden = true;
  delete c.stopWhy;
  // 輪數與預算都重新算：中斷之後接著跑本來就是新的一段，
  // 不重算的話按下「繼續」會立刻又撞到同一個上限，變成一顆沒有作用的按鈕。
  S.run = { rounds: 0, calls: 0, tokens: 0, squeezed: 0, fails: {},
            wroteTests: '', ranTests: false, nagged: false, t0: performance.now() };
  await runStream(c, 0);
  renderResumeBar();
}

async function runStream(c, depth) {
  const el = buildAssistantMsg({ model: S.model }, -1);
  $('thread').appendChild(el);
  const thinkEl = el.querySelector('.think');
  const thinkBody = el.querySelector('.think-body');
  const bodyEl = el.querySelector('.msg-body');

  const payload = { model: S.model, messages: apiMessages(c), stream: true };
  const note = roundsNote(depth || 0);
  if (note) payload.messages.push({ role: 'user', content: note });
  const think = thinkValue();
  if (think !== null) payload.think = think;      // 不支援時整個欄位省略
  const opts = buildOptions();
  if (Object.keys(opts).length) payload.options = opts;
  const keep = ($('keep_alive').value || '').trim();
  if (keep) payload.keep_alive = keep;
  if (toolsReady()) payload.tools = toolDefs();
  S.lastEst = rawEstimate('');            // 拿來跟真實的 prompt_eval_count 對帳

  let content = '', thinking = '', dirty = false, done = null;
  let toolCalls = [], images = [];
  const t0 = performance.now();
  let firstToken = null;
  let retrying = false;              // 重試倒數自己在畫 bodyEl，這時不要跟它搶

  // 第一個字還沒到、或思考的字都吐在 think 裡（「顯示思考」關著時一個字都看不見）
  // 的那段時間，畫面上要看得到「還在跑，跑了多久」。
  // 之前是一個游標閃在空白畫面上 —— 分不出「在想」跟「當掉」。
  const waitLine = function () {
    const st = waitText(performance.now() - t0, thinking, S.showThink);
    if (!st) { bodyEl.innerHTML = '<span class="caret"></span>'; return; }
    bodyEl.innerHTML = '<span class="wait"></span>';
    bodyEl.firstChild.textContent = st;
  };
  waitLine();

  let lastRender = 0;
  const flush = function () {
    if (!content && !retrying) waitLine();   // 秒數要自己走，不能只在收到字時更新
    if (!dirty) return;
    dirty = false;
    if (thinking && S.showThink) {
      if (thinkEl.hidden) {
        thinkEl.hidden = false;
        thinkEl.classList.add('open');
      }
      thinkBody.textContent = thinking.trim();
    }
    if (content) {
      if (thinkEl.classList.contains('open') && !thinkEl._closed) {
        thinkEl._closed = true;
        thinkEl.classList.remove('open');
        thinkEl.querySelector('.tt').textContent =
          '已思考 ' + ((performance.now() - t0) / 1000).toFixed(1) + ' 秒';
        thinkEl.querySelector('.think-dots').style.display = 'none';
        thinkEl.querySelector('.chev').innerHTML = ico('chevRight', 12, 2.2);
      }
      const now = performance.now();
      if (content.length < BIG_MSG || now - lastRender > BIG_MSG_MS) {
        lastRender = now;
        bodyEl.innerHTML = renderMarkdown(content) + '<span class="caret"></span>';
      }
    }
    pin();
  };
  const timer = setInterval(flush, 60);

  S.abort = new AbortController();
  setStreaming(true);

  try {
    // 連不上就等一下再試。**只在一個字都還沒收到時重試** —— 已經吐出東西再重試
    // 會重複一段。等待時把倒數畫在訊息裡，不然畫面看起來就是停在那裡不動。
    for (let attempt = 1; ; attempt++) {
      try {
        await chatStream(payload, S.abort.signal, {
          think: function (t) { firstToken = firstToken || performance.now() - t0; thinking += t; dirty = true; },
          content: function (t) { firstToken = firstToken || performance.now() - t0; content += t; dirty = true; },
          images: function (im) { images = images.concat(im); },
          tools: function (tc) { toolCalls = toolCalls.concat(tc); },
          done: function (info) { done = info; }
        });
        break;
      } catch (err) {
        const got = !!(content || thinking || toolCalls.length || done);
        if (attempt >= RETRY_MAX || !isRetryable(err, got)) throw err;
        retrying = true;
        try { await countdown(bodyEl, RETRY_BASE_MS * attempt, attempt, friendlyError(err).msg); }
        finally { retrying = false; }
      }
    }
  } catch (err) {
    clearInterval(timer);
    setStreaming(false);
    S.abort = null;
    const f = friendlyError(err);
    if (f.abort) {
      finishStream(c, el, content, thinking, '（已停止）', t0);
      markTurnDone(c);          // 中途停掉也要看得到已經花了多久
      return;
    }
    thinkEl.hidden = !thinking || !S.showThink;
    bodyEl.innerHTML = '<div class="err-card"><span class="ic">' + ico('alert', 18, 2) + '</span>' +
      '<div><div class="t"></div><div class="d"></div></div></div>';
    bodyEl.querySelector('.t').textContent = f.msg;
    bodyEl.querySelector('.d').textContent = f.hint || errorHint(f.msg);
    pin();
    return;
  }

  clearInterval(timer);
  setStreaming(false);
  S.abort = null;

  let stats = '（已停止）';
  if (done) {
    const total = (performance.now() - t0) / 1000;
    const ec = done.eval_count || 0;
    const ed = (done.eval_duration || 0) / 1e9;
    const bits = [(done.prompt_eval_count || 0) + ' prompt tokens', ec + ' tokens'];
    if (ed > 0) bits.push((ec / ed).toFixed(1) + ' tok/s');
    if (firstToken) bits.push('首字 ' + (firstToken / 1000).toFixed(2) + 's');
    bits.push('總計 ' + total.toFixed(1) + 's');
    stats = bits.join('  ·  ');
    S.run.tokens += (done.prompt_eval_count || 0) + ec;
    renderRunBar();
    // 用真實的 prompt token 數校正估算，下一次的用量條就會準得多
    if (done.prompt_eval_count && S.lastEst > 0) {
      const ratio = done.prompt_eval_count / S.lastEst;
      if (ratio > 0.3 && ratio < 4) S.ctxRatio = ratio;
    }
  }
  finishStream(c, el, content, thinking, stats, t0, toolCalls, images, done);
  if (toolCalls.length) { await runTools(c, toolCalls, (depth || 0) + 1); return; }

  // 模型不再呼叫工具＝它認為做完了。這裡是整個迴圈唯一的出口。
  // done 是假的代表使用者按了停止或連線斷了，那種情況不該再推它繼續。
  if (!done || (depth || 0) >= MAX_TOOL_ROUNDS) { markTurnDone(c); return; }

  // 排隊的插話優先：使用者剛講的話比自動檢查重要
  if (flushQueue(c)) { await runStream(c, (depth || 0) + 1); return; }

  const nag = finishCheck(S.run);
  if (!nag) { markTurnDone(c); return; }
  S.run.nagged = true;
  const msg = { role: 'user', nudge: true, text: '（自動檢查）' + nag, content: nag };
  c.messages.push(msg);
  saveChats();
  $('thread').appendChild(buildUserMsg(msg));
  pin();
  await runStream(c, (depth || 0) + 1);
}

// 模型什麼都沒吐出來的時候，要說得出「為什麼」。
// 之前這種情況是直接吞掉的：畫面上只多一行 tok/s 統計，然後什麼都沒有 ——
// 使用者完全不知道是 context 爆了、num_predict 太小、還是模型真的沒話說。
// 這裡不猜單一原因，把能分辨的數字都攤出來讓人自己判斷。
function emptyReplyNote(done) {
  if (!done) return '模型沒有回傳內容，連線可能中斷了。';
  const limit = ctxLimit();
  const prompt = done.prompt_eval_count || 0;
  const evaled = done.eval_count || 0;
  const reason = done.done_reason || '（沒說）';
  const bits = ['模型這一輪沒有輸出任何內容。'];

  if (reason === 'length') {
    bits.push('停止原因是 length —— 撞到 num_predict 的上限。把它調大或設成 -1。');
  } else if (evaled === 0) {
    bits.push('它連一個 token 都沒有產生（eval_count = 0），也就是一開始就送出結束符號。');
  }
  if (prompt >= limit * 0.95) {
    bits.push('送進去的 prompt 是 ' + fmtK(prompt) + '，num_ctx 只有 ' + fmtK(limit)
      + ' —— 幾乎塞滿了。Ollama 會從最前面截掉，模型很可能連問題都沒看到。'
      + '調大 num_ctx，或用壓縮鍵把較早的訊息收成摘要。');
  }
  const cap = S.ctxMax[S.model] || 0;
  if (cap && limit > cap) {
    bits.push('另外 num_ctx 填的比 ' + S.model + ' 支援的還大（最多 '
      + fmtK(cap) + '），多填的部分沒有作用。');
  }
  if (bits.length === 1) {
    bits.push('停止原因：' + reason + '　prompt ' + fmtK(prompt)
      + '　產生 ' + fmtK(evaled) + '。');
    bits.push('這通常是模型端的問題：換個問法、換個模型，或把 temperature 調高一點試試。'
      + '也可能是停止字串（stop）在第一個 token 就命中了。');
  }
  return bits.join('\n');
}

function finishStream(c, el, content, thinking, stats, t0, toolCalls, images, done) {
  clearTimeout(el._t);
  const seconds = thinking ? (performance.now() - t0) / 1000 : 0;
  const record = {
    role: 'assistant', content: content, thinking: thinking,
    model: S.model, stats: stats, seconds: seconds
  };
  if (toolCalls && toolCalls.length) record.tool_calls = toolCalls;
  if (images && images.length) record.images = images;
  fillAssistant(el, record);
  if (content || thinking || record.tool_calls) {
    c.messages.push(record);
    saveChats();
  } else {
    // 什麼都沒有：講清楚發生什麼事，但**不要寫進對話** ——
    // 那是介面的說明，不是模型說的話，塞進去只會污染下一輪的 context。
    const note = msgEl('assistant');
    note.innerHTML = '<div class="msg-avatar">' + ico('alert', 14, 2) + '</div>'
      + '<div class="msg-col"><div class="tool-card done">'
      + '<div class="th">沒有輸出</div><pre></pre></div></div>';
    note.querySelector('pre').textContent = emptyReplyNote(done);
    el.replaceWith(note);
  }
  setStreaming(false);
  S.abort = null;
  updateCtx();
  pin();
  $('input').focus();
}

/* ══════════════════════ 兩種後端的統一入口 ══════════════════════ */
// 上層只認 on.content / on.think / on.tools / on.images / on.done，
// 底下是 Ollama 的 NDJSON 還是 OpenAI 的 SSE 由這裡吸收。
async function chatStream(payload, signal, on) {
  if (S.provider !== 'openai') {
    await streamNdjson('/api/chat', payload, signal, function (obj) {
      const m = obj.message || {};
      if (m.thinking) on.think(m.thinking);
      if (m.content) on.content(m.content);
      if (m.images && m.images.length) on.images(m.images);
      if (m.tool_calls && m.tool_calls.length) on.tools(m.tool_calls);
      if (obj.done) { on.done(obj); return false; }
    });
    return;
  }

  const o = payload.options || {};
  const body = {
    model: payload.model,
    messages: oaMsgs(payload.messages),
    stream: true,
    stream_options: { include_usage: true }
  };
  if (payload.tools && payload.tools.length) body.tools = payload.tools;
  if (o.temperature !== undefined) body.temperature = o.temperature;
  if (o.top_p !== undefined) body.top_p = o.top_p;
  if (o.seed !== undefined) body.seed = o.seed;
  if (o.stop) body.stop = o.stop;
  if (o.num_predict !== undefined) body.max_tokens = o.num_predict;

  let info = null;
  // Ollama 的 NDJSON 一次給完整的 tool_calls，SSE 不是：一支工具的 arguments
  // 會被切成十幾片分批送過來，只能按 delta.tool_calls[].index 自己拼回去。
  // 拼不回去的症狀是模型「呼叫了工具但參數是半截 JSON」。
  const parts = [];
  await streamSse('/chat/completions', body, signal, function (obj) {
    const ch = (obj.choices || [])[0] || {};
    const d = ch.delta || {};
    // DeepSeek 與部分 vLLM 會把思考內容放在 reasoning_content
    if (d.reasoning_content || d.reasoning) on.think(d.reasoning_content || d.reasoning);
    if (d.content) on.content(d.content);
    (d.tool_calls || []).forEach(function (t) {
      const i = t.index || 0;
      const cur = parts[i] || (parts[i] =
        { id: '', type: 'function', function: { name: '', arguments: '' } });
      if (t.id) cur.id = t.id;
      const fn = t.function || {};
      // 都用累加：name 通常第一片就給完，但也有服務會拆開送
      if (fn.name) cur.function.name += fn.name;
      if (fn.arguments) cur.function.arguments += fn.arguments;
    });
    if (obj.usage) {
      info = {
        prompt_eval_count: obj.usage.prompt_tokens || 0,
        eval_count: obj.usage.completion_tokens || 0
      };
    }
  });
  const calls = parts.filter(function (t) { return t && t.function.name; });
  if (calls.length) on.tools(calls);
  on.done(info || {});
}

// 單次、不串流的呼叫，壓縮摘要用
async function once(prompt, timeoutMs) {
  if (S.provider === 'openai') {
    const data = await oaJson('/chat/completions', {
      model: S.model, messages: [{ role: 'user', content: prompt }], stream: false
    }, timeoutMs || 300000);
    return (((data.choices || [])[0] || {}).message || {}).content || '';
  }
  const body = { model: S.model, messages: [{ role: 'user', content: prompt }], stream: false };
  if (thinkValue() !== null) body.think = false;         // 摘要不需要思考，省時間
  const data = await apiJson('/api/chat', body, timeoutMs || 300000);
  return (data.message || {}).content || '';
}

/* ══════════════════════ 壓縮對話 ══════════════════════ */
const COMPACT_KEEP = 4;         // 最後幾則保留原文

function transcriptOf(msgs) {
  return msgs.map(function (m) {
    const who = m.role === 'user' ? '使用者'
      : (m.role === 'tool' ? ('工具 ' + (m.tool_name || '')) : '助理');
    return who + '：' + String(m.content || '').slice(0, 8000);
  }).join('\n\n');
}

// 摘要蓋不掉的東西。待辦清單是現在進行式，背景指令的 id 錯一個字就收不回來 ——
// 這兩樣讓模型「用自己的話轉述」等於丟掉，所以原樣接在摘要後面。
function carryOver() {
  const out = [];
  const todos = (S.todos || []).filter(function (t) { return !t.done; });
  if (todos.length) {
    out.push('還沒做完的待辦：\n' + todos.map(function (t, i) {
      return (i + 1) + '. ' + t.text;
    }).join('\n'));
  }
  const jobs = (S.jobs || []).filter(function (j) { return j.code === null; });
  if (jobs.length) {
    out.push('還在背景跑的指令（用 check_job 收）：\n' + jobs.map(function (j) {
      return j.id + '：' + j.cmd;
    }).join('\n'));
  }
  return out.length ? '\n\n以下不是摘要，是現在的實際狀態：\n\n' + out.join('\n\n') : '';
}

// 壓縮是一次完整的模型呼叫，長對話上要等幾十秒 —— 而原本是點下去才開始算，
// 那幾十秒整個介面停在「壓縮中…」。放著讓它自己跑的時候這一停最難受。
// 用量一過門檻就先在背景算好，真的要壓時直接拿現成的。
// 存在 S 而不是對話物件上：Promise 進了 localStorage 會變成 {}，
// 重整之後那個 {} 是 truthy 的，會被當成「算好了」然後炸掉。
function preCompact() {
  const c = current();
  // 不在產生中才算：本機通常只有一張顯示卡，兩個生成搶同一張卡
  // 只會讓正在跑的那個變慢。工具跑指令的空檔就夠算完了。
  if (!c || !S.model || S.streaming || S.pre) return;
  if (c.messages.length <= COMPACT_KEEP + 1) return;
  const n = c.messages.length - COMPACT_KEEP;
  const p = once(COMPACT_PROMPT + transcriptOf(c.messages.slice(0, n)));
  S.pre = { id: c.id, n: n, p: p };
  p.catch(function () { S.pre = null; });     // 算失敗就當作沒算過，點下去再算一次
}

async function compactChat() {
  if (S.streaming) { toast('正在產生回覆，等一下再壓縮'); return; }
  if (!S.model) { toast('請先連線並選擇模型'); return; }
  const c = current();
  if (c.messages.length <= COMPACT_KEEP + 1) { toast('對話還太短，不需要壓縮'); return; }

  // 背景算好的那一份只有在「算的時候是這場對話、而且訊息只增沒減」時才作數
  const pre = S.pre && S.pre.id === c.id && c.messages.length >= S.pre.n + COMPACT_KEEP
    ? S.pre : null;
  const cut = pre ? pre.n : c.messages.length - COMPACT_KEEP;
  const head = c.messages.slice(0, cut);
  const tail = c.messages.slice(cut);

  const before = head.reduce(function (n, m) { return n + estTokens(m.content); }, 0);

  blockComposer('壓縮中…');
  if (!pre) toast('壓縮中，長對話可能要等一下…');
  try {
    const summary = (await (pre ? pre.p : once(COMPACT_PROMPT + transcriptOf(head)))).trim();
    if (!summary) throw new Error('模型沒有回傳內容');
    c.preCompact = c.messages;              // 留一步可還原，摘要不理想時不會全毀
    c.messages = [{
      role: 'user', compacted: head.length,
      text: '（已壓縮先前 ' + head.length + ' 則訊息為摘要）',
      content: '以下是這場對話先前內容的摘要，請以它作為上下文繼續：\n\n'
        + summary + carryOver()
    }].concat(tail);
    saveChats();
    renderThread();
    updateCtx();
    // 省了多少才是使用者關心的事：壓縮要等、也會丟掉細節，值不值得看這個數字
    const saved = Math.round((before - estTokens(summary)) * S.ctxRatio);
    toast('已把 ' + head.length + ' 則訊息壓成摘要，省下 ' + fmtK(saved) + ' tokens');
  } catch (e) {
    toast('壓縮失敗：' + friendlyError(e).msg);
  } finally {
    S.pre = null;               // 用掉了或壞掉了，都不能再拿來壓第二次
    blockComposer('');
    renderCompactBtns();
  }
}

function uncompact() {
  const c = current();
  if (!c.preCompact) { toast('沒有可以還原的壓縮'); return; }
  c.messages = c.preCompact;
  delete c.preCompact;
  saveChats();
  renderThread();
  updateCtx();
  renderCompactBtns();
  toast('已還原壓縮前的對話');
}

function renderCompactBtns() {
  const c = current();
  const btn = $('compactBtn');
  btn.disabled = !c || c.messages.length <= COMPACT_KEEP + 1;
  btn.classList.toggle('on', !!(c && c.preCompact));
  btn.title = '壓縮對話 · ' + (S.ctxLabel || '') +
    (c && c.preCompact ? '（壓縮過了，⋯ 選單可還原）' : '');
}



