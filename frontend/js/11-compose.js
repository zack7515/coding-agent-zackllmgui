// 輸入區：附件、context 用量、系統提示預設、斜線與 @ 功能表。
// 共同點是「送出之前」——都在改那一則要送出去的訊息。

/* ══════════════════════ 附件 ══════════════════════ */
function extOf(name) {
  const base = String(name || '').toLowerCase();
  const dot = base.lastIndexOf('.');
  return dot < 0 ? base : base.slice(dot + 1);
}

// 文字檔進 context 一律包成 code block：模型比較不會把檔案內容誤讀成指令，
// 縮排與行號也不會被 markdown 吃掉。
function fenceFor(name, text) {
  const lang = EXT_LANG[extOf(name)];
  const tag = lang === undefined ? '' : lang;
  let fence = '```';
  while (text.indexOf(fence) >= 0) fence += '`';   // 內容本身有 ``` 就把圍籬加長
  return '檔案：' + name + '\n' + fence + tag + '\n' + text.replace(/\s+$/, '') + '\n' + fence;
}

function chip(label, onOpen, onRemove) {
  const el = document.createElement('span');
  el.className = 'file-chip' + (onOpen ? ' editable' : '');
  const t = document.createElement('span');
  t.textContent = label;
  if (onOpen) { t.title = '點一下可以看內容或修改'; t.addEventListener('click', onOpen); }
  const x = document.createElement('button');
  x.className = 'x';
  x.textContent = '×';
  x.title = '移除這個附件';
  x.addEventListener('click', onRemove);
  el.appendChild(t);
  el.appendChild(x);
  return el;
}

function renderAttach() {
  const bar = $('attachBar');
  const box = $('attachText');
  const n = S.images.length + S.files.length;
  bar.classList.toggle('show', n > 0);
  box.innerHTML = '';
  S.images.forEach(function (img, i) {
    box.appendChild(chip(img.name, null, function () {
      S.images.splice(i, 1); renderAttach();
    }));
  });
  S.files.forEach(function (f, i) {
    const size = f.text.length > 1000 ? (f.text.length / 1000).toFixed(1) + 'k' : f.text.length;
    box.appendChild(chip(f.name + ' · ' + size + ' 字',
      function () { openFileEditor(i); },
      function () { S.files.splice(i, 1); renderAttach(); }));
  });
  updateCtx();
}

// 貼上或拖進來的內容常常要改一下（刪掉不相干的段落、去掉密碼），
// 只能整個刪掉重來太粗暴。
function openFileEditor(i) {
  const f = S.files[i];
  if (!f) return;
  S.editing = i;
  $('fileName').value = f.name;
  $('fileText').value = f.text;
  $('fileMeta').textContent = f.text.length + ' 字元 · 約 ' + estTokens(f.text) + ' tokens'
    + ' · 送出時會包成 ' + (EXT_LANG[extOf(f.name)] || '無標籤') + ' 的 code block';
  $('fileOverlay').classList.remove('hidden');
  $('fileText').focus();
}

function saveFileEditor() {
  const f = S.files[S.editing];
  if (f) {
    f.name = $('fileName').value.trim() || f.name;
    f.text = $('fileText').value;
    if (!f.text.trim()) S.files.splice(S.editing, 1);      // 清空等於刪掉
  }
  $('fileOverlay').classList.add('hidden');
  renderAttach();
}

