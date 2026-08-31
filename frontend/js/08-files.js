// 右側面板：檔案檢視、工作區、檔案樹、選資料夾、還原點。
// 這幾段幾乎不跟工具那半邊講話（量過：只借四個名字），所以搬得乾淨。

/* ══════════════════════ 檔案檢視器 ══════════════════════ */
// 一行一個 div：行號 sticky 在左邊，橫向捲動時不會跑掉。
function codeLines(text, cls) {
  const lines = String(text).split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines.map(function (line, i) {
    return '<div class="cl' + (cls ? ' ' + cls : '') + '"><span class="ln">' + (i + 1) +
      '</span><span class="lt">' + esc(line || ' ') + '</span></div>';
  }).join('');
}

// unified diff 也要行號：debug 時「第幾行」比「加了什麼」更常被問。
// 從 @@ -a,b +c,d @@ 追新檔的行號，刪除行沒有新行號就留白。
function diffLines(diff) {
  let n = 0;
  return String(diff).split('\n').map(function (line) {
    if (line.indexOf('@@') === 0) {
      const m = /\+(\d+)/.exec(line);
      n = m ? parseInt(m[1], 10) : n;
      return '<div class="cl hunk"><span class="ln"></span><span class="lt">' +
        esc(line) + '</span></div>';
    }
    if (line.indexOf('---') === 0 || line.indexOf('+++') === 0) {
      return '<div class="cl hunk"><span class="ln"></span><span class="lt">' +
        esc(line) + '</span></div>';
    }
    const c = line.charAt(0);
    const cls = c === '+' ? 'add' : (c === '-' ? 'del' : '');
    const num = c === '-' ? '' : String(n++);
    return '<div class="cl ' + cls + '"><span class="ln">' + num +
      '</span><span class="lt">' + esc(line || ' ') + '</span></div>';
  }).join('');
}

// 三個分頁：參數／檔案／紀錄。git 與還原點都是「這個工作區發生過什麼」，
// 跟「工作區裡有哪些檔案」是兩件事，混在同一頁會互相擠。
const TABS = ['params', 'file', 'hist'];

function showTab(which) {
  if (TABS.indexOf(which) < 0) which = 'params';
  $('tabParams').classList.toggle('on', which === 'params');
  $('tabFile').classList.toggle('on', which === 'file');
  $('tabHist').classList.toggle('on', which === 'hist');
  $('pBody').hidden = which !== 'params';
  $('fBody').hidden = which !== 'file';
  $('hBody').hidden = which !== 'hist';
  $('resetBtn').hidden = which !== 'params';
  S.tab = which;
  saveConfig();
  // 切過去才去問後端，開著參數分頁時不必浪費請求
  if (which === 'file' && !S.treeReady) { S.treeReady = true; renderWorkspace(); }
  if (which === 'hist') loadHistory();
}

