/* ══════════════════════ 狀態 ══════════════════════ */
const S = {
  host: DEFAULT_HOST, models: [], caps: {}, model: '', conn: 'idle',
  version: '?', chats: [], currentId: null, params: Object.assign({}, DEFAULTS),
  think: false, showThink: true, theme: 'dark', images: [], fontScale: 1, userName: '',
  ctxMax: {}, atFiles: null,
  streaming: false, abort: null, probeSeq: 0, stick: true, upstream: '',
  provider: 'ollama',                      // 'ollama' 或 'openai'（相容 API）
  oa: { base: 'https://api.openai.com/v1', key: '' },
  srv: { tools: false, toolsLocal: false, extract: false, ext: false },
  ws: { path: '', write: false, git: false, python: '', files: 0 },
  toolDefs: [], agentRules: '', tab: 'params', fv: null, todos: [], plan: false,
  jobs: [],                                // 背景指令，活在 serve.py 那個行程裡
  pre: null,                               // 背景先算好的壓縮摘要
  streamTools: ['run_shell', 'run_tests'], mcp: null, cpus: 0, layers: {},
  sysChips: ['vram'], sys: null,        // topbar 要顯示哪幾格用量
  browse: null, treeReady: false, skills: null, slash: { items: [], at: 0 },
  run: { rounds: 0, calls: 0, tokens: 0 }, queued: [], runTicker: null,
  auto: 'off',                             // off|read|edit|full|ws，見 AUTO_MODES
  files: [], presets: [], tools: false, ctxRatio: 1, lastEst: 0, ctxLabel: ''
};

/* ══════════════════════ 小工具 ══════════════════════ */
const $ = function (id) { return document.getElementById(id); };
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function uid() { return Math.random().toString(36).slice(2, 12); }
function normalizeHost(h) {
  h = (h || '').trim().replace(/\/+$/, '');
  if (!h) return DEFAULT_HOST;
  if (!/^https?:\/\//i.test(h)) h = 'http://' + h;
  return h;
}
function humanSize(n) {
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + u[i];
}
// localStorage 在 file:// 下不是每個瀏覽器都給用，全部包起來，壞掉就只存在記憶體
function lsGet(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
}
// 回傳有沒有真的存進去。**這個回傳值不能省** —— 這個 catch 除了無痕模式，
// 也吞掉 QuotaExceededError，而配額滿了之後每一次 saveChats() 都會無聲失敗，
// 重整才發現整場任務沒了。靜靜掉資料是最不該簡化掉的那一類。
function lsSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (e) {
    return false;
  }
}

/* ══════════════════════ 對話存哪裡 ══════════════════════
   結論：**IndexedDB，一則對話一筆紀錄**，完全不出這台瀏覽器。

   為什麼不是 localStorage（原本的做法）：配額只有 5–10MB，而一場長的 agent 任務
   光工具輸出就 400KB 上下 —— 十幾場就滿。滿了之後 setItem 丟 QuotaExceededError，
   整份對話存不進去。而且它是同步的：每次存都要把「全部」對話 JSON.stringify 一遍，
   串流中一秒好幾次，那是主執行緒上實打實的停頓。

   為什麼不是寫進 serve.py 那一端：對話裡有專案路徑與程式碼，一旦落地就得回答
   「同一台 serve.py 上誰讀得到誰的對話」。伺服器只出算力，資料留在瀏覽器，
   這句話才講得清楚。

   IndexedDB 給的是：配額按磁碟算（GB 級）、非同步、structured clone 不必先轉字串、
   而且**一則對話一筆**，所以存的時候只寫改動的那一則，兩個分頁也不會整包蓋掉對方。 */
const DB_NAME = 'zackllmgui';
const DB_STORE = 'chats';
let dbPromise = null;

function chatDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise(function (ok, bad) {
    let req;
    // file:// 下 Firefox 直接不給用，無痕模式也可能擋 —— 這裡失敗就回頭走 localStorage
    try { req = indexedDB.open(DB_NAME, 1); } catch (e) { bad(e); return; }
    req.onupgradeneeded = function () {
      req.result.createObjectStore(DB_STORE, { keyPath: 'id' });
    };
    req.onsuccess = function () { ok(req.result); };
    req.onerror = function () { bad(req.error || new Error('IndexedDB 開不起來')); };
    req.onblocked = function () { bad(new Error('IndexedDB 被其他分頁擋住')); };
  });
  return dbPromise;
}

function dbRun(mode, fn) {
  return chatDb().then(function (d) {
    return new Promise(function (ok, bad) {
      const tx = d.transaction(DB_STORE, mode);
      const req = fn(tx.objectStore(DB_STORE));
      // 等 tx 完成而不是等 req.success：readwrite 要交易真的落地才算存到
      tx.oncomplete = function () { ok(req ? req.result : undefined); };
      tx.onerror = function () { bad(tx.error); };
      tx.onabort = function () { bad(tx.error || new Error('交易被中止')); };
    });
  });
}

// 新的排在前面。用 created 而不是陣列順序：兩個分頁各自寫自己的那幾則時，
// 「第幾個」會打架，「什麼時候建的」不會。
function chatsLoad() {
  return dbRun('readonly', function (st) { return st.getAll(); }).then(function (list) {
    return (list || []).sort(function (a, b) { return (b.created || 0) - (a.created || 0); });
  });
}
function chatPut(c) { return dbRun('readwrite', function (st) { return st.put(c); }); }
function chatDrop(id) { return dbRun('readwrite', function (st) { return st.delete(id); }); }
let toastTimer = null;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove('show'); }, 2200);
}

/* ══════════════════════ Markdown ══════════════════════ */
const KEYWORDS = new Set(('def class return import from as if elif else for while with try except ' +
  'finally raise lambda yield pass break continue in not and or is None True False async await ' +
  'global nonlocal assert del function const let var new this typeof export default public private ' +
  'static void int float char bool struct func package type interface fn mut impl use select insert ' +
  'update delete where join group order echo exit then fi do done esac').split(' '));

const HL_LANGS = new Set(['', 'python', 'py', 'javascript', 'js', 'typescript', 'ts', 'json',
  'bash', 'sh', 'shell', 'powershell', 'ps1', 'sql', 'c', 'cpp', 'java', 'go', 'rust', 'rs',
  'yaml', 'toml']);