async function addFile(file) {
  const ext = extOf(file.name);
  if (/^image\//.test(file.type)) {
    if ((S.caps[S.model] || []).indexOf('vision') < 0) toast('目前模型沒有 vision 能力，圖片可能不會被讀取');
    const data = await new Promise(function (ok) {
      const fr = new FileReader();
      fr.onload = function () { ok(String(fr.result).split(',')[1] || ''); };
      fr.readAsDataURL(file);
    });
    S.images.push({ name: file.name, data: data });
    renderAttach();
    return;
  }
  if (DOC_EXT.indexOf(ext) >= 0) {
    // PDF / Word 這種二進位檔瀏覽器讀不動，交給 serve.py 解析
    if (!S.srv.extract) { toast('解析 ' + ext.toUpperCase() + ' 需要用 serve.py 啟動'); return; }
    toast('解析 ' + file.name + '…');
    const res = await fetch(apiUrl('/extract'), {
      method: 'POST', body: file, headers: { 'X-Filename': encodeURIComponent(file.name) }
    });
    const data = await res.json();
    if (!res.ok) { toast(data.error || '解析失敗'); return; }
    if (!(data.text || '').trim()) { toast(file.name + ' 解析結果是空的（可能是掃描檔）'); return; }
    S.files.push({ name: file.name, text: data.text });
    renderAttach();
    return;
  }
  const text = await file.text();
  if (text.indexOf('\u0000') >= 0) { toast(file.name + ' 看起來是二進位檔，略過'); return; }
  S.files.push({ name: file.name, text: text });
  renderAttach();
}

