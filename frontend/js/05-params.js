/* ══════════════════════ 思考模式 ══════════════════════ */
function thinkOptions() {
  const caps = S.caps[S.model] || [];
  if (caps.indexOf('thinking') < 0) return [];
  return THINK_LEVELS;
}

function renderThinkSeg() {
  const opts = thinkOptions();
  const seg = $('thinkSeg');
  const enabled = opts.length > 0;
  const list = enabled ? opts : THINK_TOGGLE;

  const values = list.map(function (o) { return o[1]; });
  // **只在控制項真的能用的時候才正規化。** S.caps 是跟伺服器問回來的，第一次
  // render 時還是空的 —— 那時候 list 是 THINK_TOGGLE（值只有 true/false），
  // 存好的 'high' 不在裡面就被洗成 false，於是每次重新整理都跳回「關」。
  if (enabled && values.indexOf(S.think) < 0) S.think = values[0];

  seg.innerHTML = '';
  seg.classList.toggle('disabled', !enabled);
  list.forEach(function (opt, i) {
    const b = document.createElement('button');
    b.textContent = opt[0];
    b.disabled = !enabled;
    if (opt[1] === S.think) b.className = 'on';
    b.addEventListener('click', function () {
      S.think = opt[1];
      renderThinkSeg();
      saveConfig();
    });
    seg.appendChild(b);
  });

  // 只留「這個模型能不能用」這一句狀態，其餘說明收進驚嘆號 ——
  // 那段字每次打開參數面板都在，看第二次就變成雜訊了。
  $('thinkNote').textContent = enabled ? ''
    : (S.model || '此模型') + ' 不支援 thinking，控制項已停用。';
  $('thinkNote').hidden = enabled;
}

function openThinkHelp() {
  openHelp('思考模式', [
    ['這是什麼', '讓模型先想再答。想的過程會另外顯示，不算在回覆裡。'],
    ['強度分級', 'Ollama 的「思考預算」就是這五段（關／低／中／高／最高，'
      + '對應 think: false / low / medium / high / max），'
      + '**沒有**用 token 數設預算的介面。'],
    ['想硬性設上限', '用 num_predict —— 它算的是輸出總量，思考也計入。'],
    ['模型不支援分級', '會把任何一個等級當成「開啟」，不會報錯。'],
    ['模型完全不支援', '控制項會停用，送出時整個 think 欄位省略，不會送出去。']
  ]);
}

function thinkValue() { return thinkOptions().length ? S.think : null; }

// --fs 宣告在 :root，這裡也設在同一個節點上。設在 body 或宣告在 body
// 都會踩到「body 自己的宣告贏過繼承值」，那次的症狀是設定完全沒反應。
function applyFontScale() {
  document.documentElement.style.setProperty('--fs', String(S.fontScale));
  // 使用者正在那個欄位裡打字時不要蓋掉他打到一半的東西
  if ($('fsValue') && document.activeElement !== $('fsValue')) $('fsValue').value = fontLabel();
  if ($('fsMinus')) $('fsMinus').disabled = fontPct() <= FONT_MIN;
  if ($('fsPlus')) $('fsPlus').disabled = fontPct() >= FONT_MAX;
}

function fontPct() { return Math.round(S.fontScale * 100); }
function fontLabel() { return fontPct() + '%'; }

// 一次 1%。夾在 70～200 之間，存起來下次打開直接套用。
function stepFont(delta) { setFont(fontPct() + delta); }

// 直接輸入的入口。打不出數字就把欄位還原成現值，不要留一個看不懂的狀態。
function setFont(pct) {
  const n = parseInt(String(pct).replace('%', '').trim(), 10);
  if (isNaN(n)) { applyFontScale(); return; }
  S.fontScale = Math.min(FONT_MAX, Math.max(FONT_MIN, n)) / 100;
  applyFontScale();
  saveConfig();
}

/* ══════════════════════ 參數 ══════════════════════ */
function buildOptions() {
  const p = S.params;
  const o = {
    temperature: +(+p.temperature).toFixed(3),
    top_p: +(+p.top_p).toFixed(3),
    top_k: parseInt(p.top_k, 10),
    repeat_penalty: +(+p.repeat_penalty).toFixed(3)
  };
  const minP = +(+p.min_p).toFixed(3);
  if (minP > 0) o.min_p = minP;

  // 欄位的單位是 K，送出去的是 token。使用者要輸入的是「64」不是「65536」——
  // 後者要數零，而且數錯一位就是十倍的記憶體。
  const ctx = ctxTokens(p.num_ctx);
  if (ctx > 0) o.num_ctx = ctx;

  const np = parseInt(p.num_predict, 10);
  if (!isNaN(np) && np !== -1) o.num_predict = np;

  const seed = parseInt(p.seed, 10);
  if (!isNaN(seed) && seed >= 0) o.seed = seed;

  const stops = String(p.stop || '').split(',').map(function (s) { return s.trim(); })
    .filter(function (s) { return s; });
  if (stops.length) o.stop = stops;

  // 空字串一律不送，讓 Ollama 自己決定；填了才覆寫
  ['num_keep', 'num_batch', 'num_gpu', 'num_thread', 'draft_num_predict'].forEach(function (k) {
    const raw = String(p[k] === undefined ? '' : p[k]).trim();
    if (!raw) return;
    const v = parseInt(raw, 10);
    if (!isNaN(v)) o[k] = v;
  });
  return o;
}