async function openFile(path, backup, line) {
  if (!S.ws.path) { toast('還沒選工作區資料夾'); return; }
  showPanel('params', true);          // 面板收起來時要先叫出來，否則按了沒反應
  showTab('file');
  $('treeWrap').hidden = true;        // 樹讓位給檢視器，左上角的 ‹ 回得去
  $('viewWrap').hidden = false;
  $('fvPath').textContent = path;
  $('fvBody').innerHTML = '<div class="fv-empty">讀取中…</div>';
  try {
    const res = await fetch(apiUrl('/view'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path, backup: backup || '' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    S.fv = { path: path, backup: backup || '', text: data.text, diff: data.diff || '',
             mode: 'text', line: +line || 0 };
    $('fvReload').hidden = false;
    $('fvDiff').hidden = !data.diff;
    renderFileView();
    if (+line > 0) jumpToLine(+line);
  } catch (e) {
    $('fvBody').innerHTML = '';
    $('fvBody').appendChild(Object.assign(document.createElement('div'),
      { className: 'fv-empty', textContent: '開不起來：' + e.message }));
  }
}

// 捲到某一行並且亮一下。**要亮一下**：捲過去但不標記的話，
// 使用者還是得自己數行號找到底是哪一行。
function jumpToLine(n) {
  const rows = $('fvBody').querySelectorAll('.cl');
  const row = rows[n - 1];
  if (!row) return;
  rows.forEach(function (r) { r.classList.remove('hit'); });
  row.classList.add('hit');
  row.scrollIntoView({ block: 'center' });
}

function renderFileView() {
  const fv = S.fv;
  if (!fv) return;
  const diffMode = fv.mode === 'diff' && fv.diff;
  $('fvBody').innerHTML = diffMode ? diffLines(fv.diff) : codeLines(fv.text);
  if (!diffMode && fv.line > 0) jumpToLine(fv.line);   // 切回原始檢視時要留住標記
  $('fvDiff').textContent = diffMode ? '原始' : '差異';
  $('fvPath').textContent = fv.path + (diffMode ? '（與修改前比較）' : '');
}

/* ══════════════════════ 收合兩側欄 ══════════════════════ */
// 窄畫面本來就會自動收起來，那時候按鍵的意思變成「以浮層叫出來」。
function narrowFor(which) {
  return window.matchMedia(which === 'params' ? '(max-width:1100px)' : '(max-width:820px)').matches;
}

function showPanel(which, on) {
  const body = document.body;
  if (narrowFor(which)) body.classList.toggle('show-' + which, on);
  else body.classList.toggle('hide-' + which, !on);
}

function panelVisible(which) {
  const body = document.body;
  return narrowFor(which) ? body.classList.contains('show-' + which)
    : !body.classList.contains('hide-' + which);
}

function togglePanel(which) {
  showPanel(which, !panelVisible(which));
  renderToggles();
  saveConfig();
}

function renderToggles() {
  // 細條的顯示交給 CSS，這裡只要確保浮層狀態跟 hide-* 不會互相打架
  if (!narrowFor('sidebar')) document.body.classList.remove('show-sidebar');
  if (!narrowFor('params')) document.body.classList.remove('show-params');
}

/* ══════════════════════ 工作區 ══════════════════════ */
function openWorkspaceHelp() {
  openHelp('工作區', [
    ['這是什麼',
     '模型的檔案類工具只看得到這個資料夾裡的東西。沒設定的話，讀檔、搜尋、'
     + '跑測試那些工具根本不會出現在送給模型的清單裡。'],
    ['擋掉哪些路徑',
     '.. 、絕對路徑、指向資料夾外面的 symlink 一律拒絕；'
     + '.git、.venv、node_modules 這些資料夾不開放；'
     + '.env、*.pem、*.key、id_rsa*、credentials*.json 這類機密檔也讀不到。',
     '家目錄與磁碟根目錄不能當工作區 —— 那等於沒有邊界'],
    ['改檔案之前',
     '會先算出 diff 讓你看過再按執行，原檔複製到 .zackllmgui-backup/<時間戳>/，'
     + '結果卡上有「還原這個檔案」。'],
    ['允許修改檔案',
     '再多一道開關。沒勾的話模型只讀得到，write_file 與 edit_file '
     + '連出現在工具清單裡都不會。'],
    ['工具跑在哪一台',
     '永遠是跑 serve.py 的這台 —— 瀏覽器碰不到你的硬碟。'
     + '要用遠端的 GPU 就把 Ollama 指過去（--ollama http://主機:11434），'
     + '網頁與檔案留在自己這台。']
  ], '完整的安全邊界（包含擋不住什麼）寫在 <code>safety/README.md</code>。');
}

// git 當備份用：開工前看清楚工作區乾不乾淨，收工前一鍵 commit。
function renderWsGit() {
  const box = $('wsGit');
  if (!box) return;
  const g = S.ws.git_state || {};
  if (!g.repo) { box.innerHTML = ''; return; }
  box.innerHTML =
    '<div class="note" style="margin-top:2px;">' +
      'git <b>' + esc(g.branch || '?') + '</b> · ' +
      (g.dirty ? g.dirty + ' 個檔案有變更' : '工作區乾淨') +
      (g.stat ? '<pre style="margin:6px 0 0; white-space:pre-wrap; font-size:11px;">'
        + esc(g.stat) + '</pre>' : '') +
    '</div>' +
    (g.dirty ? '<div class="dialog-row" style="margin-top:6px;">' +
      '<button class="mini" data-commit>commit 全部變更</button>' +
      '<button class="mini" data-stash>暫存並還原（stash）</button></div>' : '');
  const commit = box.querySelector('[data-commit]');
  if (commit) {
    commit.addEventListener('click', function () {
      const msg = (prompt('commit 訊息', '模型改的：' + (S.todos[0] || {}).text || '') || '').trim();
      if (msg) gitAction('commit', msg);
    });
    box.querySelector('[data-stash]').addEventListener('click', function () {
      // 用 stash 不用 checkout：丟掉的東西 git stash pop 還救得回來
      if (confirm('把目前所有變更收進 git stash？之後可以用 git stash pop 拿回來。')) {
        gitAction('discard');
      }
    });
  }
}

async function gitAction(action, message) {
  try {
    const res = await fetch(apiUrl('/git'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, message: message || '' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    S.ws.git_state = data;
    renderWsGit();
    toast((data.message || '完成').split('\n')[0]);
  } catch (e) {
    toast('git 失敗：' + e.message);
  }
}

// 工作區的狀態列（檔案分頁的最上面一行）。原本是一個對話框，
// 現在跟檔案樹放在一起 —— 「這是哪個資料夾」跟「裡面有什麼」本來就該一起看。
function renderWorkspace() {
  const blocked = !S.srv.toolsLocal;
  $('wsPick').disabled = blocked;
  $('wsName').textContent = blocked ? '（工具只接受本機）'
    : (S.ws.path || '（未選資料夾）');
  $('wsName').title = S.ws.path
    ? S.ws.path + '\n' + (S.ws.files || 0) + ' 個檔案 · python：' + (S.ws.python || '找不到')
      + (S.ws.project_md ? '\n專案說明：' + S.ws.project_md : '')
    : '';
  renderWsGit();
  renderWriteBtn();
  renderFeatBtn();
  if (!blocked && S.ws.path) renderTree();
  else {
    $('fvTree').innerHTML = '<div class="fv-empty">' + (blocked
      ? (SAME_ORIGIN
        ? '這台 serve.py 只接受本機的檔案請求，而你是從 ' + (S.srv.client || '別台')
          + ' 連過來的。\n\n兩種做法：\n'
          + '1. 在你自己的機器上跑一份 serve.py，--ollama 指向這台 GPU 主機'
          + '（建議，檔案留在你自己那邊）\n'
          + '2. 這台加 --trust-remote 重開 —— 代價是連得到這個網頁的人'
          + '都能在這台機器上動檔案跟跑指令'
        : '直接開 HTML 檔的時候沒有 serve.py，看不到檔案。用 python serve.py 啟動。')
      : '還沒選資料夾。按上面的「選資料夾…」挑一個專案，'
        + '模型的檔案工具就只看得到那個資料夾裡的東西。') + '</div>';
  }
}

// 「工作區…」不再開對話框，直接把右側切到檔案分頁 —— 東西都在那裡了
function openWorkspace() {
  showPanel('params', true);
  showTab('file');
  renderWorkspace();
}

/* ══════════════════════ 檔案樹 ══════════════════════ */
// 像 VS Code 左邊那一欄：資料夾點開才去問後端要下一層（lazy），
// 一次把整棵樹拉下來的話，node_modules 這種東西會把瀏覽器卡死。
async function lsDir(rel) {
  const res = await fetch(apiUrl('/ls'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: rel || '' })
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data.entries || [];
}

function fmtSize(n) {
  if (!n) return '';
  return n < 1024 ? n + 'B'
    : n < 1024 * 1024 ? (n / 1024).toFixed(0) + 'K'
    : (n / 1024 / 1024).toFixed(1) + 'M';
}

function treeRow(entry, depth) {
  const row = document.createElement('div');
  row.className = 'it' + (entry.dir ? ' dir' : '');
  row.style.paddingLeft = (8 + depth * 13) + 'px';
  row.innerHTML = '<span class="tw">' + (entry.dir ? '▶' : '') + '</span>' +
    '<span class="nm"></span>' + (entry.dir ? '' : '<span class="sz"></span>');
  row.querySelector('.nm').textContent = entry.name;
  if (!entry.dir) row.querySelector('.sz').textContent = fmtSize(entry.size);
  row.title = entry.path;
  return row;
}

// open：refreshTree() 傳下來的「重畫前哪些資料夾是開的」，重建之後照樣打開。
// 沒有它的話，模型每寫一個檔就把整棵樹縮回根目錄，沒有人會想開著它。
async function expandInto(box, rel, depth, open) {
  open = open || {};
  // 已經有內容就別閃「讀取中…」：自動重讀時那一下閃爍比不更新還煩
  if (!box.querySelector('.it')) {
    box.innerHTML = '<div class="fv-empty" style="padding:4px 14px;">讀取中…</div>';
  }
  try {
    const entries = await lsDir(rel);
    box.innerHTML = '';
    if (!entries.length) {
      box.innerHTML = '<div class="fv-empty" style="padding:2px 14px;">（空資料夾）</div>';
      return;
    }
    entries.forEach(function (e) {
      const row = treeRow(e, depth);
      const kids = document.createElement('div');
      kids.className = 'kids';
      kids.dataset.rel = e.path;          // refreshTree() 靠這個認出哪一格是開的
      box.appendChild(row);
      box.appendChild(kids);
      if (e.dir && open[e.path]) {
        kids.classList.add('open');
        row.querySelector('.tw').classList.add('open');
        kids.dataset.loaded = '1';
        expandInto(kids, e.path, depth + 1, open);
      }
      row.addEventListener('click', function () {
        if (e.dir) {
          const on = kids.classList.toggle('open');
          row.querySelector('.tw').classList.toggle('open', on);
          if (on && !kids.dataset.loaded) {
            kids.dataset.loaded = '1';
            expandInto(kids, e.path, depth + 1);
          }
          return;
        }
        Array.prototype.forEach.call($('fvTree').querySelectorAll('.it.on'),
          function (x) { x.classList.remove('on'); });
        row.classList.add('on');
        openFile(e.path);
      });
    });
  } catch (err) {
    box.innerHTML = '';
    box.appendChild(Object.assign(document.createElement('div'),
      { className: 'fv-empty', textContent: '讀不到：' + err.message }));
  }
}

function renderTree() {
  showTreeView();
  redrawTree();
}

// 展開狀態不能在重畫時掉。收集現在開著的資料夾，重建時原樣打開。
function openDirs() {
  const out = {};
  Array.prototype.forEach.call($('fvTree').querySelectorAll('.kids.open'),
    function (k) { if (k.dataset.rel) out[k.dataset.rel] = 1; });
  return out;
}

function redrawTree() {
  expandInto($('fvTree'), '', 0, openDirs());
}

// 手動的 ↻，以及工具動過檔案之後的自動重讀。
// 檔案樹本來只在切到這個分頁時讀一次 —— 模型寫了新檔、rm 掉一個資料夾，
// 樹上完全看不出來，只能自己去點別的分頁再切回來。
let treeTimer = null;

function touchTree() {
  S.treeReady = false;      // 沒開著檔案分頁的話，等切過去時自然會重讀
  S.atFiles = null;         // @檔名 的自動完成也是同一份清單，一起作廢
  if (S.tab !== 'file' || !S.ws.path || !S.srv.toolsLocal) return;
  // 一輪常常連改五六個檔，每一個都重畫一次是白做工。收斂成最後一次。
  clearTimeout(treeTimer);
  treeTimer = setTimeout(function () { S.treeReady = true; redrawTree(); }, 400);
}

function showTreeView() {
  $('treeWrap').hidden = false;
  $('viewWrap').hidden = true;
}

/* ══════════════════════ 選資料夾 ══════════════════════ */
// 瀏覽器讀不到你的硬碟（<input type=file> 也只給檔名不給路徑），
// 所以資料夾是跟後端要的。/browse 只回資料夾名稱，不碰任何檔案內容。
async function browseTo(path) {
  const res = await fetch(apiUrl('/browse'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: path || '' })
  });
  const data = await res.json();
  if (!res.ok) { toast(data.error || ('HTTP ' + res.status)); return; }
  S.browse = data;
  // input 要設 value，不是 textContent —— 設 textContent 對 <input> 完全沒作用，
  // 所以框裡一直是空的，只看得到反灰的 placeholder，看不出現在在哪一層。
  $('brPath').value = data.path;
  $('brUp').disabled = !data.parent;
  $('brPickHere').disabled = !data.pickable;
  $('brNote').innerHTML = data.error ? esc(data.error)
    : (data.pickable
      ? '點資料夾往下走，或按「就選這個資料夾」把目前這一層設成工作區。'
      : '家目錄與磁碟根目錄不能當工作區 —— 那等於沒有邊界。往下選一個專案資料夾。');
  const list = $('brList');
  list.innerHTML = '';
  if (!data.dirs.length) {
    list.innerHTML = '<div class="fv-empty">（這一層沒有子資料夾）</div>';
  }
  data.dirs.forEach(function (d) {
    const row = document.createElement('div');
    row.className = 'it';
    row.innerHTML = '<span>📁</span><span class="n"></span>' +
      (d.git ? '<span class="g">git</span>' : '');
    row.querySelector('.n').textContent = d.name;
    row.addEventListener('click', function () {
      browseTo(data.path.replace(/\/+$/, '') + '/' + d.name);
    });
    list.appendChild(row);
  });
}

function openBrowser() {
  $('brOverlay').classList.remove('hidden');
  browseTo(S.ws.path || '');
}

/* ══════════════════════ 還原點（rewind） ══════════════════════ */
// 一輪一個還原點：送出提示前照一張相，那一輪動過的檔案列在底下。
// 順序記在 serve.py 的 journal.jsonl。
function openRewind() {
  showPanel('params', true);
  showTab('hist');            // 內容由 showTab 去載，不然兩邊會各載一次
}

async function loadHistory() {
  // git 狀態存在 S.ws 裡，只有套用工作區與按下 commit 才會更新 ——
  // 模型改完檔案之後它就過期了，所以進來先問一次。
  if (S.ws.path && (S.ws.git_state || {}).repo) {
    try {
      const res = await fetch(apiUrl('/git'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'status' })
      });
      const g = await res.json();
      if (res.ok) S.ws.git_state = g;
    } catch (e) { /* 拿不到就顯示舊的，不值得為此擋住整頁 */ }
  }
  renderWsGit();
  const box = $('rwList');
  const chat = S.currentId;
  box.innerHTML = '<div class="fv-empty">讀取中…</div>';
  try {
    const res = await fetch(apiUrl('/journal'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat: chat })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (chat !== S.currentId) return;      // 讀的時候使用者已經換過對話了
    renderRewind(data.entries || [], data.total || 0);
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(Object.assign(document.createElement('div'),
      { className: 'fv-empty', textContent: '讀不到紀錄：' + e.message }));
  }
}