// 貼上一大段文字時猜個副檔名，code block 才標得出語言
function guessExt(text) {
  const head = text.slice(0, 4000);
  const trimmed = text.trim();
  if (/^[{[]/.test(trimmed) && /"\s*:/.test(head)) return 'json';
  if (/^\s*(def |class |import |from \w+ import|print\()/m.test(head)) return 'py';
  if (/\b(function |const |let |=>|console\.log|require\()/.test(head)) return 'js';
  if (/^\s*(SELECT|INSERT INTO|UPDATE |CREATE TABLE)\b/im.test(head)) return 'sql';
  if (/^#!.*\b(bash|sh|zsh)\b|^\s*(sudo|apt|yum|cd|git|docker|curl) /m.test(head)) return 'sh';
  if (/^\s*(#include|int main\s*\()/m.test(head)) return 'c';
  if (/<\/[a-z][\w-]*>/i.test(head)) return 'html';
  if (/^\s*[\w.-]+:\s*\S/m.test(head) && /^\s{2,}\S/m.test(head)) return 'yaml';
  if (/^(#{1,6} |\s*[-*] |\d+\. )/m.test(head)) return 'md';
  return 'txt';
}

function attachPasted(text) {
  S.pasteSeq = (S.pasteSeq || 0) + 1;
  const name = '貼上內容-' + S.pasteSeq + '.' + guessExt(text);
  S.files.push({ name: name, text: text });
  renderAttach();
  toast('貼上的 ' + text.length + ' 個字已收成附件：' + name);
  return name;
}

function pickFiles() {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.addEventListener('change', function () {
    Array.prototype.slice.call(input.files || []).forEach(function (f) {
      addFile(f).catch(function (e) { toast('讀取 ' + f.name + ' 失敗：' + e.message); });
    });
  });
  input.click();
}

/* ══════════════════════ context 用量 ══════════════════════ */
// 粗估：CJK 一字約一 token，其餘約四字元一 token。每次回覆結束後用真實的
// prompt_eval_count 校正 S.ctxRatio，估算就會慢慢貼近該模型的 tokenizer。
// 統一用 K = 1024，跟 num_ctx 欄位同一個單位 —— 兩種 K 混用只會讓人算不清楚
function fmtK(n) {
  const v = Math.max(0, Math.round(n || 0));
  return v < 1024 ? v + ' 個' : (v / 1024).toFixed(1) + 'K';
}

function estTokens(text) {
  const str = String(text || '');
  const cjk = (str.match(/[㐀-鿿豈-﫿぀-ヿ가-힯]/g) || []).length;
  return Math.round(cjk + (str.length - cjk) / 4);
}

function rawEstimate(extra) {
  const c = current();
  let n = estTokens($('system').value);
  if (c) c.messages.forEach(function (m) {
    n += estTokens(m.content) + (m.images ? 800 * m.images.length : 0);
  });
  S.files.forEach(function (f) { n += estTokens(fenceFor(f.name, f.text)); });
  return n + estTokens(extra || '');
}

// num_ctx 欄位（單位 K）換算成 token。允許小數：1.5 就是 1536。
function ctxTokens(raw) {
  const k = parseFloat(String(raw === undefined ? '' : raw).replace(/k$/i, '').trim());
  return isNaN(k) || k <= 0 ? 0 : Math.round(k * 1024);
}

function ctxLimit() { return ctxTokens($('num_ctx').value) || 4096; }

function updateCtx() {
  const limit = ctxLimit();
  const used = Math.round(rawEstimate($('input').value) * S.ctxRatio);
  const pct = Math.min(100, used / limit * 100);
  const tight = used >= limit * 0.75;
  // 「12.3k / 64k（19%）」：用量條與壓縮鍵的提示共用同一份字串
  const nice = function (n) { return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); };
  S.ctxLabel = nice(used) + ' / ' + nice(limit) + '（' + Math.round(used / limit * 100) + '%）';
  $('ctxRow').hidden = used < limit * 0.05;        // 幾乎是空的就不佔版面
  $('ctxRow').style.cursor = tight ? 'pointer' : '';
  $('ctxRow').title = tight ? '點一下壓縮較早的訊息' : '';
  $('ctxFill').style.width = pct + '%';
  $('ctxFill').className = used >= limit ? 'over' : (pct >= 75 ? 'warn' : '');
  // num_ctx 比模型支援的還大時，多出來的部分是假的：Ollama 會默默用模型的上限。
  // 不自動改小（那等於偷改使用者填的數字），但一定要講。
  const cap = S.ctxMax[S.model] || 0;
  const over = cap && limit > cap
    ? ' · ' + (S.model || '這個模型') + ' 最多 ' + Math.round(cap / 1024) + 'K，多填的沒有用'
    : '';
  $('ctxText').textContent = S.ctxLabel +
    (used >= limit ? ' 已超出 num_ctx' : '') + (tight ? ' · 點這裡壓縮' : '') + over;
  $('ctxRow').hidden = $('ctxRow').hidden && !over;      // 只有這個警告時也要看得到
  if (tight) preCompact();      // 快滿了就先在背景把摘要算起來放著
  renderCompactBtns();
}

/* ══════════════════════ 系統提示預設 ══════════════════════ */
function allPresets() {
  return PRESETS.concat(S.presets.map(function (x) { return [x.name, x.text]; }));
}

function openPresetMenu() {
  const items = allPresets().map(function (pair) {
    return {
      label: pair[0],
      meta: pair[1].length > 26 ? pair[1].slice(0, 26) + '…' : pair[1],
      action: function () {
        $('system').value = pair[1];
        S.params.system = pair[1];
        saveConfig(); updateCtx();
        toast('已套用「' + pair[0] + '」');
      }
    };
  });
  items.push('-', { label: '清空系統提示', action: function () {
    $('system').value = ''; S.params.system = ''; saveConfig(); updateCtx();
  } });
  if (S.presets.length) {
    items.push({ label: '刪除自訂預設…', action: function () {
      showMenu($('presetBtn'), S.presets.map(function (x, i) {
        return { label: x.name, action: function () {
          S.presets.splice(i, 1); lsSet(LS_PRESETS, S.presets); toast('已刪除「' + x.name + '」');
        } };
      }));
    } });
  }
  showMenu($('presetBtn'), items);
}

function savePreset() {
  const text = ($('system').value || '').trim();
  if (!text) { toast('系統提示是空的'); return; }
  const name = (prompt('這組預設要叫什麼？', '我的預設') || '').trim();
  if (!name) return;
  S.presets = S.presets.filter(function (x) { return x.name !== name; });
  S.presets.push({ name: name, text: text });
  lsSet(LS_PRESETS, S.presets);
  toast('已存成預設「' + name + '」');
}

/* ══════════════════════ 斜線功能表 ══════════════════════ */
// 打 / 就看得到有哪些功能，不用先去翻選單。skill 選下去會變成一個附件，
// 使用者接著打自己的需求 —— 這樣它跟一般的附件走同一條路，不必另外做機制。
async function loadSkills() {
  if (S.skills) return S.skills;
  try {
    const res = await fetch(apiUrl('/skills'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const data = await res.json();
    S.skills = data.skills || [];
  } catch (e) { S.skills = []; }
  return S.skills;
}

function slashItems(q) {
  const key = q.toLowerCase();
  const hit = function (name, desc) {
    return !key || (name + ' ' + desc).toLowerCase().indexOf(key) >= 0;
  };
  const cmds = SLASH_CMDS.filter(function (c) { return hit(c[0], c[1]); })
    .map(function (c) { return { kind: '功能', key: c[0], desc: c[1], run: c[2] }; });
  const skills = (S.skills || []).filter(function (s) { return hit(s.name, s.description); })
    .map(function (s) {
      return {
        // 同名時工作區的會蓋掉內建的，所以要看得出來現在用的是哪一份
        kind: 'skill', key: s.name,
        desc: (s.scope === '專案' ? '［專案］' : '') + s.description,
        run: function () { attachSkill(s.name); }
      };
    });
  return cmds.concat(skills);
}

function renderSlash() {
  const bar = $('slashBar');
  const items = S.slash.items;
  bar.hidden = !items.length;
  if (!items.length) return;
  bar.innerHTML = '';
  let kind = '';
  items.forEach(function (it, i) {
    if (it.kind !== kind) {
      kind = it.kind;
      const h = document.createElement('div');
      h.className = 'hd';
      h.textContent = kind === 'skill' ? 'SKILLS（選了會附上它的步驟）'
        : (kind === '檔案' ? '工作區的檔案（選了會附上內容）' : '功能');
      bar.appendChild(h);
    }
    const b = document.createElement('button');
    b.className = 'sc' + (i === S.slash.at ? ' on' : '');
    b.innerHTML = '<span class="k"></span><span class="d"></span>';
    b.querySelector('.k').textContent = (it.prefix || '/') + it.key;
    b.querySelector('.d').textContent = it.desc;
    b.addEventListener('click', function () { runSlash(i); });
    bar.appendChild(b);
  });
}

function slashQuery() {
  const v = $('input').value;
  // 只認「整段訊息就是一個指令」的情況：句子中間的斜線是內容不是指令
  return /^\/[^\s\/]*$/.test(v) ? v.slice(1) : null;
}

// @ 跟 / 不一樣：它是**句子中間**用的（「看一下 @src/app.py 這一段」），
// 所以比對的是游標前的最後一個 @ 詞，不是整段訊息。
function atQuery() {
  const el = $('input');
  const head = el.value.slice(0, el.selectionStart === undefined
    ? el.value.length : el.selectionStart);
  const m = /(?:^|\s)@([^\s@]*)$/.exec(head);
  return m ? m[1] : null;
}

// 檔案清單只拉一次，之後用快取 —— 每打一個字就掃一次工作區太蠢。
async function ensureFileList() {
  if (S.atFiles || !S.ws.path) return;
  S.atFiles = [];                              // 先佔位，免得連打好幾次就發好幾個請求
  try {
    const res = await fetch(apiUrl('/ls'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flat: true })
    });
    const data = await res.json();
    if (res.ok) S.atFiles = data.files || [];
  } catch (e) { /* 拉不到就當作沒有，@ 只是方便，不是必要 */ }
}

function atItems(q) {
  const key = q.toLowerCase();
  const files = S.atFiles || [];
  // 資料夾排在前面：想附整包的人通常打的是資料夾名，不該被同名的檔案洗掉
  const dirs = dirsOf(files)
    .filter(function (d) { return !key || d.toLowerCase().indexOf(key) >= 0; })
    .slice(0, 8)
    .map(function (d) {
      const n = filesUnder(files, d).length;
      return { kind: '資料夾', key: d, prefix: '@', desc: n + ' 個檔案',
               run: function () { attachDir(d); } };
    });
  return dirs.concat(files
    .filter(function (p) { return !key || p.toLowerCase().indexOf(key) >= 0; })
    .slice(0, 30 - dirs.length)
    .map(function (p) {
      const cut = p.lastIndexOf('/');
      return { kind: '檔案', key: p, prefix: '@',
               desc: cut > 0 ? p.slice(0, cut) : '',
               run: function () { attachAt(p); } };
    }));
}

// `@資料夾/`：把底下的檔案一次附上去。**有上限而且會講清楚** ——
// 靜靜截斷的話，使用者會以為模型看得到全部，然後對著缺一半的資料下判斷。
async function attachDir(dir) {
  const all = filesUnder(S.atFiles || [], dir);
  if (!all.length) { toast(dir + ' 底下沒有檔案'); return; }
  insertAtText(dir);
  const take = all.slice(0, AT_DIR_FILES);
  let chars = 0, added = 0;
  const got = await Promise.all(take.map(async function (p) {
    try {
      const res = await fetch(apiUrl('/view'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: p })
      });
      const d = await res.json();
      return res.ok ? { name: p, text: d.text || '' } : null;
    } catch (e) { return null; }
  }));
  got.forEach(function (f) {
    if (!f || chars + f.text.length > AT_DIR_CHARS) return;
    if (S.files.some(function (x) { return x.name === f.name; })) return;
    S.files.push(f); chars += f.text.length; added += 1;
  });
  renderAttach();
  updateCtx();
  const skipped = all.length - added;
  toast('附上 ' + dir + ' 底下 ' + added + ' 個檔案（' + chars.toLocaleString() + ' 字）'
    + (skipped > 0 ? '；還有 ' + skipped + ' 個沒附上（超過上限）' : ''));
  $('input').focus();
}

// 把 @查詢 換成實際的路徑。檔案與資料夾共用。
function insertAtText(text) {
  const el = $('input');
  const at = el.selectionStart === undefined ? el.value.length : el.selectionStart;
  const head = el.value.slice(0, at).replace(/@[^\s@]*$/, '@' + text + ' ');
  el.value = head + el.value.slice(at);
  el.selectionStart = el.selectionEnd = head.length;
  autoGrow();
  updateSlash();
}

// 選了之後：把 @查詢 換成完整路徑（句子讀起來才順），同時把檔案附上去。
// 只插路徑不附內容的話，模型還要再花一輪 read_file。
async function attachAt(path) {
  const el = $('input');
  insertAtText(path);
  try {
    const res = await fetch(apiUrl('/view'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (!S.files.some(function (f) { return f.name === path; })) {
      S.files.push({ name: path, text: data.text || '' });
      renderAttach();
      updateCtx();
    }
  } catch (e) {
    toast('附不上 ' + path + '：' + e.message);
  }
  el.focus();
}

function updateSlash() {
  const at = atQuery();
  if (at !== null) {
    ensureFileList().then(function () {
      if (atQuery() === null) return;          // 等的時候使用者已經打別的了
      S.slash = { items: atItems(atQuery()), at: 0 };
      renderSlash();
    });
    return;
  }
  const q = slashQuery();
  if (q === null) { S.slash = { items: [], at: 0 }; $('slashBar').hidden = true; return; }
  S.slash = { items: slashItems(q), at: 0 };
  renderSlash();
}

function moveSlash(d) {
  const n = S.slash.items.length;
  if (!n) return;
  S.slash.at = (S.slash.at + d + n) % n;
  renderSlash();
  const on = $('slashBar').querySelector('.sc.on');
  if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest' });
}

function runSlash(i) {
  const it = S.slash.items[i === undefined ? S.slash.at : i];
  if (!it) return;
  // @ 是插在句子中間的，不能把整行清掉 —— 那是 / 指令才對的行為
  if (it.prefix !== '@') {
    $('input').value = '';
    autoGrow();
    updateSlash();
  }
  it.run();
}

async function attachSkill(name) {
  try {
    const res = await fetch(apiUrl('/skills'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    S.files.push({ name: 'skill-' + name + '.md', text: data.body });
    renderAttach();
    updateCtx();
    toast('已附上 ' + name + ' 的步驟，接著描述你要做什麼');
    $('input').focus();
  } catch (e) {
    toast('讀不到這個 skill：' + e.message);
  }
}