function buildSliders() {
  const box = $('sliders');
  SLIDERS.forEach(function (cfg) {
    const row = document.createElement('div');
    row.className = 'slider-row';
    row.innerHTML =
      '<div class="slider-top"><span class="l">' + cfg.label + '</span>' +
      '<input class="badge" id="badge_' + cfg.id + '" inputmode="decimal" ' +
      'spellcheck="false" title="可以直接輸入，範圍 ' + cfg.min + '～' + cfg.max + '"></div>' +
      '<input type="range" id="range_' + cfg.id + '" min="' + cfg.min + '" max="' + cfg.max +
      '" step="' + cfg.step + '">';
    box.appendChild(row);
    // 一定要指明 [type=range]：數值那格現在也是 <input>，而且排在前面 ——
    // 只寫 'input' 會拿到它，於是滑桿完全沒有監聽器，拖了沒反應。
    const input = row.querySelector('input[type=range]');
    const badge = row.querySelector('.badge');
    const sync = function () {
      S.params[cfg.id] = cfg.dec ? parseFloat(input.value) : parseInt(input.value, 10);
      if (document.activeElement !== badge) badge.value = (+input.value).toFixed(cfg.dec);
    };
    input.addEventListener('input', function () { sync(); });
    input.addEventListener('change', saveConfig);

    // 數字也能直接打。滑桿調得到 0.7 卻調不到 0.75 的時候（step 再小都會有這種時候），
    // 沒有輸入框就只能認了。打壞了就還原成現值，不要留一個看不懂的狀態。
    const typed = function () {
      const v = parseFloat(String(badge.value).trim());
      if (isNaN(v)) { badge.value = (+input.value).toFixed(cfg.dec); return; }
      input.value = Math.min(cfg.max, Math.max(cfg.min, v));
      sync();
      badge.value = (+input.value).toFixed(cfg.dec);
      saveConfig();
    };
    badge.addEventListener('change', typed);
    badge.addEventListener('blur', typed);
    badge.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); badge.blur(); }
    });
    cfg.el = input; cfg.badge = badge; cfg.sync = sync;
  });
}

// num_gpu / num_thread 的上限是動態的，載入模型或連上 serve.py 之後才算得出來。
function applyParamLimits() {
  PARAM_HELP.forEach(function (row) {
    if (!row[2]) return;
    const lim = row[2]();
    const el = $(row[0]);
    const unit = lim.unit || '';
    if (lim.max > 0) {
      el.max = lim.max;
      el.title = lim.note;
    } else {
      el.removeAttribute('max');
      el.title = lim.note;
    }
    const label = document.querySelector('label[for="' + row[0] + '"]');
    const base = row[3] || row[0];          // num_ctx 的標籤要保留「（K）」這個單位
    // 上限接進既有的括號裡：num_ctx（K，≤ 256），不要變成兩組括號
    if (label) {
      label.textContent = lim.max <= 0 ? base
        : (base.slice(-1) === '）'
          ? base.slice(0, -1) + '，≤ ' + lim.max + '）'
          : base + '（≤ ' + lim.max + unit + '）');
    }
  });
}

// 超過上限就夾回去。不擋輸入、只在離開欄位時修正 —— 打字打到一半被搶走游標很難用。
function clampField(id) {
  const row = PARAM_HELP.filter(function (r) { return r[0] === id; })[0];
  if (!row || !row[2]) return;
  const lim = row[2]();
  const raw = String($(id).value).trim();
  if (!raw || lim.max <= 0) return;
  const v = parseInt(raw, 10);
  if (isNaN(v)) return;
  const fixed = Math.max(0, Math.min(v, lim.max));
  if (fixed !== v) {
    $(id).value = fixed;
    S.params[id] = String(fixed);
    toast(id + ' 最多 ' + lim.max + (lim.unit || '') + '（' + lim.note + '），已改成 ' + fixed);
  }
}

// 一個說明對話框給所有 ! 按鈕共用 —— 版面上只留一個問號，內容按需要換。
// rows: [[標題, 說明, 附註?], …]
function openHelp(title, rows, note) {
  $('phTitle').textContent = title;
  $('phBody').innerHTML = rows.map(function (r) {
    return '<div class="row"><span class="n">' + esc(r[0]) + '</span>' +
      '<span class="d">' + esc(r[1]) + '</span>' +
      (r[2] ? '<span class="lim">' + esc(r[2]) + '</span>' : '') + '</div>';
  }).join('');
  $('phNote').innerHTML = note || '';
  $('phNote').hidden = !note;
  $('phOverlay').classList.remove('hidden');
}

function openParamHelp() {
  openHelp('進階參數', PARAM_HELP.map(function (row) {
    const lim = row[2] ? row[2]() : null;
    return [row[0], row[1],
      lim ? '上限 ' + (lim.max > 0 ? lim.max : '未知') + ' · ' + lim.note : ''];
  }), '欄位空著就<b>完全不送出該參數</b>，由 Ollama 自己決定 —— 提示字是它的預設值。' +
     'Ollama 不認得的參數會被忽略，所以填了沒作用時先確認你的版本與模型支不支援。');
}

function paramsToUi() {
  SLIDERS.forEach(function (cfg) {
    cfg.el.value = S.params[cfg.id];
    cfg.badge.value = (+S.params[cfg.id]).toFixed(cfg.dec);
  });
  applyParamLimits();
  FIELDS.forEach(function (k) { $(k).value = S.params[k]; });
  $('system').value = S.params.system || '';
  $('showThink').checked = S.showThink;
}

