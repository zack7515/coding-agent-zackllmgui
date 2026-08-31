// 模型：下載、刪除、卸載，以及多模型比較。

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
