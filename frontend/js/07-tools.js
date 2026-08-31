/* ══════════════════════ 工具呼叫 ══════════════════════ */
function toolsReady() {
  if (!S.srv.tools) return false;
  // 外部 API 沒有辦法問「這個模型支不支援 tools」——/v1/models 只回 id，
  // 沒有 capabilities 這種東西。所以這一邊改成手動開關（連線設定裡）。
  if (S.provider === 'openai') return !!S.oa.tools;
  return (S.caps[S.model] || []).indexOf('tools') >= 0;
}

// 為什麼不能開工具；空字串代表可以開
function toolsReason() {
  if (!S.srv.toolsLocal) {
    return SAME_ORIGIN
      // 頁面是 serve.py 端的，但請求不是從本機來的 —— 這是刻意的邊界
      ? '工具只接受本機請求。你是從別台連過來的（' + (S.srv.client || '?') + '）：'
        + '在自己的機器上跑一份 serve.py，或那台加 --trust-remote'
      : '直接開 HTML 檔沒有 serve.py，工具不會出現';
  }
  if (S.provider === 'openai') {
    return S.oa.tools ? ''
      : '外部 API 模式要自己勾「送出工具定義」（連線設定裡）—— 那邊問不到模型支不支援';
  }
  if ((S.caps[S.model] || []).indexOf('tools') < 0) return (S.model || '這個模型') + ' 沒有 tools 能力';
  return '';
}

// 規則字串由 serve.py 依「開了哪些工具」拼出來（見 agent_rules()）。
// 跟工具定義一樣只留一份，介面與測試用的是同一段文字。
function agentRules() { return toolsReady() ? (S.agentRules || '') : ''; }
// 專案地圖跟規則一起走：兩者都是「這一輪之前就固定」的東西，所以更新時機一樣。
// **中途不要動它** —— 改到系統提示等於 Ollama 那一端的 prefix cache 整段作廢。
function repoMap() { return toolsReady() ? (S.repoMap || '') : ''; }

// 功能開關清單。之後要加新功能（MCP、網頁搜尋…）在這裡加一筆就會出現在選單裡。
const FEATURES = [{
  id: 'tools',
  label: '本機工具',
  desc: '讀檔、搜尋、跑指令、抓網頁。每次執行前都會先問你',
  isOn: function () { return !!S.srv.tools; },
  blocked: toolsReason,
  toggle: function (on) { return setServerTools({ enabled: on }); }
}, {
  id: 'browser',
  label: '連網瀏覽',
  desc: 'run_browser：搜尋、開頁、順著連結往下查。會讓模型主動連出去',
  isOn: function () { return !!S.srv.browser; },
  blocked: toolsReason,
  toggle: function (on) { return setServerTools({ browser: on }); }
}, {
  id: 'sandbox',
  label: '沙盒執行',
  desc: 'run_shell 與 run_tests 關進沙盒：跑不出工作區、沒有網路',
  // 用哪一種後端是「跑 serve.py 那一台」的事（Linux 用 bubblewrap、
  // macOS 用 sandbox-exec、Windows 用 Docker Desktop），所以顯示後端名字 ——
  // 使用者才知道自己實際上被擋住了什麼。
  note: function () {
    const b = (S.srv.sandboxInfo || {}).backend;
    return b ? '　' + b : '';
  },
  isOn: function () { return !!S.srv.sandbox; },
  blocked: function () { return toolsReason() || (S.srv.sandboxWhy || ''); },
  toggle: function (on) { return setServerTools({ sandbox: on }); }
}, {
  // 「修改檔案」不在這裡 —— 它是輸入框旁邊的筆按鈕（renderWriteBtn）。
  // 一天要按好幾次的開關不該藏在兩層選單底下。
  id: 'plan',
  label: '計畫模式',
  desc: '要先用 submit_plan 送出計畫、你按核准，才會開放修改檔案的工具',
  isOn: function () { return !!S.plan; },
  blocked: function () { return toolsReason() || (S.ws.path ? '' : '要先設定工作區資料夾'); },
  toggle: function (on) { return setServerTools({ plan: on }); }
}];

// 自動模式不是開／關，是三段，所以自己一個選單項目
// 這一台只 load 得動一個模型的時候，子代理用哪個就是「要不要換權重」的取捨：
// 同一個模型＝零成本，換一個小的＝每次進出子代理都要重新載入。所以預設是跟主模型
// 一樣，想換的人自己去選 —— 這個判斷跟 VRAM 有關，程式猜不出來。
function subModelMenuItem() {
  return {
    label: '子代理模型：' + (S.subModel || '跟主模型一樣'),
    meta: S.subModel ? '換模型會重新載入權重' : '',
    action: function () {
      const pick = function (name) {
        return function () {
          S.subModel = name;
          saveConfig();
          toast(name ? '子代理改用 ' + name : '子代理跟主模型一樣');
        };
      };
      const rows = [{ label: '跟主模型一樣', meta: '不必換權重，最省',
                      checked: !S.subModel, action: pick('') }];
      (S.models || []).forEach(function (m) {
        rows.push({ label: m.name, checked: S.subModel === m.name, action: pick(m.name) });
      });
      showMenu($('featBtn'), rows);
    }
  };
}

// 這一格是「模型說做完了」跟「真的做完了」之間的差別。沒有它，收尾條件是模型自己
// 的判斷 —— agent_rules 只能好聲好氣拜託它記得跑測試，而小模型會忘。
function verifyCmd() { return (S.verify || {})[S.ws.path || ''] || ''; }

function verifyMenuItem() {
  const now = verifyCmd();
  return {
    label: '驗證指令：' + (now || '（未設定）'),
    meta: now ? '模型說做完時會先跑一次' : (S.verifyHint ? '偵測到：' + S.verifyHint : ''),
    action: function () {
      if (!S.ws.path) { toast('要先設工作區'); return; }
      const v = prompt('模型說「做完了」的時候，先跑哪一行指令？\n'
        + '沒過就把輸出丟回去讓它自己修（最多兩次）。留空＝關掉。',
        now || S.verifyHint || '');
      if (v === null) return;
      S.verify[S.ws.path] = String(v).trim();
      saveConfig();
      toast(String(v).trim() ? '驗證指令：' + String(v).trim() : '已關掉自動驗收');
    }
  };
}

function autoMenuItem() {
  return {
    label: '自動模式：' + autoLabel(),
    meta: S.auto === 'off' ? '' : '注意',
    action: function () {
      showMenu($('featBtn'), AUTO_MODES.map(function (m) {
        return {
          label: m[1], meta: m[2], checked: S.auto === m[0],
          action: async function () {
            S.auto = m[0];
            saveConfig();
            // 後端要知道現在哪一檔，agent_rules() 才寫得出實話 ——
            // 「每一次呼叫都會先讓使用者確認」在自動模式下是假的，
            // 而且那句話直接讓「讀三個檔」變成三輪。
            try { await setServerTools({ auto: m[0] }); }
            catch (e) { /* 舊版 serve.py 不認得，系統提示照舊，不影響放行 */ }
            // 放開之後就不再一個一個問，但「能不能改檔案」是另一道開關。
            // 兩個不連動的話，使用者以為放著讓它自己跑，結果模型連檔案都動不了。
            let extra = '';
            if (autoWrites(m[0]) && !S.ws.write) {
              const why = writeReason();
              if (why) extra = '（' + why + '，目前只能讀）';
              else {
                try { await setServerTools({ write: true }); extra = '，已一併開啟修改檔案'; }
                catch (e) { extra = '（修改檔案開不起來：' + e.message + '）'; }
              }
            }
            renderFeatBtn();
            renderWriteBtn();
            toast(m[0] === 'off' ? '每個工具呼叫都會問你'
              : (m[0] === 'read' ? '唯讀工具自動放行'
                : (m[0] === 'ws' ? '工作區內全自動：只動工作區檔案的指令不再問你' + extra
                  : '跑指令自動：rm、sudo 這種風險指令仍會問你' + extra)));
          }
        };
      }));
    }
  };
}

