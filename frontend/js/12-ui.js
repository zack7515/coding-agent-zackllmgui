// 版面外框：對話分支、彈出選單、側欄拖寬、主題、連線設定、系統用量。

/* ══════════════════════ 分支 ══════════════════════ */
function forkChat(idx) {
  const c = current();
  if (!c || !c.messages.length) { toast('這個對話還沒有內容'); return; }
  const upto = idx < 0 ? c.messages.length : idx + 1;
  const copy = {
    id: uid(), title: c.title.replace(/（分支.*）$/, '') + '（分支）', model: c.model,
    renamed: true, created: Date.now(),
    messages: JSON.parse(JSON.stringify(c.messages.slice(0, upto)))
  };
  S.chats.unshift(copy);
  S.currentId = copy.id;
  renderChatList();
  renderThread();
  saveChats();
  toast('已分支出新對話（' + upto + ' 則訊息）');
}

function renameChat(id) {
  const c = S.chats.filter(function (x) { return x.id === id; })[0];
  if (!c) return;
  const name = (prompt('對話名稱', c.title) || '').trim();
  if (!name) return;
  c.title = name;
  c.renamed = true;          // 別再被第一則訊息蓋掉
  renderChatList();
  saveChats(c);              // 改的不一定是當下開著的那則，要指名

}

/* ══════════════════════ 選單 ══════════════════════ */
let openMenu = null;
function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
document.addEventListener('click', function (e) {
  if (openMenu && !openMenu.contains(e.target)) closeMenu();
});

function showMenu(anchor, items) {
  closeMenu();
  const menu = document.createElement('div');
  menu.className = 'menu';
  items.forEach(function (it) {
    if (it === '-') { menu.appendChild(document.createElement('hr')); return; }
    const b = document.createElement('button');
    b.innerHTML = '<span></span>' + (it.meta ? '<span class="m">' + esc(it.meta) + '</span>' : '');
    b.querySelector('span').textContent = (it.checked ? '● ' : '') + it.label;
    // 這一行的 stopPropagation 不能拿掉：底下 document 的「點外面就關」監聽器
    // 會在事件冒泡上去時看到 action() 剛開好的子選單，然後把它關掉
    // （按鈕本身已經被移除，contains(target) 永遠是 false）。
    b.addEventListener('click', function (e) { e.stopPropagation(); closeMenu(); it.action(); });
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  // 按鈕在畫面下半部時（輸入框旁邊那幾個），往下開會整個掉到視窗外
  const h = menu.offsetHeight;
  const below = window.innerHeight - r.bottom - 12;
  menu.style.top = (h <= below || r.top < h + 12
    ? r.bottom + 6
    : Math.max(8, r.top - h - 6)) + 'px';
  menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 12)) + 'px';
  menu.style.maxHeight = Math.max(160, window.innerHeight - 24) + 'px';
  openMenu = menu;
}

function exportMenu() {
  showMenu($('moreBtn'), [
    { label: '匯出 HTML', meta: '可直接開', action: function () { exportChat('html'); } },
    { label: '匯出 Markdown', action: function () { exportChat('md'); } },
    { label: '匯出 JSON', meta: '含工具往返', action: function () { exportChat('json'); } }
  ]);
}

// 匯出檔用的 Markdown：跟畫面上那份共用 blockMd，但 code block 換成乾淨的
// <pre><code> —— 介面版帶著複製按鈕與 data-code，那些在匯出檔裡是死的。
function exportMd(src) {
  const re = /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g;
  let out = '', last = 0, m;
  while ((m = re.exec(src)) !== null) {
    out += blockMd(src.slice(last, m.index));
    out += '<pre><code>' + esc(m[2].replace(/\n$/, '')) + '</code></pre>';
    last = m.index + m[0].length;
  }
  return out + blockMd(src.slice(last));
}

