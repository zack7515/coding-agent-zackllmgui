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
function lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 無痕模式等 */ }
}
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