function renderFeatBtn() {
  const on = FEATURES.some(function (f) { return f.isOn(); });
  const btn = $('featBtn');
  btn.classList.toggle('on', on);
  btn.classList.toggle('warn', S.auto !== 'off' && S.srv.tools);
  btn.title = '功能與工具' + (on ? '（本機工具已開啟）' : '') +
    (S.auto === 'off' ? '' : ' · 自動模式：' + autoLabel());
}

function openFeatureMenu() {
  const rows = FEATURES.map(function (f) {
    const why = f.blocked();
    return {
      label: f.label,
      checked: f.isOn(),
      meta: why || ((f.isOn() ? '已開啟' : '關閉') + (f.note ? f.note() : '')),
      action: async function () {
        if (why) { toast(why); return; }
        try {
          await f.toggle(!f.isOn());
          toast(f.label + (f.isOn() ? ' 已開啟，每次執行前仍會問你' : ' 已關閉'));
        } catch (e) {
          toast('切換失敗：' + e.message);
        }
        saveConfig();
        renderFeatBtn();
      }
    };
  });
  rows.push('-', autoMenuItem());
  if (S.srv.tools) rows.push(verifyMenuItem(), subModelMenuItem());
  // 一條規則都沒有就不佔一列：規則只從確認卡的「以後都放行」長出來，
  // 沒有人會為了設定它而打開選單。有東西了才需要一個看得到、刪得掉的地方。
  if ((S.rules || []).length) {
    rows.push({ label: '允許規則…', meta: S.rules.length + ' 條', action: openRules });
  }
  showMenu($('featBtn'), rows);
}

// 送出去的欄位名 -> 回應裡對應的欄位名（只有 enabled 兩邊不同名）
const SW_KEY = { enabled: 'tools' };

// 開關直接改伺服器狀態，不必重啟 serve.py
async function setServerTools(patch) {
  const res = await fetch(apiUrl('/tools'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  // 舊版 serve.py 收到不認得的開關會靜靜忽略，回應裡連那個欄位都沒有。
  // 沒有這一段的話，畫面會永遠停在「關閉」而且不出任何錯誤 —— 這正是
  // 連網瀏覽那次的狀況：頁面是新的（build.py 每次重讀），Python 還是舊的。
  for (const k in patch) {
    const got = data[SW_KEY[k] || k];
    if (got === undefined) {
      throw new Error('這份 serve.py 沒有「' + k + '」這個開關，重新啟動 serve.py 就有了');
    }
    // auto 是字串（off／read／…），其餘是開關。字串用相等比 ——
    // !!'off' 跟 !!'ws' 都是 true，用 !! 比等於什麼都沒驗到。
    const ok = typeof patch[k] === 'string' ? got === patch[k] : !!got === !!patch[k];
    if (!ok) throw new Error('伺服器沒有套用這個開關');
  }
  S.srv.tools = !!data.tools;
  S.tools = !!data.tools;
  S.ws.write = !!data.write;
  if (data.plan !== undefined) S.plan = !!data.plan;
  if (data.browser !== undefined) S.srv.browser = !!data.browser;
  if (data.sandbox !== undefined) S.srv.sandbox = !!data.sandbox;
  S.toolDefs = data.tool_defs || [];
  S.agentRules = data.agent_rules || '';
  if (data.repo_map !== undefined) S.repoMap = data.repo_map || '';
  if (data.agents) S.agentTypes = data.agents;
  renderWriteBtn();
}

/* ══════════════════════ 串流執行 ══════════════════════ */
// run_shell / run_tests 走 /run：同步版跑 pytest 時畫面整整卡住好幾分鐘，
// 看不出來是在跑還是掛了。這裡邊跑邊把每一行貼出來，按停止就把連線斷掉。
async function runStreamed(name, args, agent) {
  const ctrl = new AbortController();
  const el = msgEl('assistant');
  el.innerHTML =
    '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
    '<div class="msg-col"><div class="tool-card">' +
      '<div class="th">執行中 <span class="nm"></span>' +
      '<button class="mini" data-stop style="margin-left:auto;">停止</button></div>' +
      '<pre class="out" style="max-height:230px; overflow:auto; margin:0;"></pre>' +
    '</div></div>';
  el.querySelector('.nm').textContent = name;
  el.querySelector('[data-stop]').addEventListener('click', function () { ctrl.abort(); });
  const out = el.querySelector('.out');
  $('thread').appendChild(el);
  S.stick = true;
  pin();

  let result = '';
  try {
    const res = await fetch(apiUrl('/run'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, args: args, agent: agent || '' }),
      signal: ctrl.signal
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error || ('HTTP ' + res.status));
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
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        let obj;
        try { obj = JSON.parse(line); } catch (e) { continue; }
        if (obj.line !== undefined) {
          out.textContent += obj.line + '\n';
          out.scrollTop = out.scrollHeight;
          pin();
        }
        if (obj.done) result = obj.result || '';
      }
    }
    // 這兩條路沒有後端算好的結果，用的是瀏覽器累積的整段輸出 ——
    // 一定要自己截，不然按停止反而比跑完更會撐爆 context
    if (!result) result = tailLines(out.textContent.trim()) + '\n（連線提早結束）';
  } catch (e) {
    result = e.name === 'AbortError'
      ? tailLines(out.textContent.trim()) + '\n（使用者按了停止）'
      : '錯誤：' + e.message;
  } finally {
    el.remove();
  }
  return result;
}

/* ══════════════════════ 待辦清單 ══════════════════════ */
// 幾套 agent 都有這個：模型跑十幾輪之後會忘記原始目標，
// 把清單攤在輸入框上方，人跟模型都看得到還剩什麼沒做。
// 一項「還在等別人」跟一項「可以開始了」是兩回事。全部混在一起顯示的話，
// 看起來就像它有五件事沒做，其實只有一件真的能動。
function todoBlockers(items, t) {
  return (t.blocked_by || []).filter(function (n) {
    const dep = items[n - 1];
    return dep && !dep.done;
  });
}

function renderTodos() {
  const bar = $('todoBar');
  const items = S.todos || [];
  bar.hidden = !items.length;
  if (!items.length) return;
  const done = items.filter(function (t) { return t.done; }).length;
  const blocked = items.filter(function (t) {
    return !t.done && todoBlockers(items, t).length;
  }).length;
  const ready = items.length - done - blocked;
  const head = ['完成 ' + done, '可以做 ' + ready]
    .concat(blocked ? ['卡住 ' + blocked] : []).join(' · ');
  bar.innerHTML = '<div class="h">待辦 · ' + head + '（共 ' + items.length + '）</div>' +
    items.map(function (t, i) {
      const wait = todoBlockers(items, t);
      const mark = t.done ? '✓' : (wait.length ? '⏸' : '○');
      return '<div class="t' + (t.done ? ' done' : '') + (wait.length ? ' blocked' : '') +
        '"><span class="b">' + mark + '</span><span>' + (i + 1) + '. ' + esc(t.text) +
        (wait.length ? '<span class="dep"> 要等 ' +
          wait.map(function (n) { return '#' + n; }).join('、') + '</span>' : '') +
        '</span></div>';
    }).join('');
}