// 單一 HTML：不連任何外部資源，寄給別人或存檔都能直接打開。
// 版面是聊天室的樣子 —— 模型靠左、你靠右。匯出檔常常是拿去給別人看的，
// 「誰說了什麼」要能一眼掃過去，不用讀每一行的名字。
// 樣式刻意寫死一份簡單的，不從主程式搬 —— 匯出檔的壽命比介面長很多。
function exportHtml(c) {
  const rows = c.messages.map(function (m) {
    if (m.role === 'tool') {
      // 工具往返跟著模型那一側，預設收起來：它是過程不是對話
      return '<div class="row left"><details class="tool"><summary>🔧 ' +
        esc(m.tool_name || '') +
        (m.denied ? '（已略過）' : (m.failed ? '（失敗）' : '')) + '</summary><pre>' +
        esc((m.args ? JSON.stringify(m.args, null, 2) + '\n\n' : '') + (m.content || '')) +
        '</pre></details></div>';
    }
    const mine = m.role === 'user';
    const who = mine ? '你' : (m.model || 'assistant');
    const think = m.thinking
      ? '<details class="think"><summary>思考過程</summary><pre>' + esc(m.thinking) +
        '</pre></details>' : '';
    return '<div class="row ' + (mine ? 'right' : 'left') + '">' +
      '<div class="bub"><div class="who">' + esc(who) + '</div>' +
      think + '<div class="body">' + exportMd(m.content || '') + '</div></div></div>';
  }).join('\n');

  return '<!doctype html>\n<html lang="zh-Hant"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>' + esc(c.title) + '</title><style>' +
    'body{max-width:860px;margin:0 auto;padding:28px 18px;' +
    'font:15px/1.75 system-ui,-apple-system,"Noto Sans TC",sans-serif;' +
    'background:#faf9f7;color:#22201d;}' +
    'h1{font-size:19px;margin:0 0 4px;}.meta{color:#8b857c;font-size:12px;margin-bottom:26px;}' +
    '.row{display:flex;margin:0 0 16px;}' +
    '.row.left{justify-content:flex-start;}.row.right{justify-content:flex-end;}' +
    '.bub{max-width:78%;border-radius:14px;padding:9px 14px;' +
    'background:#fff;border:1px solid #e7e3dc;}' +
    '.row.right .bub{background:#eef1fb;border-color:#dce1f5;}' +
    '.who{font-size:11.5px;font-weight:600;color:#8b857c;margin-bottom:4px;}' +
    '.row.right .who{text-align:right;}' +
    '.body>*:first-child{margin-top:0;}.body>*:last-child{margin-bottom:0;}' +
    'pre{background:#f2efe9;border:1px solid #e7e3dc;border-radius:8px;padding:11px 13px;' +
    'overflow-x:auto;font:12.5px/1.7 ui-monospace,Menlo,Consolas,monospace;}' +
    'code{background:#f2efe9;padding:1px 4px;border-radius:4px;' +
    'font:.9em ui-monospace,Menlo,Consolas,monospace;}' +
    'pre code{background:none;padding:0;}' +
    'details{margin:6px 0;}summary{cursor:pointer;color:#8b857c;font-size:12.5px;}' +
    '.row .tool{max-width:78%;background:#fff;border:1px solid #e7e3dc;' +
    'border-radius:12px;padding:7px 13px;}' +
    '.tool summary{color:#7c5cf0;}' +
    '@media(max-width:620px){.bub,.row .tool{max-width:92%;}}' +
    '@media(prefers-color-scheme:dark){body{background:#191714;color:#e8e4dc;}' +
    '.bub,.row .tool,pre,code{background:#221f1b;border-color:#332f28;}' +
    '.row.right .bub{background:#232637;border-color:#343a52;}}' +
    '</style></head><body>' +
    '<h1>' + esc(c.title) + '</h1>' +
    '<div class="meta">模型：' + esc(c.model || '') + ' · 匯出於 ' +
    esc(new Date().toLocaleString()) + ' · ZackLLMGUI</div>' +
    rows + '</body></html>';
}

