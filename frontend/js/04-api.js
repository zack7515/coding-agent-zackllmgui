/* ══════════════════════ Ollama API ══════════════════════ */
// serve.py 自己的端點，跟 Ollama 的 /api/* 不是同一回事。
// S.host 是「Ollama 在哪」—— 照 README 的建議把它指到 GPU 主機之後，
// /tool、/ls、/workspace 這些還是要問**端出這個頁面的那一台**，
// 不然檔案分頁會說「這個頁面不是本機開的」，而且工具全部打到 Ollama 去。
const SRV_PATHS = /^\/(upstream|tool|tools|run|preview|workspace|view|ls|browse|journal|rewind|checkpoint|rules|skills|git|restore|extract|ext|mcp|sys)$/;

function apiUrl(path) {
  if (SAME_ORIGIN && SRV_PATHS.test(path)) return location.origin + path;
  return S.host.replace(/\/+$/, '') + path;
}

/* ── 分頁身分 ───────────────────────────────────────────────────────
   serve.py 的工作區、能不能改檔案、待辦、計畫都是**跟著分頁走**的。分頁靠這個
   id 認回自己那一份；漏送的請求會落到「預設分頁」，而那正是原本兩個分頁互相
   蓋掉工作區的行為 —— A 分頁的 write_file 靜靜寫進 B 的資料夾。
   所以不在二十幾個 fetch 各補一次 header，而是包一層：新加的呼叫也不會漏。

   每次載入都重新產生，不存 sessionStorage：瀏覽器的「複製分頁」會把
   sessionStorage 一起複製，兩個分頁就會拿到同一個 id，等於沒修。
   重新整理換一個 id 沒關係 —— 網頁本來就會在載入時把自己的工作區推回去。 */
const TAB_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);

// 只有**同源、而且是 serve.py 自己的端點**才掛 X-Tab。跨來源加自訂 header 會逼出
// OPTIONS 預檢，Ollama 不見得接得住 —— 加錯地方的代價是整個頁面連不上。
function isSrvUrl(url) {
  if (!SAME_ORIGIN || String(url).indexOf(location.origin) !== 0) return false;
  return SRV_PATHS.test(String(url).slice(location.origin.length).replace(/[?#].*$/, ''));
}

if (typeof window !== 'undefined' && window.fetch) {
  const rawFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    if (!isSrvUrl(url)) return rawFetch(input, init);
    const next = Object.assign({}, init);
    next.headers = new Headers((init && init.headers)
      || (typeof input !== 'string' && input ? input.headers : undefined));
    next.headers.set('X-Tab', TAB_ID);
    return rawFetch(input, next);
  };
}

function friendlyError(err) {
  if (err && err.name === 'AbortError') return { msg: '（已停止）', abort: true };
  const raw = (err && err.message) || String(err);
  // fetch 失敗只會給 "Failed to fetch"，八成是 CORS 或主機不在
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    if (S.provider === 'openai') {
      return {
        msg: '無法連線到 ' + normalizeBase(S.oa.base),
        hint: '可能原因：\n' +
          '1. 位址打錯，或該服務沒有提供 /v1 這一層路徑\n' +
          '2. 直接開 HTML 檔時，對方沒有給 CORS 標頭 —— 改用 serve.py 啟動即可轉送\n' +
          '3. 網路不通，或需要 proxy'
      };
    }
    return {
      msg: '無法連線到 ' + S.host,
      hint: '可能原因：\n' +
        '1. Ollama 沒有啟動，或位址 / port 不對\n' +
        '2. 瀏覽器的 CORS 擋下了跨來源請求 —— Ollama 那台要設 OLLAMA_ORIGINS=*\n' +
        '3. 遠端連線時，server 需以 OLLAMA_HOST=0.0.0.0 啟動\n' +
        '若不方便改環境變數，改用 serve.py 啟動即可完全避開 CORS。'
    };
  }
  return { msg: raw };
}

async function apiJson(path, body, timeoutMs, signal) {
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || 8000);
  // 外面給的 signal（停止鍵）要接進來，不然按了停止只停得了主迴圈
  const relay = function () { ctrl.abort(); };
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', relay);
  }
  try {
    const res = await fetch(apiUrl(path), {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal
    });
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { /* 非 JSON */ }
    if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status + '：' + text.slice(0, 200)));
    return data;
  } finally {
    clearTimeout(timer);
    if (signal) signal.removeEventListener('abort', relay);
  }
}