// 照 VS Code 原始檔控制那一欄的排法，新的在最上面 ——
// 人想的是「退到剛剛」不是「退到最早」。
// 一輪一列：C 是檢查點（這則提示的起點），點下去退整個工作區。
// 沒有檢查點時（工作區不是 git repo）退回一次改檔案一列，A 新建、M 修改。
function rewindRow(e, newest) {
  const row = document.createElement('div');
  row.className = 'sc-row';
  const path = String(e.path || '');
  const cut = path.lastIndexOf('/');
  row.innerHTML = '<span class="st"></span><span class="nm"></span>'
    + '<span class="dir"></span><span class="tm"></span>';
  const st = row.querySelector('.st');
  row.querySelector('.tm').textContent = clockOf(e.ts);
  if (e.tree) {
    const n = (e.files || []).length;
    row.classList.add('ckpt');
    st.textContent = 'C';
    st.className = 'st c';
    row.querySelector('.nm').textContent = path || '（沒有內容）';
    row.querySelector('.dir').textContent = n ? n + ' 個檔案' : '沒改到檔案';
    row.title = e.ts + '　' + '送出這則提示之前的快照\n「' + path + '」'
      + (newest ? '\n最新的一輪' : '')
      + '\n點一下把整個工作區退回這裡 —— 包含 run_shell 改的東西';
    return row;
  }
  const created = !!e.created;
  st.textContent = created ? 'A' : 'M';
  st.className = 'st ' + (created ? 'a' : 'm');
  row.querySelector('.nm').textContent = cut >= 0 ? path.slice(cut + 1) : path;
  row.querySelector('.dir').textContent = cut > 0 ? path.slice(0, cut) : '';
  row.title = e.ts + '　' + e.tool + '　' + path
    + (created ? '（這一次新建的）' : '（改過，有備份）')
    + (newest ? '\n最新的一筆' : '') + '\n點一下退回這一筆之前的狀態';
  return row;
}

