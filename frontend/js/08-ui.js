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

/* ══════════════════════ 模型管理 ══════════════════════ */
function mmRow(name, meta, btnLabel, danger, action) {
  const row = document.createElement('div');
  row.className = 'mm-row';
  row.innerHTML = '<span class="n"></span><span class="m"></span>' +
    '<button class="mini' + (danger ? ' danger' : '') + '"></button>';
  row.querySelector('.n').textContent = name;
  row.querySelector('.m').textContent = meta;
  const btn = row.querySelector('button');
  btn.textContent = btnLabel;
  btn.addEventListener('click', function () { action(btn); });
  return row;
}

async function refreshModelManager() {
  const mm = $('mmList'), ps = $('psList');
  const external = S.provider === 'openai';
  $('pullRow').hidden = external;
  mm.textContent = '載入中…';
  await refreshModels(true);
  mm.innerHTML = '';
  S.models.forEach(function (m) {
    const meta = [(m.details && m.details.parameter_size) || '',
      m.size ? humanSize(m.size) : ''].filter(Boolean).join(' · ');
    if (external) {
      const row = document.createElement('div');
      row.className = 'mm-row';
      row.innerHTML = '<span class="n"></span>';
      row.querySelector('.n').textContent = m.name;
      mm.appendChild(row);
      return;
    }
    mm.appendChild(mmRow(m.name, meta, '刪除', true, function () { deleteModel(m.name); }));
  });
  if (!S.models.length) mm.textContent = '（這台主機還沒有下載任何模型）';

  if (external) {
    ps.textContent = '（外部 API 不提供下載、刪除與記憶體資訊）';
    return;
  }
  ps.textContent = '載入中…';
  try {
    const data = await apiJson('/api/ps', null, 5000);
    const running = data.models || [];
    ps.innerHTML = '';
    running.forEach(function (m) {
      ps.appendChild(mmRow(m.name, humanSize(m.size || 0) + ' 記憶體', '卸載', false, function (btn) {
        btn.disabled = true;
        unloadModel(m.name);
      }));
    });
    if (!running.length) ps.textContent = '（目前沒有模型佔用記憶體）';
  } catch (e) {
    ps.textContent = '讀不到 /api/ps：' + friendlyError(e).msg;
  }
}

async function deleteModel(name) {
  if (!confirm('確定刪除模型「' + name + '」？這會從主機上移除檔案。')) return;
  try {
    const res = await fetch(apiUrl('/api/delete'), {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: name })
    });
    if (!res.ok) throw new Error(await res.text());
    toast('已刪除 ' + name);
    await refreshModelManager();
  } catch (e) { toast('刪除失敗：' + friendlyError(e).msg); }
}

async function unloadModel(name) {
  // keep_alive 0 就是「馬上放掉」，Ollama 沒有專門的 unload API
  try {
    await apiJson('/api/generate', { model: name, keep_alive: 0 }, 15000);
    toast('已卸載 ' + name);
  } catch (e) { toast('卸載失敗：' + friendlyError(e).msg); }
  await refreshModelManager();
}