// 工具跑幾輪、花多少 token。自動模式開著時這是唯一看得出「它還在做事」的地方。
function renderRunBar() {
  const bar = $('runBar');
  const r = S.run;
  bar.hidden = !r.rounds;
  if (!r.rounds) { stopRunTicker(); return; }
  const bits = ['第 ' + r.rounds + '/' + MAX_TOOL_ROUNDS + ' 輪',
                r.calls + ' 次工具',
                '累計 ' + fmtTokens(r.tokens) + ' tokens'];
  if (r.now) bits.push('執行中 ' + r.now);
  if (r.t0) bits.push('已跑 ' + fmtElapsed(performance.now() - r.t0));
  // 背景指令活在 serve.py 那個行程裡，關掉分頁它還在跑 —— 所以要一直看得到
  const bg = (S.jobs || []).filter(function (j) { return j.code === null; });
  if (bg.length) bits.push('背景 ' + bg.length + ' 條在跑');
  if (r.squeezed) bits.push('已省略 ' + r.squeezed + ' 段較早的工具輸出');
  if (S.auto !== 'off') bits.push('自動模式：' + autoLabel());
  const now = currentTodo(S.todos);
  bar.textContent = bits.join(' · ') + (now ? '\n現在：' + now : '');
}

// 秒數要自己走，不然只有工具回來時才會更新 —— 一支跑三分鐘的指令中間完全沒有動靜，
// 看起來就像當掉了。這正是「看不出跑多久」的實際感受。
function startRunTicker() {
  if (S.runTicker) return;
  S.runTicker = setInterval(function () {
    if (S.run && S.run.rounds) renderRunBar(); else stopRunTicker();
  }, 1000);
}
function stopRunTicker() {
  if (S.runTicker) { clearInterval(S.runTicker); S.runTicker = null; }
}

function fmtTokens(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n || 0);
}