// 檢查點底下列出那一輪動過的檔案。純資訊，點的是上面那一列。
function fileLines(files) {
  const box = document.createElement('div');
  (files || []).slice(0, 40).forEach(function (f) {
    const r = document.createElement('div');
    r.className = 'sc-row sub';
    r.innerHTML = '<span class="st"></span><span class="nm"></span>';
    const st = r.querySelector('.st');
    st.textContent = f.st;
    st.className = 'st ' + (f.st === 'A' ? 'a' : f.st === 'D' ? 'd' : 'm');
    r.querySelector('.nm').textContent = f.path;
    r.title = f.path;
    box.appendChild(r);
  });
  if ((files || []).length > 40) {
    const more = document.createElement('div');
    more.className = 'sc-row sub';
    more.textContent = '…還有 ' + (files.length - 40) + ' 個';
    box.appendChild(more);
  }
  return box;
}

// journal 存的是 "2026-08-25 14:31:46"。同一天只顯示時分秒，
// 跨天的話上面會有一條日期分隔 —— 一長串重複的年月日只會擋住要看的東西。
function clockOf(ts) {
  const parts = String(ts || '').split(' ');
  return parts.length > 1 ? parts[1] : String(ts || '');
}

function dayOf(ts) { return String(ts || '').split(' ')[0]; }