// Hugging Face 的 GGUF 走 hf.co/{使用者}/{儲存庫}:{量化}；
// 使用者多半是直接複製網址或整行指令，這裡一併收下。
function normalizePull(v) {
  let t = String(v || '').trim();
  t = t.replace(/^ollama\s+(pull|run)\s+/i, '').replace(/^["']|["']$/g, '').trim();
  t = t.replace(/^https?:\/\//i, '');
  const hf = /^(?:hf\.co|huggingface\.co)\/([^/\s]+)\/([^/\s:?#]+)(?::([\w.-]+))?/i.exec(t);
  if (hf) return 'hf.co/' + hf[1] + '/' + hf[2] + (hf[3] ? ':' + hf[3] : '');
  return t.replace(/\/+$/, '');
}

async function pullModel() {
  const name = normalizePull($('pullInput').value);
  if (!name) { toast('先填模型名稱，例如 qwen3:8b'); return; }
  $('pullInput').value = name;                 // 讓使用者看到實際會送出的名字
  $('pullProgress').hidden = false;
  $('pullBtn').disabled = true;
  $('pullFill').style.width = '0%';
  $('pullText').textContent = '連線中…';
  S.pullAbort = new AbortController();
  try {
    await streamNdjson('/api/pull', { model: name, stream: true }, S.pullAbort.signal, function (obj) {
      const pct = obj.total ? (obj.completed || 0) / obj.total * 100 : 0;
      $('pullFill').style.width = pct + '%';
      $('pullText').textContent = (obj.status || '') +
        (obj.total ? '  ' + humanSize(obj.completed || 0) + ' / ' + humanSize(obj.total) : '');
    });
    toast(name + ' 下載完成');
    $('pullInput').value = '';
    await refreshModelManager();
  } catch (e) {
    const f = friendlyError(e);
    toast(f.abort ? '已取消下載' : ('下載失敗：' + f.msg));
  } finally {
    $('pullProgress').hidden = true;
    $('pullBtn').disabled = false;
    S.pullAbort = null;
  }
}

function openModels() {
  $('modelsOverlay').classList.remove('hidden');
  refreshModelManager();
}

/* ══════════════════════ 多模型比較 ══════════════════════ */
function openCompare() {
  if (!S.models.length) { toast('還沒有可用的模型'); return; }
  ['A', 'B'].forEach(function (k, i) {
    const sel = $('cmpModel' + k);
    sel.innerHTML = '';
    S.models.forEach(function (m) {
      const o = document.createElement('option');
      o.value = m.name; o.textContent = m.name;
      sel.appendChild(o);
    });
    sel.value = (i === 0 ? (S.model || S.models[0].name)
      : (S.models[1] || S.models[0]).name);
  });
  $('cmpOverlay').classList.remove('hidden');
  $('cmpPrompt').focus();
}

async function runCompare() {
  const prompt = $('cmpPrompt').value.trim();
  if (!prompt) { toast('先寫一個提示'); return; }
  const sys = ($('system').value || '').trim();
  const msgs = (sys ? [{ role: 'system', content: sys }] : []).concat([{ role: 'user', content: prompt }]);
  const opts = buildOptions();
  $('cmpRun').disabled = true;
  await Promise.all(['A', 'B'].map(function (k) {
    const model = $('cmpModel' + k).value;
    const body = $('cmpBody' + k), stat = $('cmpStat' + k);
    body.innerHTML = '<span class="caret"></span>';
    stat.textContent = '產生中…';
    if (!model) { stat.textContent = '沒有選模型'; return Promise.resolve(); }
    const t0 = performance.now();
    let out = '';
    return chatStream({ model: model, messages: msgs, stream: true, options: opts }, null, {
      think: function () { /* 比較面板不顯示思考過程 */ },
      images: function () { },
      tools: function () { },
      content: function (t) {
        out += t;
        body.innerHTML = renderMarkdown(out);
        body.scrollTop = body.scrollHeight;
      },
      done: function (info) {
        const ec = (info && info.eval_count) || 0, ed = ((info && info.eval_duration) || 0) / 1e9;
        stat.textContent = ec + ' tokens · ' + (ed > 0 ? (ec / ed).toFixed(1) + ' tok/s · ' : '') +
          ((performance.now() - t0) / 1000).toFixed(1) + 's';
      }
    }).catch(function (e) { stat.textContent = friendlyError(e).msg; });
  }));
  $('cmpRun').disabled = false;
}

function errorHint(msg) {
  const m = (msg || '').toLowerCase();
  if (m.indexOf('does not support thinking') >= 0)
    return '這個模型不支援 thinking，請把右側的思考模式切成關閉。';
  if (m.indexOf('not found') >= 0) return '請先下載模型：ollama pull ' + S.model;
  if (m.indexOf('memory') >= 0) return '記憶體不足，試著調小 num_ctx 或改用較小的模型。';
  return '';
}

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

/* ══════════════════════ 子代理：定位、追溯、中斷 ══════════════════════ */
// 卡片捲走了、或是從紀錄／背景指令那邊只拿到一個 id 時，這是唯一找得回去的路。
// 中斷是**伺服器**那一端的事：標記之後連後代與它們的背景指令一起停，
// 而且任何綁在那些 id 上的工具呼叫都會被拒絕 —— 網頁不理也叫不動。
async function openAgents() {
  let data;
  try { data = await agentCall({ action: 'list' }); }
  catch (e) { toast('問不到子代理：' + e.message); return; }

  const el = msgEl('assistant');
  el.innerHTML =
    '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
    '<div class="msg-col"><div class="tool-card"><div class="th">子代理</div>' +
    '<div class="agents"></div></div></div>';
  const boxEl = el.querySelector('.agents');
  const list = (data.agents || []).slice().sort(function (a, b) {
    return a.depth - b.depth || a.started - b.started;
  });

  if (!list.length) {
    const p = document.createElement('div');
    p.className = 'muted';
    p.textContent = '現在沒有子代理在跑。可用的型別：'
      + (data.types || []).map(function (t) { return t.name; }).join('、')
      + '（最多 ' + data.depth_max + ' 層）';
    boxEl.appendChild(p);
  }

  // 沒人認得的 worktree：serve.py 重啟過，登記沒了但資料夾還在磁碟上。
  // 這裡是唯一收得掉它們的地方 —— 列不出來就等於收不掉。
  (data.orphans || []).forEach(function (o) {
    const row = document.createElement('div');
    row.className = 'agent-row';
    const info = document.createElement('div');
    const bits = [o.id, '沒人認得的 worktree', o.branch];
    if (o.changes) bits.push(o.changes + ' 個未提交的改動');
    if (o.gone) bits.push('資料夾不在了');
    if (o.msg) bits.push(o.msg);
    info.textContent = bits.join(' · ');
    row.appendChild(info);
    const btn = document.createElement('button');
    btn.className = 'mini';
    btn.textContent = '收掉';
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      try {
        const r = await agentCall({ action: 'close', id: o.id });
        info.textContent += r.commits
          ? ' · 資料夾收掉了，成果留在 ' + r.branch + '（' + r.merge + '）'
          : ' · 收掉了（分支上是空的，一起刪了）';
      } catch (e) { info.textContent += ' · 收不掉：' + e.message; }
    });
    row.appendChild(btn);
    boxEl.appendChild(row);
  });

  list.forEach(function (a) {
    const row = document.createElement('div');
    row.className = 'agent-row';
    const info = document.createElement('div');
    // 全部用 textContent：型別名字來自 agents/*.md，那是檔案內容不是我們寫的字串
    const bits = [a.id, a.type, '第 ' + a.depth + ' 層'];
    if (a.parent) bits.push('上層 ' + a.parent);
    if (a.branch) bits.push(a.branch);
    // 借過去的是同一份資料夾，不是複本 —— 在裡面裝套件會動到主專案
    if (a.linked && a.linked.length) bits.push('借用 ' + a.linked.join('、'));
    bits.push(a.calls + ' 次工具');
    bits.push(a.secs + ' 秒');
    if (a.last) bits.push('最後 ' + a.last.tool);
    if (a.jobs && a.jobs.length) bits.push('背景 ' + a.jobs.join('、'));
    if (a.stopped) bits.push('已中斷：' + a.why);
    info.textContent = bits.join(' · ');
    row.appendChild(info);
    if (!a.stopped) {
      const btn = document.createElement('button');
      btn.className = 'mini';
      btn.textContent = '中斷';
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        try {
          const r = await agentCall({ action: 'stop', id: a.id, why: '從子代理清單中斷' });
          if (S.subs) r.stopped.forEach(function (k) {
            Object.keys(S.subs).forEach(function (key) {
              if (S.subs[key].sid === k || key === k) S.subs[key].stopped = true;
            });
          });
          info.textContent += ' · 已中斷 ' + r.stopped.join('、')
            + (r.jobs.length ? '（連背景指令 ' + r.jobs.join('、') + '）' : '');
        } catch (e) { info.textContent += ' · 中斷失敗：' + e.message; }
      });
      row.appendChild(btn);
    }
    boxEl.appendChild(row);
  });
  $('thread').appendChild(el);
  pin();
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
    ['兩種還原點', 'C（檢查點）＝送出每一則提示之前的完整快照，退回去是整個工作區；'
      + 'M／A＝單獨一次改檔案，只退那一個檔案。'],
    ['為什麼要有 C', '單筆還原點只有 write_file／edit_file／delete_file 會留。'
      + '模型用 run_shell 下 sed、npm、>> 改的東西不在裡面 —— 檢查點補的就是這一段。'],
    ['C 需要 git', '快照是 git 的 shadow commit（用臨時 index 做，你的 HEAD、分支、'
      + '暫存區都不會被動到）。工作區不是 git repo 就只有單筆還原點。'],
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
