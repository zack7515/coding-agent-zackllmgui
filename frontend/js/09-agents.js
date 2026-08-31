// 子代理：開起來、跑迴圈、收工，加上介面上的定位／追溯／中斷。
// 原本一半在 07-tools 一半在 08-ui，而後者只是為了呼叫前者。

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