function renderRewind(entries, total) {
  const box = $('rwList');
  box.innerHTML = '';
  $('rwCount').textContent = entries.length;
  if (!entries.length) {
    const git = (S.ws.git_state || {}).repo;
    box.innerHTML = '<div class="fv-empty">' + (total
      ? '這則對話還沒改過檔案（工作區裡有其他對話改的 ' + total + ' 筆）。'
      : '還沒有改過任何檔案。')
      + (git ? '' : '<br>工作區不是 git repo，所以每則提示的檢查點沒有建立 ——'
             + '只有檔案工具改的會有單筆還原點。') + '</div>';
    return;
  }
  // 用本機時間組今天的日期，不要用 toISOString（那是 UTC，跨日的時候會差一天）。
  // journal 的時間戳是 serve.py 那台的本機時間 —— 遠端開網頁又跨時區的話，
  // 這裡只會少顯示「今天」兩個字，日期本身還是對的。
  const n = new Date();
  const today = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0')
    + '-' + String(n.getDate()).padStart(2, '0');
  let day = '';
  entries.slice().reverse().forEach(function (e, i) {
    const d = dayOf(e.ts);
    if (d !== day) {
      day = d;
      const sep = document.createElement('div');
      sep.className = 'sc-row day';
      sep.textContent = d === today ? '今天' : d;
      box.appendChild(sep);
    }
    const row = rewindRow(e, i === 0);
    row.addEventListener('click', function () {
      // 還原一定是照時間倒著做的，所以會連帶退掉別則對話在這之後改的東西。
      // 還原是照時間倒著做的，會連帶退掉別則對話後來改的 —— 講出來讓人決定
      const others = e.other_chats
        ? '\n其中 ' + e.other_chats + ' 筆是「其他對話」的，也會一起退回去。' : '';
      if (e.tree) {
        const n = (e.files || []).length;
        if (!confirm('把整個工作區退回送出這則提示之前？\n\n' +
                     e.ts + '\n「' + e.path + '」\n\n' +
                     '這一輪動過的 ' + n + ' 個檔案會退回去，之後新增的會刪掉 ——\n' +
                     '包含 run_shell 改的。共退 ' + e.undo_count + ' 輪。' + others +
                     '\n你的 git 分支、HEAD、暫存區與對話內容都不受影響。')) return;
        doRewind(e.id);
        return;
      }
      if (!confirm('把工作區退回這一筆之前的樣子？\n\n' +
                   e.ts + ' ' + e.tool + ' ' + e.path +
                   '\n\n會退回 ' + e.undo_count + ' 筆改動。' + others +
                   '\n對話內容不受影響。')) return;
      doRewind(e.id);
    });
    box.appendChild(row);
    if (e.tree && (e.files || []).length) box.appendChild(fileLines(e.files));
  });
}

async function doRewind(id) {
  try {
    const res = await fetch(apiUrl('/rewind'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    loadHistory();                 // 重讀：這一則對話剩下哪些還原點
    toast('已還原 ' + (data.undone || []).length + ' 筆' +
      ((data.failed || []).length ? '，' + data.failed.length + ' 筆失敗' : ''));
    if ((data.failed || []).length) console.warn('rewind 失敗的項目', data.failed);
    if (S.tab === 'file' && S.ws.path) renderWorkspace();
    if (S.tab === 'hist') loadHistory();          // 檔案退回去了，git 狀態也跟著變
  } catch (e) {
    toast('還原失敗：' + e.message);
  }
}