// 走 serve.py 代理時要顯示真正的 Ollama 位址，不是網頁自己的 port；
// 外部 API 模式則顯示該服務的位址。
function displayHost() {
  const h = S.provider === 'openai' ? normalizeBase(S.oa.base) : (S.upstream || S.host);
  return h.replace(/^https?:\/\//, '');
}

/* ══════════════════════ 外部 OpenAI 相容 API ══════════════════════ */
// num_thread 是套用在跑 Ollama 的那一台。這個判斷由後端做 ——
// 它會把主機名解析出來跟自己的位址比對，所以 --ollama http://<自己的區網IP>
// 也認得出來是本機；前端用正規表示式比字串會漏掉那種寫法。
function ollamaIsLocal() {
  return S.provider !== 'openai' && !!S.srv.ollamaLocal;
}

function normalizeBase(v) {
  v = (v || '').trim().replace(/\/+$/, '');
  if (!v) return 'https://api.openai.com/v1';
  if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
  return v;
}

function oaTarget(path) { return normalizeBase(S.oa.base) + path; }

// 有 serve.py 就走 /ext 轉送（瀏覽器直接打 api.openai.com 會被 CORS 擋，
// 而且金鑰只在本機之間傳）；直接開檔時只好自己打，成不成看對方給不給 CORS。
function oaFetch(path, body, signal) {
  const target = oaTarget(path);
  const headers = { 'Content-Type': 'application/json' };
  if (S.oa.key) headers['Authorization'] = 'Bearer ' + S.oa.key;
  let url = target;
  if (S.srv.ext) { url = apiUrl('/ext'); headers['X-Target'] = target; }
  const init = { method: body ? 'POST' : 'GET', headers: headers, signal: signal || undefined };
  if (body) init.body = JSON.stringify(body);
  return fetch(url, init);
}

function oaError(data) {
  if (!data) return '';
  if (typeof data.error === 'string') return data.error;
  return (data.error && (data.error.message || data.error.type)) || '';
}

async function oaJson(path, body, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || 20000);
  try {
    const res = await oaFetch(path, body, ctrl.signal);
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { /* 非 JSON */ }
    if (!res.ok) throw new Error(oaError(data) || ('HTTP ' + res.status + '：' + text.slice(0, 200)));
    return data;
  } finally { clearTimeout(timer); }
}

// SSE：一行一個 data:，收到 [DONE] 就結束（跟 Ollama 的 NDJSON 不一樣）
async function streamSse(path, body, signal, onObj) {
  const res = await oaFetch(path, body, signal);
  if (!res.ok) {
    const txt = await res.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) { /* 非 JSON */ }
    throw new Error(oaError(data) || ('HTTP ' + res.status + '：' + txt.slice(0, 200)));
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
      if (!line || line.charAt(0) === ':') continue;        // 空行與心跳
      if (line.indexOf('data:') !== 0) continue;
      const payload = line.slice(5).trim();
      if (payload === '[DONE]') { try { reader.cancel(); } catch (e) { /* 已關 */ } return; }
      let obj;
      try { obj = JSON.parse(payload); } catch (e) { continue; }
      if (obj.error) throw new Error(oaError(obj) || 'API 回報錯誤');
      onObj(obj);
    }
  }
}

// Ollama 的 tool call 沒有 id，arguments 是物件；OpenAI 要 id，arguments 要字串。
function oaToolCall(t, id) {
  const fn = (t && t.function) || {};
  const a = fn.arguments;
  return {
    id: (t && t.id) || id,
    type: 'function',
    function: {
      name: fn.name || '',
      arguments: typeof a === 'string' ? a : JSON.stringify(a || {})
    }
  };
}

// OpenAI 的訊息格式沒有 thinking / images，能對應的就對應。
//
// 工具往返要整串一起看，不能一則一則轉：OpenAI 規定每一則 tool 訊息都要帶
// tool_call_id 指回去，而我們存的格式（照 Ollama 的）沒有這個欄位。
// 順序是固定的 —— assistant 的 tool_calls 後面就是同樣數量、同樣順序的 tool
// 訊息 —— 所以按順序配回去。配不到的（舊對話、或中間被壓縮過）退回純文字，
// 寧可少一點結構也不要讓整串歷史被對方退回。
function oaMsgs(list) {
  const out = [];
  let ids = [];                     // 上一則 assistant 還沒被認領的 tool_call_id
  (list || []).forEach(function (m) {
    if (m.role === 'tool') {
      const id = ids.shift();
      out.push(id
        ? { role: 'tool', tool_call_id: id, content: String(m.content || '') }
        : { role: 'user',
            content: '（工具 ' + (m.tool_name || '') + ' 的執行結果）\n' + (m.content || '') });
      return;
    }
    const item = { role: m.role, content: m.content || '' };
    ids = [];
    if (m.tool_calls && m.tool_calls.length) {
      item.tool_calls = m.tool_calls.map(function (t, i) {
        return oaToolCall(t, 'call_' + out.length + '_' + i);
      });
      ids = item.tool_calls.map(function (t) { return t.id; });
    }
    out.push(item);
  });
  return out;
}