function highlight(code, lang) {
  if (!HL_LANGS.has((lang || '').toLowerCase())) return esc(code);
  const re = /("[^"\n]*"|'[^'\n]*')|(#[^\n]*|\/\/[^\n]*)|(\b\d+\.?\d*\b)|(\b[A-Za-z_]\w*\b)/g;
  let out = '', last = 0, m;
  while ((m = re.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    const t = m[0];
    if (m[1]) out += '<span class="s">' + esc(t) + '</span>';
    else if (m[2]) out += '<span class="c">' + esc(t) + '</span>';
    else if (m[3]) out += '<span class="n">' + esc(t) + '</span>';
    else if (KEYWORDS.has(t)) out += '<span class="k">' + esc(t) + '</span>';
    else if (code[m.index + t.length] === '(') out += '<span class="f">' + esc(t) + '</span>';
    else out += esc(t);
    last = m.index + t.length;
  }
  return out + esc(code.slice(last));
}

function inlineMd(text) {
  let out = esc(text);
  out = out.replace(/`([^`\n]+)`/g, function (_, c) { return '<code>' + c + '</code>'; });
  out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  return out;
}

function blockMd(text) {
  const lines = text.split('\n');
  let html = '', para = [], list = null;
  function flushPara() {
    if (para.length) { html += '<p>' + para.join('<br>') + '</p>'; para = []; }
  }
  function flushList() {
    if (list) { html += '<' + list.tag + '>' + list.items.join('') + '</' + list.tag + '>'; list = null; }
  }
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i], t = raw.trim();
    const h = /^(#{1,6})\s+(.*)$/.exec(t);
    const ul = /^[-*+]\s+(.*)$/.exec(t);
    const ol = /^\d+[.)]\s+(.*)$/.exec(t);
    if (h) { flushPara(); flushList(); html += '<h3>' + inlineMd(h[2]) + '</h3>'; }
    else if (ul) {
      flushPara();
      if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
      list.items.push('<li>' + inlineMd(ul[1]) + '</li>');
    } else if (ol) {
      flushPara();
      if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
      list.items.push('<li>' + inlineMd(ol[1]) + '</li>');
    } else if (!t) { flushPara(); flushList(); }
    else { flushList(); para.push(inlineMd(raw)); }
  }
  flushPara(); flushList();
  return html;
}

// 把模型寫出來的 `wafer_counter.py:42` 變成點得下去的東西。
//
// **在 DOM 上做，不在 HTML 字串上做**：訊息是字串組出來的，字串取代會誤傷
// href="…" 與 data-code="…" 這類屬性。只走文字節點就完全沒有這個問題。
//
// **只認工作區裡真的存在的檔案**。不驗的話 http://host:8080 與 example.com:443
// 都會被當成「檔案:行號」—— 副檔名白名單擋不住 .com:443。
const FILE_REF_RE = /([A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,8})(?::(\d+))?/g;

function fileRefTarget(path) {
  const files = S.atFiles || [];
  if (!files.length) return '';
  if (files.indexOf(path) >= 0) return path;
  // 模型常常只寫檔名不寫路徑。只有唯一一個同名檔時才敢連過去。
  const hits = files.filter(function (f) {
    return f === path || f.endsWith('/' + path);
  });
  return hits.length === 1 ? hits[0] : '';
}

function linkifyFileRefs(root) {
  if (!root || !(S.atFiles || []).length) return;
  const skip = { A: 1, BUTTON: 1, TEXTAREA: 1, INPUT: 1 };
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walk.nextNode()) {
    const n = walk.currentNode;
    if (!n.parentElement || skip[n.parentElement.tagName]) continue;
    if (n.parentElement.closest('a')) continue;
    if (FILE_REF_RE.test(n.nodeValue)) nodes.push(n);
    FILE_REF_RE.lastIndex = 0;
  }
  nodes.forEach(function (node) {
    const frag = document.createDocumentFragment();
    let last = 0, m;
    FILE_REF_RE.lastIndex = 0;
    while ((m = FILE_REF_RE.exec(node.nodeValue)) !== null) {
      const target = fileRefTarget(m[1]);
      if (!target) continue;
      if (m.index > last) frag.appendChild(document.createTextNode(node.nodeValue.slice(last, m.index)));
      const a = document.createElement('span');
      a.className = 'file-ref';
      a.textContent = m[0];
      a.setAttribute('data-path', target);
      if (m[2]) a.setAttribute('data-line', m[2]);
      a.title = '在檔案分頁開啟 ' + target + (m[2] ? ' 第 ' + m[2] + ' 行' : '');
      frag.appendChild(a);
      last = m.index + m[0].length;
    }
    if (!frag.childNodes.length) return;
    if (last < node.nodeValue.length) frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
    node.parentNode.replaceChild(frag, node);
  });
}

function renderMarkdown(src) {
  const re = /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g;
  let out = '', last = 0, m;
  while ((m = re.exec(src)) !== null) {
    out += blockMd(src.slice(last, m.index));
    const lang = (m[1] || '').trim();
    const code = m[2].replace(/\n$/, '');
    out += '<div class="code"><div class="code-head"><span class="code-lang">' +
      esc(lang || 'text') + '</span>' +
      '<button class="code-copy" title="複製程式碼" data-code="' + esc(code) + '">' +
      ico('copy', 13) + '</button></div><pre>' + highlight(code, lang) + '</pre></div>';
    last = m.index + m[0].length;
  }
  return out + blockMd(src.slice(last));
}