// 模型要問問題時不送到 /tool（伺服器沒有人可以答），直接在對話裡問。
function askUser(args) {
  return new Promise(function (resolve) {
    const el = msgEl('assistant');
    el.innerHTML =
      '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
      '<div class="msg-col"><div class="tool-card">' +
        '<div class="th">模型想問你</div><div class="q"></div>' +
        '<div class="ta opts"></div>' +
        '<div class="ta"><input class="ans" placeholder="或自己打一句">' +
        '<button class="mini" data-send disabled>送出</button></div>' +
      '</div></div>';
    el.querySelector('.q').textContent = args.question || '（沒有問題內容）';
    const input = el.querySelector('.ans');
    input.style.cssText =
      'flex:1; height:28px; background:var(--ground); border:1px solid var(--line2);' +
      'border-radius:7px; padding:0 9px; font-size:12px; outline:none;';
    const send = el.querySelector('[data-send]');
    const opts = el.querySelector('.opts');
    let picked = '';

    // 點選項**不會直接送出**，只是選起來（反亮），要按送出才算數。
    // 原本是點下去就當作答案 —— 一個誤觸就替使用者做了決定，而且沒有回頭路：
    // 那則回答已經進到 context 裡了。選項也常常很長，滑過去點錯不難。
    function pick(value, btn) {
      picked = picked === value ? '' : value;     // 再點一次可以取消
      [].forEach.call(opts.children, function (b) {
        const on = b === btn && picked;
        b.classList.toggle('on', !!on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (picked) input.value = '';               // 選項與自己打的是二選一，不要曖昧
      refresh();
    }
    function answer() { return picked || input.value.trim(); }
    function refresh() { send.disabled = !answer(); }

    (args.options || []).slice(0, 6).forEach(function (o) {
      const b = document.createElement('button');
      b.className = 'mini';
      b.type = 'button';
      b.textContent = o;
      b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', function () { pick(o, b); });
      opts.appendChild(b);
    });
    opts.hidden = !(args.options || []).length;
    $('thread').appendChild(el);
    S.stick = true;
    pin();
    input.focus();
    waitBadge(true);
    notifyBg('模型在問你：' + String(args.question || '').slice(0, 80));

    const done = function (text) {
      waitBadge(false);
      el.querySelector('.ta:last-child').innerHTML = '';
      opts.innerHTML = '';
      opts.hidden = true;
      el.querySelector('.q').textContent += '\n你：' + text;
      resolve(text);
    };
    send.addEventListener('click', function () { if (answer()) done(answer()); });
    input.addEventListener('input', function () {
      if (input.value.trim() && picked) pick(picked, null);   // 打字就放掉選項
      refresh();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && answer()) { e.preventDefault(); done(answer()); }
    });
  });
}

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
// 備份一直都有（每次改檔案前複製一份），缺的是「先後順序」——
// 沒有順序就只能一個檔一個檔還原，沒辦法「退回十分鐘前」。
// serve.py 的 journal.jsonl 記的就是那個順序。
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

// 照 VS Code 原始檔控制那一欄的排法：一行一個檔案，狀態字母在最前面，
// 檔名、目錄（灰）、時間。新的在最上面 —— 要退回去的時候，人想的是
// 「退到剛剛」不是「退到最早」，所以由新到舊 top-down。
function rewindRow(e, newest) {
  const row = document.createElement('div');
  row.className = 'sc-row';
  const created = !!e.created;
  const path = String(e.path || '');
  const cut = path.lastIndexOf('/');
  row.innerHTML = '<span class="st"></span><span class="nm"></span>'
    + '<span class="dir"></span><span class="tm"></span>';
  // A = 新建（Added）、M = 修改（Modified），跟 git 與 VS Code 同一套字母
  const st = row.querySelector('.st');
  // C = 檢查點（Checkpoint），一整則提示的起點；A = 新建、M = 修改，
  // 跟 git 與 VS Code 同一套字母。
  if (e.tree) {
    row.classList.add('ckpt');
    st.textContent = 'C';
    st.className = 'st c';
    row.querySelector('.nm').textContent = path || '（沒有內容）';
    row.querySelector('.dir').textContent = '這則提示之前';
    row.querySelector('.tm').textContent = clockOf(e.ts);
    row.title = e.ts + '　送出這則提示之前的完整快照\n「' + path + '」'
      + (newest ? '\n最新的一筆' : '')
      + '\n點一下把整個工作區退回這個時間點 —— 包含 run_shell 改的東西';
    return row;
  }
  st.textContent = created ? 'A' : 'M';
  st.className = 'st ' + (created ? 'a' : 'm');
  row.querySelector('.nm').textContent = cut >= 0 ? path.slice(cut + 1) : path;
  row.querySelector('.dir').textContent = cut > 0 ? path.slice(0, cut) : '';
  row.querySelector('.tm').textContent = clockOf(e.ts);
  row.title = e.ts + '　' + e.tool + '　' + path
    + (created ? '（這一次新建的）' : '（改過，有備份）')
    + (newest ? '\n最新的一筆' : '') + '\n點一下退回這一筆之前的狀態';
  return row;
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
      // 只顯示這一則卻偷偷動到別人的，那是騙人 —— 講出來讓使用者決定。
      const others = e.other_chats
        ? '\n其中 ' + e.other_chats + ' 筆是「其他對話」改的，也會一起退回去。' : '';
      // 檢查點退的是**整個工作區**，不是清單上那幾個檔案 —— 這一輪之後多出來的
      // 檔案會被刪掉。差別夠大，要用不同的話講清楚。
      if (e.tree) {
        if (!confirm('把整個工作區退回送出這則提示之前的樣子？\n\n' +
                     e.ts + '\n「' + e.path + '」\n\n' +
                     '這一輪之後新增的檔案會被刪掉，改過的會退回去 ——\n' +
                     '包含 run_shell 改的（那些沒有單筆還原點）。\n' +
                     '你的 git 分支、HEAD 與暫存區不受影響，對話內容也不受影響。')) return;
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

/* ══════════════════════ 修改檔案的開關 ══════════════════════ */
// 從選單裡搬出來變成輸入框旁邊的按鈕：它是每天要按好幾次的東西，
// 不該藏在兩層選單底下。
function writeReason() {
  if (!S.srv.tools) return toolsReason() || '本機工具沒有開啟';
  if (!S.ws.path) return '要先選一個工作區資料夾';
  return '';
}

function renderWriteBtn() {
  const btn = $('writeBtn');
  if (!btn) return;
  const why = writeReason();
  btn.classList.toggle('on', !!S.ws.write);
  btn.disabled = !!why;
  btn.title = why || (S.ws.write
    ? '模型可以修改檔案（每一次仍然要你按確認）— 點一下關閉'
    : '模型目前只讀得到檔案 — 點一下允許修改');
}

async function toggleWrite() {
  const why = writeReason();
  if (why) { toast(why); return; }
  try {
    await setServerTools({ write: !S.ws.write });
    toast(S.ws.write ? '模型可以修改檔案了，每一次仍然要你按確認' : '已改回唯讀');
  } catch (e) {
    toast('切換失敗：' + e.message);
  }
  renderWriteBtn();
  renderFeatBtn();
}

// 重開之後把 serve.py 那一端的狀態接回來。
//
// 為什麼需要這個：工作區與四個開關都是 serve.py 的**行程全域**，重啟 serve.py
// 就回到預設；自動模式與其他偏好卻存在瀏覽器的 localStorage 裡。兩邊不對齊的
// 症狀是「自動模式已經放到最開，但模型改不動檔案」—— 看起來像壞掉，其實是
// 伺服器那邊的「修改檔案」根本沒開。
//
// 順序有意義：**工作區一定要先設**，因為 serve.py 的 ALLOW_WRITE 有
// `and WORKSPACE is not None`，沒有工作區的時候 write:true 會被靜靜吃掉。
async function restoreServerState(conf) {
  const done = [];
  const want = (current() || {}).ws || conf.wsPath || '';
  if (want && want !== S.ws.path) {
    try { await applyWorkspace(want); done.push('工作區'); }
    catch (e) { toast('上次的工作區接不回來（' + want + '）：' + e.message); }
  }
  const saved = conf.srv || {};
  const patch = {};
  if (saved.tools !== undefined && !!saved.tools !== !!S.srv.tools) patch.enabled = !!saved.tools;
  if (saved.write !== undefined && !!saved.write !== !!S.ws.write) patch.write = !!saved.write;
  if (saved.browser !== undefined && !!saved.browser !== !!S.srv.browser) patch.browser = !!saved.browser;
  if (saved.sandbox !== undefined && !!saved.sandbox !== !!S.srv.sandbox) patch.sandbox = !!saved.sandbox;
  // **檔位比另外存的那個 write 旗標可靠。** 上面那行接的是「上次伺服器的狀態」，
  // 而它會漂掉（換一台 serve.py、setServerTools 失敗過一次、或存檔時剛好是 false）。
  // 檔位是使用者自己選的意圖，而且「改檔案自動」以上本來就以動得了檔案為前提 ——
  // 所以放在後面蓋過去：不然畫面顯示全自動，那支筆卻是灰的，要人再點一次。
  if (autoWrites(S.auto) && !S.ws.write) patch.write = true;
  // 自動模式存在瀏覽器、serve.py 重啟就回到 off。不推回去的話系統提示會停在
  // 「每一次都會問你」，而實際上沒有人在按 —— 模型會一輪讀一個檔。
  if (S.auto && S.auto !== 'off') patch.auto = S.auto;
  if (Object.keys(patch).length) {
    const label = { enabled: '本機工具', write: '修改檔案', browser: '連網瀏覽', sandbox: '沙盒' };
    try {
      await setServerTools(patch);
      Object.keys(patch).filter(function (k) { return label[k]; })
        .forEach(function (k) { done.push(label[k] + (patch[k] ? '' : '（關）')); });
    } catch (e) {
      // 非本機的瀏覽器會被 serve.py 擋掉（403），那是對的，不要吵
      if (S.srv.toolsLocal !== false) toast('開關接不回來：' + e.message);
    }
  }
  // **一定要說出來**：靜靜地把「修改檔案」打開，跟使用者沒有按過那個鈕是兩回事
  if (done.length) toast('接回上次的設定：' + done.join('、'));
  renderWriteBtn();
  renderFeatBtn();
  renderWorkspace();
}


async function applyWorkspace(path) {
  const res = await fetch(apiUrl('/workspace'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: path })
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  S.ws = Object.assign({ path: '', write: false }, data);
  S.atFiles = null;                    // 換了工作區，@ 的檔案清單就過期了
  S.toolDefs = data.tool_defs || [];
  S.agentRules = data.agent_rules || '';
  if (data.repo_map !== undefined) S.repoMap = data.repo_map || '';
  if (data.verify_hint !== undefined) S.verifyHint = data.verify_hint || '';
  if (data.agents) S.agentTypes = data.agents;
  renderWorkspace();
  ensureFileList();                    // 先拉起來：模型寫的「檔案:行號」要靠它才連得起來
  const c = current();                 // 工作區跟著對話走：下次點回這則就切回來
  if (c) { c.ws = S.ws.path || ''; saveChats(); }
  saveConfig();
  const names = toolDefs().map(function (d) { return d.function.name; });
  toast(S.ws.path
    ? '工作區：' + S.ws.path + '（' + (S.ws.files || 0) + ' 個檔案，'
      + names.length + ' 支工具可用）'
    : '已清除工作區，檔案類工具已停用');
  return data;
}

// 執行前一定要人點頭。工具會在跑 serve.py 的那台機器上動手，不能自動放行。
// unified diff 上色。+ 綠、- 紅、@@ 灰，其餘原樣。
function renderDiff(text) {
  return text.split('\n').map(function (line) {
    const c = line.charAt(0);
    const cls = line.indexOf('@@') === 0 ? 'h'
      : (c === '+' && line.indexOf('+++') !== 0) ? 'a'
      : (c === '-' && line.indexOf('---') !== 0) ? 'd' : '';
    return cls ? '<span class="' + cls + '">' + esc(line) + '</span>' : esc(line);
  }).join('\n');
}

// 先跟後端要「這一次的預覽」：改檔案是 diff，跑指令是風險評估。
// 自動模式要不要放行也看它的結論，所以一定要等它回來。
async function toolPreview(name, args, agent) {
  // 以前只有改檔案與跑指令要問後端。現在每一支都要問 —— 允許規則可能命中任何工具，
  // 而規則要在伺服器那一端算（那裡才是真的擋得住的地方）。
  try {
    const res = await fetch(apiUrl('/preview'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, args: args, agent: agent || '' })
    });
    const data = await res.json();
    return { diff: data.diff || data.error || '', risk: data.risk || 'ok',
             rule: data.rule || null, scope: data.scope || '' };
  } catch (e) {
    return { diff: '', risk: 'ok', rule: null, scope: '' };
  }
}

/* ══════════════════════ 允許規則 ══════════════════════ */
// 自動模式是全有全無的三段，但人想要的從來不是那三個，而是
// 「pytest 一律放行、git commit 要問我、secrets/ 永遠不准碰」。
// 規則就是把這種判斷寫下來一次。真正的擋在 serve.py 那一端。

// 從這一次的呼叫猜一條合理的規則。猜的是「同一類」，不是「一模一樣」——
// 每跑一次 pytest 就多一條規則的話，那張清單三天就沒人看得懂了。
function ruleSuggestion(name, args) {
  if (name === 'run_shell' || name === 'run_tests') {
    const cmd = String((args && args.command) || '').trim();
    if (!cmd) return { tool: name, pattern: '*' };
    // 取前兩個詞：git commit -m … → git commit*
    const bits = cmd.split(/\s+/).slice(0, cmd.split(/\s+/)[0] === 'git' ? 2 : 1);
    return { tool: name, pattern: bits.join(' ') + '*' };
  }
  const path = String((args && args.path) || '');
  if (path) {
    const cut = path.lastIndexOf('/');
    return { tool: name, pattern: cut > 0 ? path.slice(0, cut) + '/**' : '*' };
  }
  return { tool: name, pattern: '*' };
}

async function rulesCall(body) {
  const res = await fetch(apiUrl('/rules'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  S.rules = data.rules || [];
  S.ruleFiles = data.files || [];
  return data;
}

function addRule(tool, pattern, action, note) {
  return rulesCall({ action: 'add', tool: tool, pattern: pattern,
                     rule: action, note: note || '' });
}

async function openRules() {
  try { await rulesCall({ action: 'list' }); }
  catch (e) { toast('讀不到規則：' + e.message); return; }
  renderRules();
  $('rlOverlay').classList.remove('hidden');
}

function renderRules() {
  const box = $('rlList');
  box.innerHTML = '';
  $('rlPath').textContent = (S.ruleFiles || []).map(function (f) {
    return f.scope + '：' + f.path + (f.exists ? '' : '（還沒有）');
  }).join('\n');
  if (!(S.rules || []).length) {
    box.innerHTML = '<div class="fv-empty">還沒有規則。'
      + '確認卡上的「以後都放行」會在這裡留下一條。</div>';
    return;
  }
  S.rules.forEach(function (r, i) {
    const row = document.createElement('div');
    row.className = 'mm-row';
    row.innerHTML = '<span class="st"></span><span class="n"></span>'
      + '<span class="m"></span><button class="mini danger" data-del>刪除</button>';
    const st = row.querySelector('.st');
    st.textContent = { allow: '放行', ask: '要問', deny: '禁止' }[r.action] || r.action;
    st.style.color = r.action === 'deny' ? 'var(--err)'
      : (r.action === 'allow' ? 'var(--accent)' : 'var(--ink3)');
    row.querySelector('.n').textContent = r.tool + '　' + r.pattern;
    // 專案的規則只在這個工作區有效，全域的到哪都算 —— 看得出來才不會刪錯
    row.querySelector('.m').textContent = (r.scope || '') + (r.note ? '　' + r.note : '');
    row.querySelector('[data-del]').addEventListener('click', async function () {
      try { await rulesCall({ action: 'remove', index: i }); renderRules(); }
      catch (e) { toast('刪不掉：' + e.message); }
    });
    box.appendChild(row);
  });
}

function confirmTool(name, args, pre) {
  return new Promise(function (resolve) {
    const write = WRITE_TOOLS.indexOf(name) >= 0;
    const plan = name === 'submit_plan';
    const risky = (pre && pre.risk && pre.risk !== 'ok') ? pre.diff : '';
    const el = msgEl('assistant');
    el.innerHTML =
      '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
      '<div class="msg-col"><div class="tool-card">' +
        '<div class="th">模型要求執行 <span class="nm"></span><span class="st fp"></span></div>' +
        '<pre class="args"></pre>' +
        '<div class="ta"><button class="mini" data-go>執行</button>' +
        '<button class="mini danger" data-no>略過</button>' +
        '<button class="mini" data-always hidden>以後都放行</button>' +
        '<span class="st">會在 serve.py 那台機器上執行</span></div>' +
      '</div></div>';
    el.querySelector('.nm').textContent = plan ? '（計畫）' : name;
    el.querySelector('.args').textContent = plan
      ? String(args.plan || '') : JSON.stringify(args, null, 2);
    if (plan) {
      el.querySelector('.th').firstChild.textContent = '模型提出的計畫，核准後才開放修改檔案 ';
      el.querySelector('[data-go]').textContent = '核准';
      el.querySelector('[data-no]').textContent = '退回';
      el.querySelector('.ta .st').textContent = '核准之後 write_file / edit_file 才會出現';
    }
    if (write) el.querySelector('.fp').textContent = '· ' + (args.path || '');
    // 多行的（load_skill 會先跑的那幾行、沙盒說明）只把第一行放進這個小標，
    // 完整的接在參數下面 —— 一個 span 塞五行等於沒顯示
    const head = risky.split('\n')[0];
    if (risky) {
      const warn = el.querySelector('.fp');
      warn.textContent = head;
      warn.style.color = 'var(--err)';
    }
    $('thread').appendChild(el);
    S.stick = true;
    pin();
    waitBadge(true);
    notifyBg('等你確認：' + (plan ? '模型提出的計畫' : name)
      + (write && args.path ? ' · ' + args.path : '')
      + (head ? ' · ' + head : ''));

    // 改檔案的話，把參數換成看得懂的 diff —— 沒人有辦法從一坨 JSON 判斷該不該按下去
    if (write && pre && pre.diff) {
      const box = el.querySelector('.args');
      box.className = 'args diff';
      box.innerHTML = renderDiff(pre.diff);
    } else if (risky.indexOf('\n') >= 0) {
      // 不是 diff、但有好幾行要看的：load_skill 會先執行的那份清單
      el.querySelector('.args').textContent += '\n\n' + risky;
    }

    // 「以後都放行」：把這一次的判斷寫成規則，不用每天重新點一遍。
    // 風險指令不給這顆 —— 那條保證不能被一顆順手的按鈕拿掉。
    const always = el.querySelector('[data-always]');
    const sug = ruleSuggestion(name, args);
    if (sug && !risky && !plan) {
      always.hidden = false;
      always.title = '之後 ' + sug.tool + ' 遇到 ' + sug.pattern + ' 就直接執行';
      always.addEventListener('click', async function () {
        try {
          await addRule(sug.tool, sug.pattern, 'allow');
          toast('已加規則：' + sug.tool + ' / ' + sug.pattern + ' 直接放行');
        } catch (e) { toast('規則存不起來：' + e.message); }
        done(true);
      });
    }

    const done = function (ok) { waitBadge(false); el.remove(); resolve(ok); };
    el.querySelector('[data-go]').addEventListener('click', function () { done(true); });
    el.querySelector('[data-no]').addEventListener('click', function () { done(false); });
  });
}

// 同一個呼叫（工具名＋參數完全一樣）連續失敗幾次就不再送。
// 模型會用一模一樣的參數重試失敗的呼叫 —— 系統提示裡寫了「不要這樣」，
// 但那是規則不是機制。自動模式下沒有人在按確認，25 輪就這樣燒完了，
// 而且畫面上看起來它「還在做事」。
const REPEAT_LIMIT = 2;
// ponytail: 比的是「工具名＋參數完全一樣」。差一個空白就繞過去了，
//           但模型重試時通常是原封不動送同一份。真的漏掉再做正規化。

// 這一輪屬於哪則對話。跑起來之後使用者可能切去看別的對話，而還原點要記在
// **發動它的那一則**底下，不是被看著的那一則。
function runChat() { return (S.run && S.run.chat) || S.currentId; }

function callKey(name, args) {
  try { return name + '\u0000' + JSON.stringify(args || {}); }
  catch (e) { return name; }               // 參數塞不進 JSON 就只比工具名
}

// 一次工具呼叫：預覽 → 確認（或自動放行）→ 執行，結果寫回 msg。
// 主迴圈與子代理共用這一支 —— 兩份實作的話，「危險指令一定要問人」
// 遲早會有一份忘了跟上。
// agent：這一次要跑在哪個子代理的 worktree 裡（空字串＝主代理自己的工作區）
async function execTool(name, args, msg, agent) {
  const key = callKey(name, args);
  S.run.fails = S.run.fails || {};
  if (S.run.fails[key] >= REPEAT_LIMIT) {
    msg.content = '（這個呼叫用一模一樣的參數已經失敗 ' + REPEAT_LIMIT +
      ' 次，所以沒有再跑一次。換個做法：先用 read_file 或 list_dir 確認實際情況、' +
      '改參數、或改用別的工具。重複同一個呼叫不會有不同的結果。）';
    msg.failed = true;
    msg.blocked = true;
    return msg;
  }
  const pre = await toolPreview(name, args, agent);
  if (pre.rule && pre.rule.action === 'deny') {
    // 規則說不行就不要問了。真正的擋在伺服器那一端，這裡只是不要白問一次。
    msg.content = '（允許規則擋下來了：' + pre.rule.tool + ' / ' + pre.rule.pattern
      + (pre.rule.note ? '　' + pre.rule.note : '') + '）';
    msg.failed = true;
    msg.blocked = true;
    return msg;
  }
  const auto = autoApprove(name, pre.risk, pre.rule, pre.scope);
  if (!auto && !await confirmTool(name, args, pre)) {
    msg.content = '（使用者拒絕執行這個工具）';
    msg.denied = true;
    return msg;
  }
  msg.auto = auto;
  S.run.calls += 1;
  // 不串流的工具，結果卡是**跑完之後**才貼上來的 —— setup_env 裝套件裝兩分鐘，
  // 這中間畫面上一個字都不會多。把正在跑的那一支寫進進度條，秒數本來就在走。
  S.run.now = name;
  renderRunBar();
  try {
    // 會跑很久的那兩支邊跑邊顯示；其他工具一次回來就好
    if (S.streamTools.indexOf(name) >= 0 && !args.background) {
      msg.content = await runStreamed(name, args, agent);
      msg.failed = msg.content.indexOf('錯誤：') === 0;
      msg.streamed = true;
      S.run.now = '';
      touchTree();                         // run_shell／run_tests 最常動到檔案
      if (msg.failed) S.run.fails[key] = (S.run.fails[key] || 0) + 1;
      else delete S.run.fails[key];
      return msg;
    }
    const res = await fetch(apiUrl('/tool'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, args: args, chat: runChat(),
                             agent: agent || '' })
    });
    const data = await res.json();
    if (data.jobs) { S.jobs = data.jobs; renderRunBar(); }
    msg.content = res.ok ? data.result : ('錯誤：' + (data.error || ('HTTP ' + res.status)));
    msg.failed = !res.ok;
    // serve.py 用 [backup]路徑 回報備份位置；那是給介面用的，別佔模型的 context
    const at = msg.content.indexOf('[backup]');
    if (at >= 0) {
      msg.backup = msg.content.slice(at + 8).trim();
      msg.content = msg.content.slice(0, at).trim();
    }
    if (data.todos) { S.todos = data.todos; renderTodos(); }
    if (data.tool_defs) S.toolDefs = data.tool_defs;   // 核准計畫後會多出寫入工具
  } catch (e) {
    msg.content = '錯誤：' + e.message;
    msg.failed = true;
  }
  S.run.now = '';
  renderRunBar();
  if (msg.failed) S.run.fails[key] = (S.run.fails[key] || 0) + 1;
  else delete S.run.fails[key];            // 成功過就重新計算
  // 失敗的也要重讀：run_shell 跑到一半掛掉，前半段的檔案已經寫下去了
  if (READ_ONLY_TOOLS.indexOf(name) < 0) touchTree();
  if (!msg.failed) noteFinishSignals(name, args);
  return msg;
}

/* ══════════════════════ 子代理 ══════════════════════ */
// 「掃過整個專案找出所有用到 X 的地方」這種事，自己做會把幾十個檔案的內容
// 灌進主對話，之後每一輪都要重送。子代理有自己的 context，只有結論回來。
//
// 型別定義在 agents/*.md（照常見的 agents/ 慣例），由 serve.py 讀進來 ——
// 加一種子代理是加一個檔案，不是改這裡的程式碼。

// 跟 MAX_TOOL_ROUNDS 一樣是**失控煞車不是預算**：交辦出去的事沒有人在旁邊看。
const SUB_ROUNDS = 60;
// 深度上限是硬的。那套 agent 用提示詞當成本煞車（「不要隨便開子代理」），
// 那是因為有人在看帳單；這裡的前提是放著跑三十分鐘沒人看，所以要機制。
const SUB_DEPTH_MAX = 2;
// 這兩支永遠不給，型別檔寫了也沒用：問了沒人看得懂上下文、
// 待辦是主代理那條線的東西（同一個 Session，寫進去會真的蓋掉）。
const SUB_NEVER = ['ask_user_question', 'todo_write'];
// 續談用的 context 最多留這麼多則。每一則都是一整條對話，不能無限留。
const SUB_KEEP = 20;
const SUB_CTX_AT = 0.8;   // 子代理用掉這個比例的 num_ctx 就收工具，逼它做結論

function agentTypes() { return S.agentTypes || []; }

function agentType(name) {
  const list = agentTypes();
  const want = String((name === undefined || name === null) ? '' : name);
  return list.filter(function (t) { return t.name === want; })[0] || list[0] || null;
}

function subTools(type, depth) {
  const pick = (type && type.tools) || ['*'];
  const all = pick.indexOf('*') >= 0;
  return toolDefs().filter(function (d) {
    const n = (d.function || {}).name;
    if (SUB_NEVER.indexOf(n) >= 0) return false;
    if (n === 'task' && (depth || 1) >= SUB_DEPTH_MAX) return false;
    return all || pick.indexOf(n) >= 0;
  });
}

// 這種子代理會不會動到檔案？決定它能不能跟別人平行跑。
function subWrites(type) {
  const pick = (type && type.tools) || ['*'];
  if (pick.indexOf('*') >= 0) return true;
  return pick.filter(function (n) {
    return WRITE_TOOLS.indexOf(n) >= 0 || n === 'run_shell' || n === 'setup_env';
  }).length > 0;
}

async function agentCall(body) {
  const res = await fetch(apiUrl('/agent'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

async function runSubagent(args, depth, parent) {
  const at = depth || 1;
  const prev = S.subs && S.subs[String((args || {}).resume || '')];
  const task = String((args && (args.prompt || args.task)) || '').trim();
  if (!task) return '錯誤：task 要給 prompt';
  const type = prev ? prev.type : agentType((args || {}).type);
  if (!type) return '錯誤：沒有任何子代理型別可用（agents/ 是空的）';
  const signal = S.abort ? S.abort.signal : null;

  const el = msgEl('assistant');
  el.innerHTML =
    '<div class="msg-avatar">' + ico('wrench', 14, 2) + '</div>' +
    '<div class="msg-col"><div class="tool-card"><div class="th">子代理 ' + type.name +
    ' <span class="sid"></span> <span class="st">進行中</span>' +
    '<button class="mini" data-stop style="margin-left:auto;">中斷</button></div>' +
    '<pre class="out" style="margin:0;"></pre></div></div>';
  const out = el.querySelector('.out');
  out.textContent = task;
  $('thread').appendChild(el);
  pin();
  const log = function (line) { out.textContent += '\n' + line; pin(); };

  // **每一種子代理都要在伺服器登記**，不只需要 worktree 的那些。工具白名單如果只靠
  // 網頁「不送那幾支定義」，模型幻覺出一個工具名就繞過去了 —— 送到 /tool 的只是一個
  // 字串，伺服器原本無從知道是誰在叫。登記之後 agent_guard() 才擋得住。
  //
  // 續談也要重新登記一次：上一輪結束時伺服器那邊的登記就收掉了（連同 worktree）。
  // 對外的追問 id（box.id）跨續談不變，這一次的伺服器 id（box.sid）每次都新的。
  S.subs = S.subs || {};
  const box = prev || { id: '', sid: '', type: type, msgs: null, stopped: false };
  box.stopped = false;
  try {
    const info = await agentCall({ action: 'open', type: type.name,
                                   parent: parent || '', chat: runChat(),
                                   task: task });
    box.sid = info.id;
    box.id = box.id || info.id;
    log('· 子代理 ' + info.id + '（第 ' + info.depth + ' 層'
      + (info.parent ? '，上層 ' + info.parent : '')
      + (info.branch ? '，分支 ' + info.branch : '')
      + (info.linked && info.linked.length ? '，借用 ' + info.linked.join('、') : '')
      + '）');
  } catch (e) {
    el.querySelector('.st').textContent = '開不起來';
    return '子代理失敗：登記不了（' + e.message + '）';
  }
  const sid = box.sid;
  el.querySelector('.sid').textContent = box.id;
  S.subs[box.id] = box;
  // 留著是為了讓主代理追問得下去，但不能無限留 —— 每一則都是一整條 context
  const keys = Object.keys(S.subs);
  if (keys.length > SUB_KEEP) delete S.subs[keys[0]];
  // 中斷是伺服器那一端的事：標記之後連它的後代與背景指令一起停，
  // 而且任何綁在這個 id 上的工具呼叫都會被拒絕 —— 網頁不理也叫不動。
  el.querySelector('[data-stop]').addEventListener('click', async function () {
    box.stopped = true;
    try {
      const r = await agentCall({ action: 'stop', id: sid, why: '使用者在卡片上按了中斷' });
      log('· 已中斷 ' + r.stopped.join('、')
        + (r.jobs.length ? '（順手殺掉背景指令 ' + r.jobs.join('、') + '）' : ''));
    } catch (e) { log('· 中斷失敗：' + e.message); }
  });
  const stopped = function () { return box.stopped || (signal && signal.aborted); };

  box.msgs = box.msgs || [{ role: 'system', content:
    (type.prompt || '') + '\n\n'
    + '你是子代理，只負責完成交辦的這一件事：\n'
    + '- 問不到使用者，卡住就把卡在哪裡寫進結論。\n'
    + '- 做完用不超過 15 行回報結論，帶上關鍵的檔案與行號。\n'
    + '- 你的過程不會被主代理看到，只有最後這段文字會，所以結論要能單獨讀懂。\n\n'
    + [agentRules(), repoMap()].filter(Boolean).join('\n\n') }];
  const msgs = box.msgs;
  msgs.push({ role: 'user', content: task });
  let wrap = false;                 // 收工具了嗎（context 快滿的時候）

  const finish = async function (text) {
    // 有改動就留著 worktree 並且講清楚改在哪個分支 —— 子代理跑了十分鐘的結果，
    // 不能因為主代理沒接住就靜靜刪掉。沒改動的才自動清掉（照它們的規則）。
    let note = '';
    try {
      const r = await agentCall({ action: 'close', id: sid });
      if (r.committed) {
        note = '\n[worktree] 改了 ' + r.changes + ' 個檔案，已經 commit 在分支 '
          + r.branch + '。要收下就 ' + r.merge + '。\n' + (r.diff || '');
      } else if (r.kept) {
        note = '\n[worktree] 有 ' + r.changes + ' 個檔案改動，收不掉（' + (r.error || '')
          + '），留在 ' + r.path + '。';
      }
    } catch (e) { /* 已經收掉了就算了 */ }
    return text + note + '\n[子代理 ' + box.id + '] 要追問就用 task 帶 resume:"' + box.id + '"';
  };

  try {
    for (let i = 1; i <= SUB_ROUNDS; i++) {
      // 停止鍵與卡片上的「中斷」都要停得住。子代理原本自己開 AbortController，
      // 按停止只停得了主迴圈，它會一路跑到輪數用完。
      if (stopped()) {
        el.querySelector('.st').textContent = '已中斷';
        return await finish('（子代理被中斷了，任務沒有完成）');
      }
      // 預算是整輪共用的，所以子代理自己也要看 —— 只在主迴圈看的話，三個平行子代理
      // 會各自燒完 60 輪，主迴圈要等它們全部回來才發現超支。
      const over = budgetStop(S.run);
      if (over) {
        el.querySelector('.st').textContent = '超出預算';
        return await finish('（停在這裡：' + over + '）');
      }
      el.querySelector('.st').textContent = '第 ' + i + '/' + SUB_ROUNDS + ' 輪';

      // 子代理沒有壓縮，它的 context 只會一路長；60 輪跑到一半就會超過 num_ctx，
      // 而超過不會報錯 —— 最前面被丟掉的正好是它的任務。所以在滿之前**把工具收走**：
      // 沒有工具可叫，它只能把現在知道的寫成結論。收工具比用提示詞求它收尾可靠。
      if (!wrap && msgs.reduce(function (n, m) { return n + estTokens(m.content || ''); }, 0)
          * S.ctxRatio > ctxLimit() * SUB_CTX_AT) {
        wrap = true;
        msgs.push({ role: 'user', content: '你的 context 快滿了，不要再呼叫工具，'
          + '用現在已經知道的東西給結論，並且講清楚哪些部分還沒查完。' });
        log('· context 快滿了，收工具讓它做結論');
      }

      let text = '';
      let calls = null;
      const payload = { model: type.model || S.subModel || S.model, messages: msgs,
                        tools: wrap ? [] : subTools(type, at), stream: true };
      const opts = buildOptions();
      if (Object.keys(opts).length) payload.options = opts;
      if (thinkValue() !== null) payload.think = false;   // 子代理不用思考，省時間
      // 走 chatStream 而不是自己打 /api/chat：外部 API 那條路它已經處理好了。
      await chatStream(payload, signal, {
        think: function () { },
        images: function () { },
        content: function (t) { text += t; },
        tools: function (tc) { calls = (calls || []).concat(tc); },
        // 子代理的 token 也要算進同一本帳：不算的話三個平行子代理各跑 60 輪，
        // budgetStop() 完全看不到，外部 API 的帳單就沒有任何上限擋著。
        done: function (d) {
          if (!S.run || !d) return;
          S.run.tokens += (d.prompt_eval_count || 0) + (d.eval_count || 0);
          renderRunBar();
        }
      });

      const m = { role: 'assistant', content: text };
      if (calls && calls.length) m.tool_calls = calls;
      msgs.push(m);

      if (!calls || !calls.length) {
        const done = text.trim();
        el.querySelector('.tool-card').classList.add('done');
        el.querySelector('.st').textContent = '完成（' + i + ' 輪）';
        log('→ ' + (done || '（沒有結論）'));
        return await finish(done || '（子代理沒有給出結論）');
      }
      for (let k = 0; k < calls.length; k++) {
        if (stopped()) break;
        const fn = calls[k].function || {};
        const a = callArgs(calls[k]);
        log('· ' + fn.name + ' ' + JSON.stringify(a).slice(0, 120));
        const r = fn.name === 'task'
          ? { content: await runSubagent(a, at + 1, sid) }
          : await execTool(fn.name || '', a, { role: 'tool', tool_name: fn.name, content: '' },
                           sid);
        // 從別的地方（另一個分頁、curl）中斷時，工具會開始被伺服器拒絕。
        // 認出這件事就收工，不要用剩下的輪數去撞一道已經關上的門。
        if (String(r.content || '').indexOf('已經被中斷') >= 0) box.stopped = true;
        msgs.push({ role: 'tool', tool_name: fn.name, content: String(r.content || '') });
      }
    }
    el.querySelector('.st').textContent = '輪數用完';
    return await finish('（子代理跑了 ' + SUB_ROUNDS + ' 輪還沒有結論，任務可能太大，拆小一點再交辦）');
  } catch (e) {
    if (stopped()) {
      el.querySelector('.st').textContent = '已中斷';
      return await finish('（子代理被中斷了，任務沒有完成）');
    }
    el.querySelector('.st').textContent = '失敗';
    log('→ ' + e.message);
    return await finish('子代理失敗：' + e.message);
  }
}

// 同一輪來了好幾個 task 就一起發。**這是唯一值得平行化的東西**：每個子代理是一整條
// 獨立的模型迴圈，平行省的是真正的牆鐘時間；三個 read_file 平行跑省的只有微秒級的
// 本機 I/O，而同一輪一次送三個省的是兩輪 60k context 的 prefill（見 tech.md）。
//
// 會動到檔案的型別**要有自己的 worktree 才准平行**。共用工作區的兩個寫入者同時
// 動同一個檔案，收拾起來比省下的時間貴 —— 有了 isolation: worktree 之後，
// 衝突變成 merge 問題，那個有現成工具可以處理。
function startSubagents(calls, depth) {
  const running = {};
  const idx = [];
  for (let i = 0; i < calls.length; i++) {
    if (((calls[i] || {}).function || {}).name !== 'task') continue;
    const t = agentType(callArgs(calls[i]).type);
    if (t && (t.isolation === 'worktree' || !subWrites(t))) idx.push(i);
  }
  if (idx.length > 1) {
    idx.forEach(function (i) { running[i] = runSubagent(callArgs(calls[i]), depth || 1); });
  }
  return running;
}

function callArgs(call) {
  let a = ((call || {}).function || {}).arguments;
  if (typeof a === 'string') { try { a = JSON.parse(a); } catch (e) { a = {}; } }
  return a || {};
}

// 給 finishCheck 用的兩個訊號。只在工具**成功**之後記 ——
// 寫失敗的測試檔不算寫過，跑失敗的測試倒是算跑過（跑了就會看到紅字）。
function noteFinishSignals(name, args) {
  // 沒動過檔案就不必驗收：模型只是讀了一輪東西回答問題
  if (WRITE_TOOLS.indexOf(name) >= 0) S.run.wroteFiles = true;
  if (looksLikeTestRun(name, args)) S.run.ranTests = true;
  // delete_file 也在 WRITE_TOOLS 裡，但**刪掉一個測試檔不算寫了測試** ——
  // 算進去的話「寫了測試沒跑」會對著一個已經不存在的檔案叫
  else if (name !== 'delete_file' && WRITE_TOOLS.indexOf(name) >= 0
           && looksLikeTestFile((args || {}).path)) {
    S.run.wroteTests = String(args.path);
  }
}

async function runTools(c, calls, depth) {
  // 停在這裡不等於結束：把原因記在對話上，續跑條就會冒出來，按下去重新算一段。
  // 之前是 toast 一句就沒了 —— 跑了二十分鐘的任務停在半路，連怎麼接回去都沒說。
  const over = depth > MAX_TOOL_ROUNDS
    ? '工具連續跑了 ' + MAX_TOOL_ROUNDS + ' 輪，停在這裡' : budgetStop(S.run);
  if (over) {
    c.stopWhy = over;
    saveChats();
    stopRunTicker();
    markTurnDone(c);
    renderResumeBar();
    toast(over + ' —— 下面有「繼續」');
    return;
  }
  S.run.rounds = depth;
  renderRunBar();
  startRunTicker();
  // 不鎖輸入框了：跑十分鐘的任務不該讓人只能乾等。打字會排隊（submitFromInput），
  // 送出鍵維持「停止」的語意，所以按鍵仍然是停用的。
  blockComposer(RUNNING_HINT);
  const subs = startSubagents(calls);        // 同一輪的多個 explore 子代理一起發
  for (let i = 0; i < calls.length; i++) {
    const fn = (calls[i] || {}).function || {};
    const name = fn.name || '';
    const args = callArgs(calls[i]);

    const msg = { role: 'tool', tool_name: name, args: args, content: '' };

    // 這兩支不送到 /tool：伺服器上沒有人可以回答，模型迴圈也在瀏覽器這一端
    if (name === 'ask_user_question') {
      msg.content = await askUser(args);
      c.messages.push(msg);
      saveChats();
      continue;
    }
    if (name === 'task') {
      msg.content = await (subs[i] || runSubagent(args));
      c.messages.push(msg);          // 只存結論：過程留在子代理自己的 context 裡
      saveChats();
      continue;
    }

    await execTool(name, args, msg);
    if (msg.streamed) {
      c.messages.push(msg);
      saveChats();
      $('thread').appendChild(buildToolMsg(msg));
      pin();
      continue;
    }
    c.messages.push(msg);
    saveChats();
    $('thread').appendChild(buildToolMsg(msg));
    pin();
  }
  blockComposer('');
  // 壓在這裡不是隨便挑的：這一輪的工具結果都進去了，下一次模型呼叫還沒發出去。
  // 壓在迴圈開頭的話，發出這幾個呼叫的那則 assistant 訊息可能被摘要掉，
  // 後面接上去的 tool 結果就變成沒有來源的孤兒。
  if (await autoCompact(c)) toast('context 快滿了，已經自動壓縮先前的訊息');
  flushQueue(c);          // 使用者在這一輪打的字，跟工具結果一起送過去
  await runStream(c, depth);
}