/* ══════════════════════ 連線狀態 ══════════════════════ */
function setConn(state, extra) {
  S.conn = state;
  const shortHost = displayHost();
  const pill = $('statusPill'), dot = $('statusDot');
  dot.className = 'dot ' + (state === 'empty' ? 'ok' : state);
  pill.classList.toggle('error', state === 'error');

  const set = function (text, host, hostColor) {
    $('statusText').textContent = text;
    $('statusHost').textContent = host || '';
    $('statusHost').style.color = hostColor || '';
    $('pillSep').style.display = host ? '' : 'none';
  };

  if (state === 'ok') {
    set('已連線', shortHost);
    $('footInfo').textContent = S.models.length + ' 個模型 · v' + S.version;
    blockComposer('');
  } else if (state === 'empty') {
    set('已連線', '無可用模型', 'var(--warn)');
    $('footInfo').textContent = '沒有已下載的模型';
    blockComposer('沒有可用模型，請先執行 ollama pull');
  } else if (state === 'connecting') {
    set('連線中…', shortHost);
    $('footInfo').textContent = '連線中…';
  } else if (state === 'error') {
    set('無法連線', '');
    $('footInfo').textContent = '連線失敗';
    blockComposer(CONN_HINT);
  } else {
    set('尚未連線', shortHost);
  }
  $('footHost').textContent = shortHost;
  if (extra) S.connMessage = extra;
}

function blockComposer(reason) {
  S.blocked = reason;
  // 「連不上」不鎖送出鍵：送出去會進重試迴圈並顯示倒數，這比「按鍵是灰的、
  // 什麼也做不了」有用。其他理由（沒有可用模型）鎖住是對的。
  const on = !!reason && reason !== CONN_HINT;
  // 輸入框**不鎖**：跑到一半打的字會排隊（見 submitFromInput），
  // 鎖住等於長任務跑十分鐘只能乾等。送出鍵仍然停用 —— 那顆鍵在串流時是「停止」。
  $('input').disabled = false;
  $('sendBtn').disabled = on && !S.streaming;
  $('input').placeholder = reason || '輸入訊息…';
  if (!S.streaming) $('hint').textContent = reason || '';
}

// /upstream 回來的東西裡，跟工具有關的那一半。
function applyAgentState(info) {
  S.srv.tools = !!info.tools;
  S.srv.toolsLocal = !!info.tools_local;
  S.srv.client = info.client || '';
  S.srv.trustRemote = !!info.trust_remote;
  S.srv.browser = !!info.browser;
  S.srv.sandbox = !!info.sandbox;
  S.srv.sandboxInfo = info.sandbox_info || {};
  S.srv.sandboxWhy = S.srv.sandboxInfo.why || '';
  S.ws = Object.assign({ path: '', write: false }, info.workspace || {});
  S.toolDefs = info.tool_defs || [];
  S.agentRules = info.agent_rules || '';
  S.repoMap = info.repo_map || '';
  S.verifyHint = info.verify_hint || '';
  S.todos = info.todos || [];
  S.jobs = info.jobs || [];      // 重整頁面之後背景指令還在，這裡接得回來
  S.plan = !!info.plan;
  S.mcp = info.mcp || null;
  S.agentTypes = info.agents || [];   // 子代理型別來自 agents/*.md，不是寫死的
  if (info.stream_tools) S.streamTools = info.stream_tools;
  renderTodos();
}

// serve.py 的程式碼被改過就自己重開，然後重整頁面。
// 網頁每次重整都是新的（build.py 重讀 frontend/），Python 卻凍在啟動那一刻。
// 「頁面新、serve.py 舊」害過兩次，症狀都是「明明改好了卻還是不能用」。
// 跑到一半不重開 —— 重啟會把正在跑的工具一起殺掉。
async function checkSourceChanged() {
  if (S.restarting || S.streaming) return;
  let data;
  try {
    const res = await fetch(apiUrl('/alive'), { cache: 'no-store' });
    if (!res.ok) return;                       // 舊版沒有這支，靜靜略過
    data = await res.json();
  } catch (e) { return; }
  if (!data.src_changed || !data.local) return;
  S.restarting = true;
  toast('serve.py 的程式碼改過了，重新啟動…');
  try {
    await fetch(apiUrl('/restart'), { method: 'POST' });
  } catch (e) { /* 回應還沒送完就被換掉也算成功 */ }
  // 等它回來再重整。直接 reload 會撞在還沒 listen 的空檔上。
  for (let i = 0; i < 40; i++) {
    await new Promise(function (r) { setTimeout(r, 500); });
    try {
      const res = await fetch(apiUrl('/alive'), { cache: 'no-store' });
      if (res.ok) { location.reload(); return; }
    } catch (e) { /* 還沒起來 */ }
  }
  S.restarting = false;
  toast('serve.py 重啟後沒有回來，請自己看一下終端機');
}


