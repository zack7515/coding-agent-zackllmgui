// 工具：定義、確認、執行、待辦、允許規則。
// 留在這裡的是「模型要動手之前經過的那條路」，檔案面板與子代理已經搬走。

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
  btn.classList.toggle('on', !!S.ws.write && !why);   // 灰掉還亮著等於騙人
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
    saveConfig();          // 下次開頁面靠這個值把權限推回去，不能只等關視窗才存
    toast(S.ws.write ? '模型可以修改檔案了，每一次仍然要你按確認' : '已改回唯讀');
  } catch (e) {
    toast('切換失敗：' + e.message);
  }
  renderWriteBtn();
  renderFeatBtn();
}

// 重開之後把 serve.py 那一端的狀態接回來。工作區與四個開關是行程全域、
// 重啟就回預設，自動模式卻存在 localStorage —— 兩邊不對齊的症狀是
// 「自動模式放到最開但模型改不動檔案」，看起來像壞掉。
// **工作區一定要先設**：沒有工作區的時候 write:true 會被靜靜吃掉。
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
  warnMissingTools(data.missing_tools);
  return data;
}

// 工作區用得到的工具鏈這台沒有（C# 專案但沒裝 .NET SDK 是最典型的）。
// **只提醒不代裝**：裝 SDK 是使用者的決定，沙盒裡也沒有網路。
// 記在記憶體不寫進設定檔 —— 裝完重新整理就該重問一次，而不是永遠閉嘴。
const askedTools = new Set();
async function warnMissingTools(list) {
  for (const m of (list || [])) {
    const key = (S.ws.path || '') + '|' + m.lang;
    if (askedTools.has(key)) continue;
    askedTools.add(key);
    await painted();                   // confirm() 會擋住主執行緒，先讓 toast 畫出來
    const url = (String(m.how).match(/https?:\/\/\S+/) || [])[0];
    const ok = confirm('這個工作區有 ' + m.lang + ' 的程式碼，但這台沒有裝'
      + m.what + '（找不到 ' + m.tool + '）。\n\n'
      + '編譯與測試會跑不動，模型也已經被告知不要自己去裝。\n\n'
      + m.how + '\n\n'
      + (url ? '要開下載頁嗎？' : '知道了嗎？'));
    if (ok && url) window.open(url, '_blank', 'noopener');
  }
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