function exportChat(fmt) {
  const c = current();
  if (!c || !c.messages.length) { toast('這個對話還沒有內容'); return; }
  let text, mime, ext;
  if (fmt === 'html') {
    text = exportHtml(c); mime = 'text/html'; ext = '.html';
  } else if (fmt === 'json') {
    text = JSON.stringify(c, null, 2); mime = 'application/json'; ext = '.json';
  } else {
    const lines = ['# ' + c.title, '', '> 模型：' + (c.model || ''), ''];
    c.messages.forEach(function (m) {
      lines.push('## ' + (m.role === 'user' ? '你' : (m.model || 'assistant')), '');
      if (m.thinking) lines.push('<details><summary>思考過程</summary>', '', m.thinking, '', '</details>', '');
      lines.push(m.content, '');
    });
    text = lines.join('\n'); mime = 'text/markdown'; ext = '.md';
  }
  const url = URL.createObjectURL(new Blob([text], { type: mime + ';charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = c.title.replace(/[\\/:*?"<>|]/g, '_') + ext;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

/* ══════════════════════ 連線設定 ══════════════════════ */
const PROVIDERS = [['Ollama', 'ollama'], ['OpenAI 相容', 'openai']];

function renderProvSeg(pick) {
  const seg = $('provSeg');
  seg.innerHTML = '';
  PROVIDERS.forEach(function (opt) {
    const b = document.createElement('button');
    b.textContent = opt[0];
    if (opt[1] === pick) b.className = 'on';
    b.addEventListener('click', function () { renderProvSeg(opt[1]); });   // 只切換，別把打到一半的欄位洗掉
    seg.appendChild(b);
  });
  $('provOllama').hidden = pick !== 'ollama';
  $('provOpenai').hidden = pick !== 'openai';
  S.provPick = pick;         // 說明按鈕要知道現在看的是哪一種
}

// 說明一律收在驚嘆號後面：對話框上留操作項就好，一段一段的字會把版面撐長，
// 而且真正要看說明的是第一次用的人，看第二次就變成雜訊了。
function openRulesHelp() {
  openHelp('允許規則', [
    ['要解決什麼', '自動模式是全有全無的三段。人真正想要的從來不是那三個，'
      + '而是「pytest 一律放行、git commit 要問我、secrets/ 永遠不准碰」。'],
    ['怎麼比對', '工具名與樣式都用萬用字元。樣式比對的是這次呼叫的主體 —— '
      + '指令類比指令、檔案類比路徑、連網類比網址。第一條命中的說了算。'],
    ['順序', 'deny 規則 → 擋掉的危險指令 → 風險指令一律問 → allow 規則 → 自動模式。'
      + 'allow 不能蓋過風險指令：那條保證不能被一個設定檔悄悄拿掉。'],
    ['擋在哪一端', 'deny 是 serve.py 擋的，不是瀏覽器。只在瀏覽器擋的話，'
      + '那不是邊界，是提醒。'],
    ['存在哪', '工作區的 .zackllmgui-rules.json，沒有的話用 serve.py 旁邊那份。'
      + '模型讀不到也寫不到它 —— 不然等於自己給自己開權限。']
  ]);
}

/* ══════════════════════ 側欄拖寬 ══════════════════════ */
// 上下限只寫在 CSS 的 min-width / max-width，這裡讀回來用 —— 兩邊各寫一份
// 遲早會對不上，而且「拖到某個寬度按鈕就跑版」是使用者第一個會踩到的事。
function sideWidthLimits(aside) {
  const cs = getComputedStyle(aside);
  const min = parseFloat(cs.minWidth) || 190;
  const max = parseFloat(cs.maxWidth) || (innerWidth * 0.7);
  // 再留 360px 給中間的對話區，不然兩側可以把它擠到不能用
  return [min, Math.max(min, Math.min(max, innerWidth - 360))];
}

function wireResizer(handle, aside, varName, grows) {
  if (!handle || !aside) return;
  handle.addEventListener('pointerdown', function (e) {
    if (e.button) return;                        // 只理會左鍵
    e.preventDefault();
    const startX = e.clientX;
    const startW = aside.getBoundingClientRect().width;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('dragging');
    document.body.classList.add('resizing');

    const move = function (ev) {
      // 左側欄往右拖是變寬，右側欄相反
      const delta = grows === 'right' ? ev.clientX - startX : startX - ev.clientX;
      const lim = sideWidthLimits(aside);
      const w = Math.max(lim[0], Math.min(lim[1], startW + delta));
      document.documentElement.style.setProperty(varName, w + 'px');
    };
    const up = function () {
      handle.classList.remove('dragging');
      document.body.classList.remove('resizing');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      saveConfig();                              // 拖完才存，不是拖的每一格都存
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
  // 雙擊還原成預設寬度：拖歪了不必自己慢慢喬回去
  handle.addEventListener('dblclick', function () {
    document.documentElement.style.removeProperty(varName);
    saveConfig();
  });
}

// 存回去的寬度也要夾一次：視窗換了一台螢幕（或使用者改了 CSS）之後，
// 上次那個寬度可能已經超出這台的上限。
function applySideWidth(varName, aside, px) {
  if (!(+px > 0) || !aside) return;
  const lim = sideWidthLimits(aside);
  document.documentElement.style.setProperty(
    varName, Math.max(lim[0], Math.min(lim[1], +px)) + 'px');
}

function savedSideWidth(varName) {
  const v = document.documentElement.style.getPropertyValue(varName);
  return v ? parseFloat(v) : 0;
}


function openHostHelp() {
  const rows = S.provPick === 'openai' ? [
    ['接得上哪些服務',
     '任何提供 /v1/chat/completions 的都行：OpenAI、Groq、together、OpenRouter，'
     + '或本機的 vLLM、LM Studio、llama.cpp。'],
    ['金鑰放在哪',
     '存在這台瀏覽器的 localStorage，不會送到其他地方。'
     + (S.srv.ext ? '請求經由 serve.py 轉送，不會有 CORS 問題，金鑰只在本機之間傳遞。'
                  : '直接開 HTML 檔時瀏覽器會直接連過去，對方沒給 CORS 標頭就會失敗，'
                    + '建議改用 serve.py 啟動。')],
    ['工具能不能用', '可以，但要自己勾上面那個「送出工具定義」——'
     + '外部 API 問不到模型支不支援（/v1/models 只回 id，沒有 capabilities），'
     + '所以這裡沒辦法像 Ollama 那樣自動判斷。勾了之後讀檔、跑指令、'
     + '改檔案跟本機模式完全一樣。不支援工具的模型會直接回錯誤。'],
    ['這個模式還少了什麼',
     '思考過程只有部分服務給得出來（DeepSeek、vLLM 的 reasoning_content，'
     + 'OpenRouter 的 reasoning）—— 那不是規格裡的欄位，給了就顯示，沒給就沒有。'
     + '另外圖片還沒接，子代理（task）也不會出現在工具清單裡。']
  ] : [
    ['現在連到哪', S.upstream ? '經由代理連到 ' + S.upstream + '。' : '還沒連上。'],
    ['CORS 會不會出問題', SAME_ORIGIN
      ? '這個頁面是本機的 serve.py 端出來的，預設走同源代理，不會有問題。'
        + '要直接連別台就填完整位址（那台要設 OLLAMA_ORIGINS）。'
      : '直接開 HTML 檔時，Ollama 那台要設 OLLAMA_ORIGINS=*；遠端還要 OLLAMA_HOST=0.0.0.0。'
        + '不方便改的話，改用 serve.py 啟動。']
  ];
  openHelp('連線設定', rows);
}

function openRewindHelp() {
  openHelp('還原檔案', [
    ['一輪一個還原點', '每則提示送出前照一張相（C），那一輪動過的檔案列在底下。'
      + '點 C 就把整個工作區退回那一輪之前 —— 包含 run_shell 改的東西。'],
    ['要有 git', '快照是 git 的 shadow commit，用臨時 index 做，你的 HEAD、分支、'
      + '暫存區都不會被動到。不是 git repo 就只有檔案工具改的那幾筆。'],
    ['不會動到什麼', '只動工作區裡的檔案。對話一個字都不會變，所以還原之後'
      + '模型仍然記得它做過什麼。'],
    ['紀錄放在哪', '工作區的 .zackllmgui-backup/journal.jsonl，跟備份放在一起。']
  ]);
}

function openFontHelp() {
  openHelp('字體大小', [
    ['縮放哪些東西', '只縮放要讀的內容 —— 對話、程式碼、思考過程、輸入框。'],
    ['為什麼側欄不跟著變', '側欄與按鈕一起放大的話，可讀區反而被擠小。'
      + '要整個介面放大請用瀏覽器的 Ctrl +。'],
    ['範圍', '70%～200%，設定會記住，下次打開直接套用。']
  ]);
}

function openPullHelp() {
  openHelp('模型名稱怎麼填', [
    ['Ollama 官方模型', '直接填名稱，例如 qwen3:8b。'],
    ['Hugging Face 的 GGUF', 'hf.co/使用者/儲存庫:量化標籤，例如 '
      + 'hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M。'
      + '沒填量化標籤時 Ollama 會抓該儲存庫的預設檔案。'],
    ['懶得整理格式', '直接貼 huggingface.co 的網址、或整行 ollama pull … 指令，會自動轉換。']
  ]);
}

function openCompareHelp() {
  openHelp('多模型比較', [
    ['用什麼設定跑', '目前的取樣參數與系統提示。'],
    ['會不會影響對話', '不含對話歷史，也不會存進對話 —— 純粹是同一句話丟給幾個模型看差別。']
  ]);
}

function openHostDialog(pick) {
  // 這支同時當事件處理器用，收到的可能是 Event，不是模式字串
  const mode = (pick === 'ollama' || pick === 'openai') ? pick : S.provider;
  $('hostInput').value = S.host;
  $('oaBase').value = S.oa.base;
  $('oaKey').value = S.oa.key;
  $('oaTools').checked = !!S.oa.tools;
  $('hostResult').textContent = '';
  renderProvSeg(mode);
  $('hostOverlay').classList.remove('hidden');
  const focus = mode === 'openai' ? $('oaBase') : $('hostInput');
  focus.focus();
  focus.select();
}

function dialogMode() {
  return $('provOpenai').hidden ? 'ollama' : 'openai';
}

// 測試時暫時套用對話框裡的值，測完還原，免得測壞了連原本的連線也斷掉
async function testHost() {
  const res = $('hostResult');
  res.style.color = 'var(--ink3)';
  res.textContent = '測試中…';
  const keep = { provider: S.provider, host: S.host, oa: S.oa };
  S.provider = dialogMode();
  S.host = normalizeHost($('hostInput').value);
  S.oa = { base: normalizeBase($('oaBase').value), key: $('oaKey').value.trim(),
           tools: $('oaTools').checked };
  try {
    if (S.provider === 'openai') {
      const data = await oaJson('/models', null, 20000);
      const n = (data.data || []).length;
      res.style.color = 'var(--ok)';
      res.textContent = '成功 · ' + n + ' 個模型';
    } else {
      const tags = await apiJson('/api/tags');
      let v = '?';
      try { v = (await apiJson('/api/version', null, 5000)).version; } catch (e) { /* 舊版沒這支 */ }
      res.style.color = 'var(--ok)';
      res.textContent = '成功 · v' + v + ' · ' + ((tags.models || []).length) + ' 個模型';
    }
  } catch (err) {
    res.style.color = 'var(--err)';
    res.textContent = friendlyError(err).msg;
  } finally {
    S.provider = keep.provider;
    S.host = keep.host;
    S.oa = keep.oa;
  }
}

/* ══════════════════════ 輸入框 ══════════════════════ */
function autoGrow() {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(170, Math.max(24, el.scrollHeight)) + 'px';
}

/* ══════════════════════ 主題 ══════════════════════ */
function applyTheme() {
  document.documentElement.setAttribute('data-theme', S.theme);
}

// 側欄左下角的頭像。名字只是顯示用的 —— 不會送給模型，也不會離開這台機器。
function renderUser() {
  const name = (S.userName || '').trim();
  const btn = $('avatarBtn');
  btn.textContent = name ? Array.from(name)[0] : '本';
  btn.title = (name || '本機') + '　點一下改名字';
}

function renameUser() {
  // 取消要當作沒事發生。|| '' 會把 null 變成空字串，等於按取消就把名字清掉
  const raw = prompt('你的名字（只顯示在這個角落，不會送出去）', S.userName || '');
  if (raw === null) return;
  S.userName = raw.trim().slice(0, 20);
  renderUser();
  saveConfig();
}

/* ══════════════════════ 兩個入口 ══════════════════════ */
// 側欄齒輪＝跟整個程式有關的設定；右上 ⋯＝只跟眼前這個對話有關的動作。
function openSettingsMenu(anchor) {
  showMenu(anchor && anchor.getBoundingClientRect ? anchor : $('settingsBtn'), [
    { label: '連線設定…', meta: displayHost(), action: openHostDialog },
    { label: '模型管理…', meta: 'Ctrl M', action: openModels },
    { label: '多模型比較…', action: openCompare },
    '-',
    { label: '字體大小…', meta: fontLabel(), action: function () {
        applyFontScale();                       // 進來先把 stepper 的顯示對齊現值
        $('fsOverlay').classList.remove('hidden');
      } },
    { label: '淺色 / 深色主題', meta: S.theme === 'dark' ? '深色' : '淺色',
      action: function () {
        S.theme = S.theme === 'dark' ? 'light' : 'dark';
        applyTheme(); saveConfig();
      } },
    { label: '重新整理模型清單', meta: 'F5', action: function () { refreshModels(); } }
  ]);
}



/* ══════════════════════ 系統用量 ══════════════════════ */
// 這是 **serve.py 那一台** 的數字。Ollama 指到別台時，GPU 那幾格講的不是
// 真正在跑模型的那張卡 —— 面板上會標出來，不然會看到「模型在跑但 GPU 0%」
// 然後以為是壞了。VRAM 排第一：它是唯一一個爆掉不報錯、只會慢十倍的東西。

async function refreshSys() {
  if (document.hidden) return;              // 分頁在背景就不要一直問
  try {
    const res = await fetch(apiUrl('/sys'));
    S.sys = res.ok ? await res.json() : null;
  } catch (e) { S.sys = null; }
  renderSysBar();
  if (!$('sysOverlay').classList.contains('hidden')) renderSysFull();
}

function sysShown() {
  return SYS_METRICS.filter(function (m) {
    return S.sysChips.indexOf(m[0]) >= 0 && sysCell(m[0], S.sys);
  });
}

function renderSysBar() {
  const bar = $('sysBar');
  const shown = S.sys ? sysShown() : [];
  bar.hidden = !shown.length;
  if (!shown.length) return;
  bar.innerHTML = shown.map(function (m) {
    const c = sysCell(m[0], S.sys);
    return '<span class="m ' + sysLevel(c.at) + '"><span>' + m[1] +
      '</span><b>' + esc(c.text) + '</b></span>';
  }).join('');
}

function sysRow(k, v, at) {
  return '<div><div class="row"><span class="k">' + esc(k) + '</span>' +
    '<span class="v">' + esc(v) + '</span></div>' +
    (typeof at === 'number'
      ? '<div class="bar ' + sysLevel(at) + '"><i style="width:' +
        Math.min(100, Math.max(0, at * 100)).toFixed(0) + '%"></i></div>'
      : '') + '</div>';
}

function renderSysFull() {
  const d = S.sys, box = $('sysFull');
  if (!d) { box.textContent = '拿不到系統用量（只有本機開的頁面看得到）。'; return; }
  const rows = [];
  (d.gpu || []).forEach(function (g, i) {
    const tag = (d.gpu.length > 1 ? '#' + i + ' ' : '') + g.name;
    if (g.vram && g.vram.total) {
      rows.push(sysRow(tag + '　VRAM',
        g.vram.used.toFixed(1) + ' / ' + g.vram.total.toFixed(1) + ' GB',
        g.vram.used / g.vram.total));
    }
    rows.push(sysRow(tag + '　使用率', g.util + '%　' + g.temp + '°C', g.util / 100));
  });
  if (d.ram && d.ram.total) {
    rows.push(sysRow('RAM', d.ram.used.toFixed(1) + ' / ' + d.ram.total.toFixed(1) + ' GB',
      d.ram.used / d.ram.total));
  }
  if (typeof d.cpu === 'number' && d.cpu >= 0) {
    rows.push(sysRow('CPU（' + d.cores + ' 核）', d.cpu.toFixed(0) + '%', d.cpu / 100));
  }
  if (!d.gpu || !d.gpu.length) {
    rows.push('<div class="note">找不到 nvidia-smi，所以沒有 GPU 與 VRAM 那幾格。</div>');
  }
  (S.sysModels || []).forEach(function (m) {
    rows.push(sysRow('載入中：' + m.name,
      (m.size_vram ? (m.size_vram / 1073741824).toFixed(1) + ' GB 在 VRAM' : '在 CPU')));
  });
  box.innerHTML = rows.join('');
  $('sysWhere').textContent = d.ollama_local
    ? '這些是 serve.py 這台機器的數字，Ollama 也在同一台。'
    : 'Ollama 不在這台機器上 —— GPU 與 VRAM 講的是 serve.py 這台的卡，不是跑模型的那張。';
}

function renderSysPick() {
  $('sysPick').innerHTML = SYS_METRICS.map(function (m) {
    const has = !!sysCell(m[0], S.sys);
    return '<label class="check" title="' + esc(m[2]) + '">' +
      '<input type="checkbox" data-sys="' + m[0] + '"' +
      (S.sysChips.indexOf(m[0]) >= 0 ? ' checked' : '') +
      (has ? '' : ' disabled') + '>' + m[1] + (has ? '' : '（這台拿不到）') + '</label>';
  }).join('');
  $('sysPick').querySelectorAll('input[data-sys]').forEach(function (el) {
    el.addEventListener('change', function () {
      const id = el.getAttribute('data-sys');
      S.sysChips = SYS_METRICS.map(function (m) { return m[0]; }).filter(function (x) {
        return x === id ? el.checked : S.sysChips.indexOf(x) >= 0;
      });
      saveConfig();
      renderSysBar();
    });
  });
}

async function openSys() {
  $('sysOverlay').classList.remove('hidden');
  renderSysFull();
  renderSysPick();
  try {                                     // 哪個模型正佔著 VRAM，Ollama 自己知道
    const res = await fetch(apiUrl('/api/ps'));
    S.sysModels = res.ok ? ((await res.json()).models || []) : [];
  } catch (e) { S.sysModels = []; }
  renderSysFull();
}