async function loadUpstream() {
  S.upstream = '';
  S.srv = { tools: false, toolsLocal: false, extract: false, ext: false };
  S.ws = { path: '', write: false, git: false, python: '', files: 0 };
  S.toolDefs = [];
  S.agentRules = '';
  S.repoMap = '';
  S.todos = [];
  S.atFiles = null;
  S.mcp = null;

  // 直接開 HTML 檔的時候才是真的沒有 serve.py。
  // 這裡以前比的是 S.host === location.origin，等於「把 Ollama 指到別台」
  // 就整組工具跟著消失 —— 那兩件事沒有關係。
  if (!SAME_ORIGIN) { renderFeatBtn(); return; }
  try {
    const info = await apiJson('/upstream', null, 3000);
    S.upstream = info.upstream || '';
    S.srv.extract = !!info.extract;
    S.srv.ext = !!info.ext;
    S.cpus = info.cpus || 0;
    S.srv.ollamaLocal = !!info.ollama_local;
    applyParamLimits();
    applyAgentState(info);
  } catch (e) { /* 不是 serve.py 端出來的，當作沒有 */ }
  // 上次開著就自動接回去，省得每次重開 serve.py 都要再點一次
  if (S.tools && S.srv.toolsLocal && !S.srv.tools) {
    try { await setServerTools({ enabled: true }); } catch (e) { /* 開不起來就算了 */ }
  }
  renderFeatBtn();
  S.treeReady = false;
  if (S.tab === 'file') { S.treeReady = true; renderWorkspace(); }
  else renderWriteBtn();
}

function byModelName(a, b) {
  return (a.name || '').toLowerCase() < (b.name || '').toLowerCase() ? -1 : 1;
}

async function refreshModels(quiet) {
  const seq = ++S.probeSeq;
  if (!quiet) setConn('connecting');
  try {
    if (S.provider === 'openai') {
      const data = await oaJson('/models', null, 20000);
      if (seq !== S.probeSeq) return;
      S.models = (data.data || []).map(function (m) { return { name: m.id }; })
        .filter(function (m) { return m.name; }).sort(byModelName);
      S.version = 'OpenAI 相容';
    } else {
      const tags = await apiJson('/api/tags');
      if (seq !== S.probeSeq) return;          // 已被更新的探測取代
      S.models = (tags.models || []).slice().sort(byModelName);
      try {
        const v = await apiJson('/api/version', null, 5000);
        S.version = (v && v.version) || '?';
      } catch (e) { S.version = '?'; }
    }
    if (seq !== S.probeSeq) return;

    if (!S.models.length) { setConn('empty'); renderModelBtn(); return; }
    const names = S.models.map(function (m) { return m.name; });
    if (names.indexOf(S.model) < 0) S.model = names[0];
    setConn('ok');
    renderModelBtn();
    ensureCaps(S.model);
  } catch (err) {
    if (seq !== S.probeSeq) return;
    const f = friendlyError(err);
    setConn('error', f.hint ? f.msg + '\n' + f.hint : f.msg);
  }
}

async function ensureCaps(model) {
  if (!model) return;
  // OpenAI 相容 API 沒有能力查詢，一律當作沒有 thinking / tools / vision
  if (S.provider === 'openai') { S.caps[model] = []; applyCaps(model); return; }
  if (S.caps[model]) { applyCaps(model); return; }
  try {
    const info = await apiJson('/api/show', { model: model }, 20000);
    S.caps[model] = info.capabilities || [];
    // 層數的鍵前面帶著架構名（qwen35.block_count、llama.block_count…），所以用找的
    const mi = info.model_info || {};
    const key = Object.keys(mi).filter(function (k) { return /\.block_count$/.test(k); })[0];
    if (key) S.layers[model] = mi[key];
    // context 上限同理（qwen35.context_length、llama.context_length…）。
    // num_ctx 填得比這個大時，Ollama 不會報錯，它就是默默用模型的上限 ——
    // 使用者以為自己有 256K，實際上是 32K，然後又撞回「模型好像變笨了」。
    const ck = Object.keys(mi).filter(function (k) { return /\.context_length$/.test(k); })[0];
    if (ck) S.ctxMax[model] = mi[ck];
  } catch (e) { S.caps[model] = []; }
  if (model === S.model) applyCaps(model);
}

function applyCaps(model) {
  const caps = S.caps[model] || [];
  $('capThink').hidden = caps.indexOf('thinking') < 0;
  $('capTools').hidden = caps.indexOf('tools') < 0;
  $('capVision').hidden = caps.indexOf('vision') < 0;
  applyParamLimits();
  $('attachBtn').disabled = !!S.blocked;          // 文字檔任何模型都吃得下
  renderFeatBtn();
  if (caps.indexOf('vision') < 0 && S.images.length) { S.images = []; renderAttach(); }
  renderThinkSeg();
}

