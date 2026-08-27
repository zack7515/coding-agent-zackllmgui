#!/usr/bin/env node
// zackllmgui.html 的自我檢查：node test_gui.js
// 只驗兩件事——整份腳本能不能被解析，以及不依賴 DOM 的純函式對不對。

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'zackllmgui.html'), 'utf8');
const script = /<script>\n([\s\S]*?)<\/script>/.exec(html)[1];

// 1. 整份腳本要能解析（少一個括號就會在這裡爆）
new Function(script);
console.log('ok   腳本可解析');

// 2. 把不碰 DOM 的片段挖出來單獨跑
function grab(name, kind) {
  // 靠「頂層區塊都在第一欄收尾」來切，不用括號配對——程式碼裡的正規表示式
  // 含有 { 與 [，配對法會被騙。
  const isConst = kind === 'const';
  const start = script.indexOf((isConst ? 'const ' : 'function ') + name);
  assert.ok(start >= 0, '找不到 ' + name);
  let mark = '\n}';
  if (isConst) {
    const line = script.slice(start, script.indexOf('\n', start));
    // 一行寫完的 const（行尾註解不算）。少了這個，const A = 1; // 說明
    // 會被當成多行，一路抓到下一個 \n}; 為止。
    if (/;\s*(\/\/.*)?$/.test(line)) return line.replace(/\/\/.*$/, '');
    mark = /=\s*\[/.test(script.slice(start, start + 60)) ? '\n];' : '\n};';
  }
  const end = script.indexOf(mark, start);
  assert.ok(end > 0, '抓不完整：' + name);
  return script.slice(start, end + mark.length);
}

const sandbox = [grab('EXT_LANG', 'const'), grab('extOf'), grab('fenceFor'), grab('estTokens'),
  'return { fenceFor: fenceFor, estTokens: estTokens, extOf: extOf };'].join('\n');
const fn = new Function(sandbox)();

// 副檔名對照
assert.strictEqual(fn.extOf('a/b/Main.PY'), 'py');
assert.ok(fn.fenceFor('main.py', 'print(1)').includes('```python'), '應該標成 python');
assert.ok(fn.fenceFor('note.txt', 'hi').includes('```\nhi'), 'txt 不標語言');
assert.ok(fn.fenceFor('x.unknownext', 'hi').includes('```\nhi'), '沒認出來就不標語言');
console.log('ok   code block 語言標籤');

// 內容本身含 ``` 時，圍籬要加長，否則 code block 會被提前關掉
const nested = fn.fenceFor('a.md', 'before\n```js\nx\n```\nafter');
assert.ok(nested.startsWith('檔案：a.md\n````'), '圍籬沒有加長：\n' + nested);
assert.ok(nested.trimEnd().endsWith('````'), '收尾圍籬沒有加長');
console.log('ok   巢狀 code block 圍籬');

// token 估算：CJK 一字約一 token，ASCII 約四字元一 token
assert.strictEqual(fn.estTokens('中文測試'), 4);
assert.strictEqual(fn.estTokens('abcdefgh'), 2);
assert.strictEqual(fn.estTokens(''), 0);
assert.ok(fn.estTokens('中文 mixed text') > fn.estTokens('mixed text'));
console.log('ok   token 估算');

// 貼上長文時猜副檔名
const g = new Function(grab('guessExt') + '\nreturn guessExt;')();
assert.strictEqual(g('def main():\n    print(1)\n'), 'py');
assert.strictEqual(g('{\n  "a": 1\n}'), 'json');
assert.strictEqual(g('SELECT * FROM t;'), 'sql');
assert.strictEqual(g('const x = () => 1;'), 'js');
assert.strictEqual(g('sudo apt install foo'), 'sh');
assert.strictEqual(g('就是一段普通的中文字'), 'txt');
console.log('ok   貼上內容的語言判斷');

// 預設提示要夠具體：本機小模型吃不動一句話的指令
const presets = new Function(grab('PRESETS', 'const') + '\nreturn PRESETS;')();
assert.ok(presets.length >= 7, '預設組數變少了');
presets.forEach(function (pair) {
  assert.ok(pair[1].length > 200, pair[0] + ' 的提示太短了（' + pair[1].length + ' 字）');
  assert.ok(/規則|禁止/.test(pair[1]), pair[0] + ' 少了規則或禁止事項');
});
console.log('ok   系統提示預設 (' + presets.length + ' 組)');

// num_ctx 欄位的單位是 K：使用者輸入 64，送出去的是 65536。
// 換算錯一位就是十倍的記憶體，所以兩個方向都要驗。
assert.ok(/num_ctx:\s*64\b/.test(script), 'num_ctx 預設值不是 64（K）');
const ct = new Function(grab('ctxTokens') + '\nreturn ctxTokens;')();
assert.strictEqual(ct('64'), 65536);
assert.strictEqual(ct('64K'), 65536, '打了 K 也要收');
assert.strictEqual(ct('1.5'), 1536, '小數要收');
assert.strictEqual(ct(''), 0);
assert.strictEqual(ct('abc'), 0);
assert.strictEqual(ct('-8'), 0);
console.log('ok   num_ctx 以 K 為單位');

// 模型下載：Hugging Face 的各種貼法都要收得下來
const np = new Function(grab('normalizePull') + '\nreturn normalizePull;')();
assert.strictEqual(np('qwen3:8b'), 'qwen3:8b');
assert.strictEqual(np('  ollama pull qwen3:8b '), 'qwen3:8b');
assert.strictEqual(np('hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M'),
  'hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M');
assert.strictEqual(np('https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF'),
  'hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF');
assert.strictEqual(np('huggingface.co/user/repo/tree/main'), 'hf.co/user/repo');
assert.strictEqual(np('ollama run hf.co/user/repo:Q8_0'), 'hf.co/user/repo:Q8_0');
assert.strictEqual(np(''), '');
console.log('ok   模型下載名稱正規化（含 Hugging Face）');

// 進階參數：拿掉的兩個不能還在，欄位要有預設值提示
assert.ok(!/presence_penalty|frequency_penalty/.test(html), 'presence/frequency_penalty 還沒清乾淨');
['num_ctx', 'num_predict', 'seed', 'keep_alive', 'num_batch'].forEach(function (id) {
  assert.ok(new RegExp('id="' + id + '"[^>]*placeholder=').test(html), id + ' 少了預設值提示');
});
console.log('ok   進階參數欄位');

// 工具定義由後端供給，前端不能再自己寫一份（兩份 schema 遲早對不上）。
// 認的是 schema 的特徵（parameters: { type: 'object' }）而不是 type:'function' ——
// 後者在把 tool call 轉成 OpenAI 格式時本來就會出現，那是格式轉換不是 schema。
assert.ok(!/parameters\s*:\s*\{\s*type\s*:\s*['"]object/.test(script),
  '前端又出現硬寫的工具 schema');
assert.ok(/function toolDefs\(\)/.test(script) && /S\.toolDefs \|\| \[\]/.test(script),
  'toolDefs 沒有改用後端給的定義');
assert.ok(/tool_defs/.test(script), '沒有從 /upstream 取回 tool_defs');
console.log('ok   工具定義來自後端');

// diff 上色
const rd = new Function(grab('esc') + '\n' + grab('renderDiff') + '\nreturn renderDiff;')();
const html2 = rd('@@ -1 +1 @@\n-old\n+new\n 同一行');
assert.ok(html2.indexOf('<span class="d">-old</span>') >= 0, html2);
assert.ok(html2.indexOf('<span class="a">+new</span>') >= 0, html2);
assert.ok(html2.indexOf('<span class="h">@@') >= 0, html2);
console.log('ok   diff 上色');

// 檔案檢視：行號要對，diff 的行號要跟著 @@ 走（debug 全靠這個）
const cv = new Function(grab('esc') + '\n' + grab('codeLines') + '\n' + grab('diffLines') +
  '\nreturn { codeLines: codeLines, diffLines: diffLines };')();
const code = cv.codeLines('a\nb\nc\n');
assert.strictEqual((code.match(/class="cl"/g) || []).length, 3, '行數不對：' + code);
assert.ok(code.indexOf('<span class="ln">2</span>') >= 0, '沒有行號');

const dl = cv.diffLines('--- a\n+++ b\n@@ -10,3 +10,4 @@\n ctx\n-gone\n+added\n more\n');
assert.ok(dl.indexOf('<span class="ln">10</span><span class="lt"> ctx') >= 0, '起始行號沒跟著 @@：' + dl);
assert.ok(/class="cl del"><span class="ln"><\/span>/.test(dl), '刪除行不該有新檔行號');
assert.ok(dl.indexOf('<span class="ln">11</span><span class="lt">+added') >= 0, '新增行的行號不對');
console.log('ok   檔案檢視的行號與 diff');

// 等第一個字的那段時間要看得出「還在跑、跑了多久」——
// 只有一個游標閃在空白畫面上，分不出「在想」跟「當掉」。
const wt = new Function(grab('fmtElapsed') + '\n' + grab('waitText') +
  '\nreturn waitText;')();
assert.ok(wt(3000, '', true).indexOf('等模型回應') === 0, '還沒收到字時要說在等');
assert.ok(wt(65000, '', true).indexOf('1 分 5 秒') > 0, '秒數要自己走');
assert.strictEqual(wt(3000, 'abc', true), '', '思考看得見的時候不必再畫一行');
assert.ok(wt(3000, 'abc', false).indexOf('思考中') === 0,
  '「顯示思考」關著時，思考那幾分鐘畫面上要有東西');
assert.ok(/if \(!content && !retrying\) waitLine\(\);/.test(script),
  'flush 不再更新等待狀態的話，秒數就不走了');
console.log('ok   等回應時看得出還在跑');

// 系統用量：拿不到的那一格要整格不畫。畫成 0 的話，「沒有這張卡」跟
// 「這張卡現在很閒」在畫面上長得一模一樣。
const sc = new Function(grab('sysCell') + '\nreturn sysCell;')();
assert.strictEqual(sc('vram', null), null);
assert.strictEqual(sc('vram', { gpu: [] }), null, '沒有卡就不該畫 VRAM');
assert.strictEqual(sc('cpu', { cpu: -1 }), null, '第一次取樣沒有基準，不能畫成 0%');
assert.strictEqual(sc('vram', { gpu: [{ vram: { used: 8.25, total: 12 } }] }).text, '8.3/12 G');
assert.strictEqual(sc('ram', { ram: { used: 6.7, total: 62.5 } }).text, '6.7/63 G');
const sl = new Function(grab('sysLevel') + '\nreturn sysLevel;')();
assert.deepStrictEqual([sl(0.5), sl(0.8), sl(0.95)], ['', 'hot', 'full']);
console.log('ok   系統用量只畫得出來的那幾格');

// 自動模式：危險指令永遠要人看過，計畫永遠要人核准
const am = new Function('let S;\n' + grab('AUTO_MODES', 'const') + '\n' +
  grab('READ_ONLY_TOOLS', 'const') + '\n' + grab('WRITE_TOOLS', 'const') + '\n' + grab('autoApprove') +
  '\nreturn function (mode, name, risk, scope) { S = { auto: mode }; ' +
  'return autoApprove(name, risk, null, scope); };')();
assert.strictEqual(am('off', 'read_file', 'ok'), false, '關閉時不該自動放行');
assert.strictEqual(am('read', 'read_file', 'ok'), true);
assert.strictEqual(am('read', 'edit_file', 'ok'), false, '唯讀模式不該放行寫入');
assert.strictEqual(am('read', 'run_shell', 'ok'), false);
assert.strictEqual(am('full', 'edit_file', 'ok'), true);
assert.strictEqual(am('full', 'run_shell', 'risky'), false, '危險指令永遠要問');
assert.strictEqual(am('full', 'run_shell', 'block'), false);
assert.strictEqual(am('full', 'submit_plan', 'ok'), false, '計畫永遠要人核准');
// 「改檔案自動」：改檔案放行，跑指令還是要問 —— 這一格的整個意義就在這條界線上
assert.strictEqual(am('edit', 'read_file', 'ok'), true);
assert.strictEqual(am('edit', 'write_file', 'ok'), true);
assert.strictEqual(am('edit', 'edit_file', 'ok'), true);
assert.strictEqual(am('edit', 'run_shell', 'ok'), false, '改檔案自動不該連指令一起放行');
assert.strictEqual(am('edit', 'run_tests', 'ok'), false);
assert.strictEqual(am('edit', 'setup_env', 'ok'), false, 'setup_env 要連網裝套件，不算改檔案');
assert.strictEqual(am('edit', 'submit_plan', 'ok'), false, '計畫永遠要人核准');
// 「工作區內全自動」：只有後端算出 scope === 'ws' 的風險指令才放行。
// 這一格是唯一一個會讓風險指令不問人的地方，所以每個邊界都要有一條。
assert.strictEqual(am('ws', 'run_shell', 'risky', 'ws'), true, '工作區內的 rm 不該再問');
assert.strictEqual(am('ws', 'run_shell', 'risky', ''), false, '動到工作區外還是要問');
assert.strictEqual(am('ws', 'run_shell', 'block', 'ws'), false, 'block 那級永遠不放行');
assert.strictEqual(am('ws', 'edit_file', 'ok'), true);
assert.strictEqual(am('ws', 'submit_plan', 'ok'), false, '計畫永遠要人核准');
assert.strictEqual(am('full', 'run_shell', 'risky', 'ws'), false,
  'scope 只有在「工作區內全自動」那一格才算數');
console.log('ok   自動模式的放行規則');

// 五個檔位的順序：從嚴格到寬鬆，中間不能跳號（之後 Shift+Tab 循環要照這個順序）
const SYS_ORDER = new Function(grab('SYS_METRICS', 'const') +
  '\nreturn SYS_METRICS.map(function (m) { return m[0]; });')();
assert.strictEqual(SYS_ORDER[0], 'vram', 'VRAM 要排第一 —— 窄畫面只留得下第一格');
const AUTO_MODES_ORDER = new Function(grab('AUTO_MODES', 'const') +
  '\nreturn AUTO_MODES.map(function (m) { return m[0]; });')();
assert.deepStrictEqual(AUTO_MODES_ORDER, ['off', 'read', 'edit', 'full', 'ws']);

// 前端不再自己判斷指令風險（後端說了算）
assert.ok(!/const RISKY = \//.test(script), '前端又出現自己的風險判斷');

// 選單裡點一個會再開子選單的項目時，事件不能冒泡到 document ——
// 那個「點外面就關」的監聽器會把剛開好的子選單當成外面點的，立刻收掉。
// 自動模式的三段選單就是這樣一直打不開的。
assert.ok(/b\.addEventListener\('click', function \(e\) \{ e\.stopPropagation\(\); closeMenu\(\);/
  .test(script), '選單項目沒有擋住事件冒泡，子選單會一開就被關掉');
assert.ok(!/setTimeout\(function \(\) \{ openMenu = menu; \}, 0\)/.test(script),
  '那行 setTimeout 是舊的權宜之計，已經沒有用了');
console.log('ok   子選單不會被自己關掉');

// 工具端點只有一個入口：端出這個網頁的那台。之前做過本機／伺服器切換，
// 但使用模式是「每個人在自己機器上跑 serve.py、GPU 指向遠端」，切換沒有存在意義。
for (const p of ['/tool', '/tools', '/workspace', '/preview', '/view', '/restore', '/run', '/git']) {
  assert.ok(script.includes("apiUrl('" + p + "')"), p + ' 沒有走 apiUrl');
}
assert.ok(!/agentUrl|S\.agent\b/.test(script), '工具後端切換的殘留程式碼還在');
console.log('ok   工具端點單一入口');

// 長指令要走串流，不然跑測試時畫面整整卡住看不出來在做什麼
assert.ok(/S\.streamTools\.indexOf\(name\) >= 0/.test(script), 'runTools 沒有分派到串流路徑');
assert.ok(/apiUrl\('\/run'\)/.test(script), '串流沒有打到 /run');
console.log('ok   長指令走串流');

// 字體只縮放「要讀的內容」，介面的按鈕標籤不跟著變大
for (const sel of ['.bubble', '.msg-body{', '.think-body', '#input{']) {
  const at = html.indexOf(sel);
  assert.ok(at > 0, '找不到規則 ' + sel);
  assert.ok(/calc\([\d.]+px \* var\(--fs\)\)/.test(html.slice(at, at + 260)),
    sel + ' 沒有跟著字體倍率走');
}
assert.ok(/--fs:1;/.test(html), '沒有定義字體倍率變數');
// 字體：- 100% + 的 stepper，一次 1%，夾在 70～200
const fsz = new Function(
  'const S = { fontScale: 1 };\n' +
  'const $ = () => null;\n' +
  'function saveConfig() {}\n' +
  'const document = { documentElement: { style: { setProperty() {} } } };\n' +
  grab('FONT_MIN', 'const') + '\n' + grab('applyFontScale') + '\n' +
  grab('fontPct') + '\n' + grab('fontLabel') + '\n' + grab('stepFont') + '\n' +
  grab('setFont') + '\n' +
  'return { step: stepFont, set: setFont, pct: fontPct, label: fontLabel, S: S };')();
fsz.step(1);
assert.strictEqual(fsz.pct(), 101, '一次應該只動 1%');
for (let i = 0; i < 500; i++) fsz.step(1);
assert.strictEqual(fsz.pct(), 200, '沒有夾在上限');
for (let i = 0; i < 500; i++) fsz.step(-1);
assert.strictEqual(fsz.pct(), 70, '沒有夾在下限');
assert.strictEqual(fsz.label(), '70%');
// 中間那個數值要能直接打，不是只有 + −
fsz.set('125');
assert.strictEqual(fsz.pct(), 125, '直接輸入沒有生效');
fsz.set('300%');
assert.strictEqual(fsz.pct(), 200, '直接輸入沒有夾在上限');
fsz.set('abc');
assert.strictEqual(fsz.pct(), 200, '打不出數字時不該把設定弄壞');
// 倍率變數一定要宣告在 :root：宣告在 body 的話 body 自己的值會贏過繼承值
assert.ok(/:root\{[\s\S]{0,400}--fs:1;/.test(html), '--fs 沒有宣告在 :root，設定會沒反應');
assert.ok(!/body\{[^}]*--fs:/.test(html), '--fs 又被宣告到 body 上了');
console.log('ok   字體大小可調');

// thinking 強度：Ollama 的「思考預算」就是這個，且不限 gpt-oss
const lv = new Function(grab('THINK_LEVELS', 'const') + '\nreturn THINK_LEVELS;')();
assert.deepStrictEqual(lv.map(x => x[1]), [false, 'low', 'medium', 'high', 'max'],
  '強度分級要跟 Ollama 認得的值一致');
assert.ok(!/gpt-oss/i.test(grab('thinkOptions')),
  'thinking 分級不該只開給 gpt-oss，其他 thinking 模型也吃得下');
console.log('ok   thinking 強度分級');

// 進階參數的說明與上限：num_gpu 看模型層數、num_thread 看 CPU 核心數
const stub = 'const S = {};\nfunction ollamaIsLocal() { return true; }\n';
const help = new Function(stub + grab('PARAM_HELP', 'const') + '\nreturn PARAM_HELP;')();
const fieldIds = new Function(grab('FIELDS', 'const') + '\nreturn FIELDS;')();
assert.deepStrictEqual(help.map(h => h[0]).sort(), fieldIds.slice().sort(),
  '每個進階欄位都要有說明，不能多也不能少');
help.forEach(h => assert.ok(h[1].length > 20, h[0] + ' 的說明太短，等於沒寫'));

const lim = new Function(
  'const S = { model: "m", layers: { m: 65 }, cpus: 32, ctxMax: { m: 262144 } };\n' +
  'function ollamaIsLocal() { return true; }\n' +
  grab('PARAM_HELP', 'const') +
  '\nreturn PARAM_HELP.filter(r => r[2]).map(r => [r[0], r[2]()]);')();
// num_ctx 的上限是模型的 context_length 換算成 K：262144 / 1024 = 256
assert.deepStrictEqual(lim.map(x => [x[0], x[1].max]),
  [['num_ctx', 256], ['num_gpu', 65], ['num_thread', 32]],
  '上限沒有從模型的 context_length / 層數 / CPU 核心數算出來');

const noInfo = new Function(
  'const S = { model: "", layers: {}, cpus: 0, ctxMax: {} };\n' +
  'function ollamaIsLocal() { return true; }\n' +
  grab('PARAM_HELP', 'const') +
  '\nreturn PARAM_HELP.filter(r => r[2]).map(r => r[2]().max);')();
assert.deepStrictEqual(noInfo, [0, 0, 0], '資訊不足時要回 0（不設上限），不能亂猜一個數字');

// 上限要接進既有的括號裡（num_ctx（K，≤ 256）），問不到就整個不顯示、
// 連 max 屬性都要拿掉 —— 掰一個數字出來比沒有更糟，使用者會照著它調
const lab = new Function(
  "const els = {}, labels = {};\n" +
  "const S = { model: 'm', ctxMax: {}, layers: {}, cpus: 0 };\n" +
  "function ollamaIsLocal() { return true; }\n" +
  "const $ = (id) => els[id] || (els[id] = { max: '', title: '',\n" +
  "  removeAttribute(k) { this[k] = '(removed)'; } });\n" +
  "const document = { querySelector: (sel) => {\n" +
  "  const id = sel.match(/for=\"(\\w+)\"/)[1];\n" +
  "  return labels[id] || (labels[id] = { textContent: '' }); } };\n" +
  grab('PARAM_HELP', 'const') + '\n' +
  script.slice(script.indexOf('function applyParamLimits'),
               script.indexOf('// 超過上限就夾回去')) +
  "\nreturn function (max) { S.ctxMax = max ? { m: max } : {};\n" +
  "  applyParamLimits();\n" +
  "  return [labels.num_ctx.textContent, String(els.num_ctx.max)]; };")();
assert.deepStrictEqual(lab(262144), ['num_ctx（K，≤ 256）', '256'],
  '上限沒有接進原本的括號裡');
assert.deepStrictEqual(lab(0), ['num_ctx（K）', '(removed)'],
  '問不到上限時，標籤與 max 屬性都要乾淨');
const remote = new Function(
  'const S = { model: "m", layers: { m: 65 }, cpus: 32, upstream: "http://gpu:11434" };\n' +
  'function ollamaIsLocal() { return false; }\n' + grab('PARAM_HELP', 'const') +
  '\nreturn PARAM_HELP.filter(r => r[0] === "num_thread")[0][2]();')();
assert.strictEqual(remote.max, 0, 'Ollama 在遠端時不該拿本機核心數當 num_thread 的上限');
// 判斷本機與否要問後端（它會解析主機名比對自己的位址），不要在前端比字串
assert.ok(/S\.srv\.ollamaLocal/.test(script), 'ollamaIsLocal 沒有用後端的判斷');
assert.ok(!/hardwareConcurrency/.test(script),
  'num_thread 不能拿瀏覽器這台的核心數 —— 那個參數是套用在跑 Ollama 的機器上');
assert.ok(/gpu/.test(remote.note), '應該講明它讀不到哪一台的核心數');
assert.ok(/clampField\(k\)/.test(script), '欄位變更時沒有夾住上限');
console.log('ok   進階參數說明與上限');

// 工作區改成用選的：路徑輸入框與整個舊對話框都不該還在
assert.ok(!/id="wsOverlay"|id="wsPath"|id="wsApply"/.test(html),
  '舊的工作區對話框還在，路徑應該用 /browse 選');
assert.ok(/apiUrl\('\/browse'\)/.test(script), '沒有走 /browse 選資料夾');
assert.ok(/apiUrl\('\/ls'\)/.test(script), '檔案樹沒有走 /ls');
// 樹一定要 lazy：一次拉整棵會被 node_modules 卡死
assert.ok(/kids\.dataset\.loaded/.test(script), '檔案樹沒有做延遲展開');
// 「修改檔案」搬到輸入框旁邊的按鈕，就不該同時留在功能選單裡
const feats = script.slice(script.indexOf('const FEATURES'),
                           script.indexOf('function autoMenuItem'));
assert.ok(feats.length > 200, '找不到 FEATURES');
assert.ok(!/id: 'write'/.test(feats), '修改檔案同時存在按鈕與選單，會有兩個真相');
assert.ok(/id="writeBtn"/.test(html) && /toggleWrite/.test(script), '沒有修改檔案的按鈕');
console.log('ok   工作區用選的、檔案樹延遲展開');

// 打 / 叫出來的功能表：只認「整段訊息就是一個指令」，句子中間的斜線是內容
const sq = new Function(
  "let val = '';\nconst $ = () => ({ get value() { return val; } });\n" +
  grab('slashQuery') + '\nreturn function (v) { val = v; return slashQuery(); };')();
assert.strictEqual(sq('/comp'), 'comp');
assert.strictEqual(sq('/'), '');
assert.strictEqual(sq('請看 a/b 這個路徑'), null, '句子中間的斜線不該叫出功能表');
assert.strictEqual(sq('/skill 加上說明'), null, '有空白之後就不是在選指令了');
assert.strictEqual(sq('/a/b'), null);
const cmds = new Function(
  script.slice(script.indexOf('const SLASH_CMDS'), script.indexOf('const FONT_MIN')) +
  '\nreturn SLASH_CMDS.map(c => c[0]);')();
assert.ok(cmds.includes('rewind') && cmds.includes('workspace') && cmds.includes('export'),
  '功能表少了東西：' + cmds.join(','));
console.log('ok   斜線功能表');

// 工具卡要收得起來，狀態存在訊息上（重新整理還在）
assert.ok(/m\.folded = !m\.folded/.test(script), '工具卡沒有收折');
assert.ok(/\.tool-card\.fold pre/.test(html), '收折沒有對應的樣式');

// 匯出的 HTML 要能單獨打開：不可以連外部資源
const exp = script.slice(script.indexOf('function exportHtml'), script.indexOf('function exportChat'));
assert.ok(exp.length > 400, '找不到 exportHtml');
assert.ok(!/https?:\/\//.test(exp), '匯出的 HTML 連了外部資源，離線就開不了');
assert.ok(/<!doctype html>/i.test(exp) && /<meta charset="utf-8">/.test(exp),
  '匯出的 HTML 少了 doctype 或編碼宣告');
// 模型靠左、使用者靠右：拿去給別人看的時候要一眼分得出誰說了什麼
const eh = new Function(
  'const esc=(s)=>String(s).replace(/[&<>"]/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'
  + '\'"\':"&quot;"}[c]));const exportMd=(s)=>esc(s);' + exp + '\nreturn exportHtml;')();
const doc = eh({ title: 't', model: 'm', messages: [
  { role: 'user', content: '問題' },
  { role: 'assistant', content: '回答' },
  { role: 'tool', tool_name: 'read_file', args: {}, content: 'x' }] });
assert.ok(/row right[^]*問題/.test(doc), '使用者的訊息不在右邊');
assert.ok(/row left[^]*回答/.test(doc), '模型的回覆不在左邊');
assert.strictEqual((doc.match(/row right/g) || []).length, 1, '只有使用者那則該靠右');
assert.strictEqual((doc.match(/row left/g) || []).length, 2, '模型與工具都該靠左');
console.log('ok   工具卡收折與 HTML 匯出');

// 3. 每個 $('id') 都要在 HTML 裡真的存在（接線打錯字最常見）
const ids = new Set();
const idRe = /id="([^"]+)"/g;
let m;
while ((m = idRe.exec(html)) !== null) ids.add(m[1]);
const missing = [];
const useRe = /\$\('([A-Za-z_][\w-]*)'\)/g;
while ((m = useRe.exec(script)) !== null) if (!ids.has(m[1])) missing.push(m[1]);
assert.deepStrictEqual([...new Set(missing)], [], '這些 id 在 HTML 裡不存在');
console.log('ok   所有 $(id) 都對得上 (' + ids.size + ' 個 id)');

// context 快滿的時候要自動把較早的工具輸出換成一行 —— 連續跑二十幾輪時沒人
// 在看用量條，滿了 Ollama 會靜靜地從前面截掉，症狀是「模型突然忘記在幹嘛」。
const sq2 = new Function(
  "let ratio = 1, limit = 1000;\n" +
  "const S = { ctxRatio: 1, run: {} };\n" +
  "const renderRunBar = () => {};\n" +
  "const ctxLimit = () => limit;\n" +
  grab('estTokens') + '\n' +
  script.slice(script.indexOf('const SQUEEZE_AT'), script.indexOf('function apiMessages')) +
  '\nreturn function (msgs, lim) { limit = lim; const r = squeezeTools(msgs); ' +
  'return { msgs: r, n: S.run.squeezed }; };')();
const big = (n) => 'x'.repeat(n);
const conv = () => [
  { role: 'user', content: '做事' },
  { role: 'tool', tool_name: 'read_file', content: big(4000) },
  { role: 'tool', tool_name: 'search_files', content: big(4000) },
  { role: 'tool', tool_name: 'read_file', content: big(4000) },
  { role: 'tool', tool_name: 'run_tests', content: big(4000) },
  { role: 'assistant', content: '好了' }];
// 還很空的時候一個字都不要動
assert.strictEqual(sq2(conv(), 100000).n, 0, '沒滿就不該動手');
// 滿了才動，而且最後三則工具輸出要留原文
const tight = sq2(conv(), 4000);
assert.ok(tight.n >= 1, '快滿了卻沒有省');
assert.strictEqual(tight.msgs.filter((m) => /已省略|為了留下 context/.test(m.content)).length,
  tight.n, '省略的數量對不上');
assert.strictEqual(tight.msgs[tight.msgs.length - 2].content.length, 4000,
  '最後一則工具輸出被動到了');
assert.strictEqual(tight.msgs[0].content, '做事', '使用者說的話不可以被省略');
assert.strictEqual(tight.msgs[5].content, '好了', '模型的回覆不可以被省略');
console.log('ok   context 快滿時自動省略較早的工具輸出');

// 模型什麼都沒吐出來的時候要說得出為什麼。之前是直接吞掉的：
// 畫面上只多一行 tok/s，然後什麼都沒有，使用者完全不知道發生什麼事。
(function () {
  const mk = (limit, cap) => new Function(`
    const S = { model: 'm', ctxMax: ${JSON.stringify(cap ? { m: cap } : {})} };
    const ctxLimit = () => ${limit};
    ${grab('fmtK')}
    ${grab('emptyReplyNote')}
    return emptyReplyNote;
  `)();
  const n = mk(65536, 0);

  assert.ok(/連線可能中斷/.test(n(null)), '完全沒有 done 時要講連線');
  assert.ok(/num_predict/.test(n({ done_reason: 'length', eval_count: 100 })),
    'length 要指向 num_predict');
  assert.ok(/eval_count = 0/.test(n({ done_reason: 'stop', eval_count: 0 })),
    '一個 token 都沒產生要講出來');
  // prompt 幾乎塞滿 num_ctx —— 這是最常見也最難自己看出來的那一種
  const full = n({ done_reason: 'stop', eval_count: 0, prompt_eval_count: 65000 });
  assert.ok(/num_ctx/.test(full) && /截掉/.test(full), '塞滿 context 要講清楚：' + full);
  // 填得比模型支援的還大，順便提醒
  assert.ok(/支援的還大/.test(mk(262144, 32768)({ done_reason: 'stop', eval_count: 0,
    prompt_eval_count: 10 })), '超過模型上限沒有提醒');
  // 什麼線索都沒有的時候，也要把數字攤出來而不是沉默
  const plain = n({ done_reason: 'stop', eval_count: 5, prompt_eval_count: 100 });
  assert.ok(/停止原因/.test(plain) && /prompt/.test(plain), plain);
  console.log('ok   空回覆會講出原因');
})();

// serve.py 自己的端點要打回「端出這個頁面的那一台」，不能跟著 S.host 走。
// 把 Ollama 指到 GPU 主機之後，/tool 與 /ls 還是本機這一台的事 ——
// 這裡搞混的話，檔案分頁會說「這個頁面不是本機開的」，工具也全部打到 Ollama 去。
(function () {
  const mk = (host, sameOrigin) => new Function(`
    const S = { host: ${JSON.stringify(host)} };
    const SAME_ORIGIN = ${sameOrigin};
    const location = { origin: 'http://localhost:5678' };
    ${script.slice(script.indexOf('const SRV_PATHS'), script.indexOf('function friendlyError'))}
    return { apiUrl: apiUrl, isSrvUrl: isSrvUrl, tab: TAB_ID };
  `)();

  // 把 Ollama 指到別台：serve.py 的端點不可以跟著跑掉
  const both = mk('http://gpu-box:11434', true);
  const remote = both.apiUrl;
  assert.strictEqual(remote('/ls'), 'http://localhost:5678/ls', '/ls 跟著 Ollama 跑掉了');
  assert.strictEqual(remote('/tool'), 'http://localhost:5678/tool');
  assert.strictEqual(remote('/upstream'), 'http://localhost:5678/upstream');
  assert.strictEqual(remote('/api/tags'), 'http://gpu-box:11434/api/tags',
    'Ollama 的路徑才是要跟著 S.host 走的那些');
  assert.strictEqual(remote('/api/chat'), 'http://gpu-box:11434/api/chat');

  // 直接開 HTML 檔（file://）時沒有 serve.py，只能全部走 S.host
  const file = mk('http://localhost:11434', false).apiUrl;
  assert.strictEqual(file('/ls'), 'http://localhost:11434/ls');
  console.log('ok   serve.py 的端點不跟著 Ollama 位址跑');

  // X-Tab 只能掛在 serve.py 自己的端點上。掛到 Ollama 去會逼出 OPTIONS 預檢，
  // 對方接不住就整頁連不上 —— 這比原本要修的 bug 還嚴重。
  assert.ok(both.isSrvUrl('http://localhost:5678/tool'), '/tool 沒有帶上分頁 id');
  assert.ok(both.isSrvUrl('http://localhost:5678/ls?path=a'), 'query string 擋掉了判斷');
  assert.ok(!both.isSrvUrl('http://gpu-box:11434/api/chat'), 'X-Tab 掛到 Ollama 去了');
  assert.ok(!both.isSrvUrl('http://localhost:5678/api/chat'), '同源的 Ollama 路徑也不必掛');
  assert.ok(!mk('x', false).isSrvUrl('http://localhost:5678/tool'), 'file:// 下沒有 serve.py');
  assert.ok(both.tab && both.tab.length > 6, '分頁 id 太短，兩個分頁會撞在一起');
  console.log('ok   分頁 id 只掛在 serve.py 的端點上');
})();

// 允許規則：deny > 危險指令一律問 > allow > 自動模式。
// allow 不能蓋過危險指令 —— 那條保證是寫在文件上的，不能被設定檔悄悄拿掉。
(function () {
  const ap = new Function(
    "const S = { auto: 'off' };\n" + grab('READ_ONLY_TOOLS', 'const') + '\n' +
    grab('autoApprove') +
    "\nreturn function (auto, name, risk, rule) { S.auto = auto; " +
    "return autoApprove(name, risk, rule); };")();
  const allow = { action: 'allow' }, deny = { action: 'deny' }, ask = { action: 'ask' };
  assert.strictEqual(ap('off', 'run_shell', 'ok', allow), true, 'allow 沒有放行');
  assert.strictEqual(ap('off', 'run_shell', 'ok', null), false, '沒有規則就該問');
  assert.strictEqual(ap('full', 'run_shell', 'ok', deny), false, '全自動竟然蓋過 deny');
  assert.strictEqual(ap('off', 'run_shell', 'risky', allow), false,
    'allow 不可以讓危險指令不用問');
  assert.strictEqual(ap('full', 'run_shell', 'risky', allow), false,
    '全自動加 allow 也不行');
  assert.strictEqual(ap('full', 'edit_file', 'ok', ask), false, 'ask 要蓋過全自動');
  console.log('ok   允許規則的順序');
})();

// 從這一次呼叫猜規則：猜的是「同一類」，不是「一模一樣」——
// 每跑一次 pytest 就多一條規則的話，那張清單三天就沒人看得懂了
(function () {
  const sug = new Function(grab('ruleSuggestion') + '\nreturn ruleSuggestion;')();
  assert.deepStrictEqual(sug('run_shell', { command: 'pytest -q tests/' }),
    { tool: 'run_shell', pattern: 'pytest*' });
  assert.deepStrictEqual(sug('run_shell', { command: 'git commit -m "x"' }),
    { tool: 'run_shell', pattern: 'git commit*' }, 'git 要連子指令一起看');
  assert.deepStrictEqual(sug('edit_file', { path: 'src/deep/a.py' }),
    { tool: 'edit_file', pattern: 'src/deep/**' }, '檔案要用整個目錄，不是單一檔案');
  assert.deepStrictEqual(sug('edit_file', { path: 'a.py' }),
    { tool: 'edit_file', pattern: '*' });
  console.log('ok   規則建議');
})();

// @ 是句子中間用的，跟 / 只認整行不一樣
(function () {
  let val = '', caret = null;
  const aq = new Function(
    "let val = '', caret = null;\n" +
    "const $ = () => ({ get value() { return val; }, " +
    "get selectionStart() { return caret === null ? val.length : caret; } });\n" +
    grab('atQuery') +
    "\nreturn function (v, c) { val = v; caret = c === undefined ? null : c; " +
    "return atQuery(); };")();
  assert.strictEqual(aq('@'), '');
  assert.strictEqual(aq('@src/ap'), 'src/ap');
  assert.strictEqual(aq('看一下 @src/app.py'), 'src/app.py', '句子中間也要認');
  assert.strictEqual(aq('看一下 @a.py 這一段'), null, '後面接了空白就不是在選了');
  assert.strictEqual(aq('a@b.com'), null, '信箱不該叫出檔案清單');
  assert.strictEqual(aq('看一下 @a.py 這一段', 9), 'a.py', '游標在中間要看游標前面那一段');
  console.log('ok   @ 提檔案');
})();

// 還原點清單照 VS Code 原始檔控制那一欄排：狀態字母、檔名、灰色目錄、時間，
// 而且新的在最上面 —— 要退回去的時候人想的是「退到剛剛」不是「退到最早」。
(function () {
  const src = script.slice(script.indexOf('function rewindRow'),
                           script.indexOf('async function doRewind'));
  const rows = [];
  const box = new Function(`
    const rows = [];
    const el = () => {
      const e = { className: '', textContent: '', title: '', kids: [], _q: {} };
      Object.defineProperty(e, 'innerHTML', { set(v) {
        (v.match(/class="([a-z]+)"/g) || []).forEach((m) => {
          const k = m.match(/"([a-z]+)"/)[1];
          e._q['.' + k] = { className: k, textContent: '' };
        });
      }, get() { return ''; } });
      e.querySelector = (sel) => e._q[sel] || (e._q[sel] = { textContent: '' });
      e.appendChild = (c) => rows.push(c);
      e.addEventListener = () => {};
      return e;
    };
    const document = { createElement: el };
    const $ = (id) => id === 'rwList'
      ? { set innerHTML(v) {}, get innerHTML() { return ''; }, appendChild: (c) => rows.push(c) }
      : { textContent: '' };
    ${src}
    return { rewindRow, clockOf, dayOf, renderRewind, rows };
  `)();

  assert.strictEqual(box.clockOf('2026-08-25 14:31:46'), '14:31:46', '只顯示時分秒');
  assert.strictEqual(box.dayOf('2026-08-25 14:31:46'), '2026-08-25');

  const mk = (ts, path, created) => ({ ts, path, tool: 'edit_file', created,
                                       id: ts, undo_count: 1, other_chats: 0 });
  box.renderRewind([mk('2020-01-02 09:00:00', 'pkg/a.py', false),
                    mk('2020-01-03 14:31:46', 'pkg/deep/b.py', true)], 2);
  const out = box.rows;
  // 日期分隔 + 新的那一筆先出現
  assert.strictEqual(out[0].className, 'sc-row day', '第一列應該是日期分隔');
  assert.strictEqual(out[0].textContent, '2020-01-03');
  assert.strictEqual(out[1]._q['.nm'].textContent, 'b.py', '新的那一筆要排在最上面');
  assert.strictEqual(out[1]._q['.dir'].textContent, 'pkg/deep', '目錄要跟檔名分開');
  assert.strictEqual(out[1]._q['.st'].textContent, 'A', '新建的檔案要標 A');
  assert.strictEqual(out[1]._q['.tm'].textContent, '14:31:46');
  assert.strictEqual(out[3]._q['.st'].textContent, 'M', '改過的檔案要標 M');
  assert.strictEqual(out[2].textContent, '2020-01-02', '跨天要再插一條分隔');
  // 今天的日期要顯示成「今天」，而且要用本機時間算（toISOString 是 UTC，跨日會差一天）
  const n = new Date();
  const ymd = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0')
    + '-' + String(n.getDate()).padStart(2, '0');
  box.rows.length = 0;
  box.renderRewind([mk(ymd + ' 08:00:00', 'x.py', false)], 1);
  assert.strictEqual(box.rows[0].textContent, '今天', '今天的紀錄要顯示成「今天」');
  console.log('ok   還原點清單（VS Code 原始檔控制的排法）');
})();

// 取樣參數的數字要能直接打：滑桿調得到 0.7 卻調不到 0.75，沒有輸入框就只能認了。
// 打壞的值要還原成現值，超出範圍要夾回去 —— 這兩個沒做的話會送出垃圾參數。
(function () {
  const src = script.slice(script.indexOf('function buildSliders'),
                           script.indexOf('// num_gpu / num_thread'));
  // querySelector 要照 innerHTML 的實際內容與**順序**回答，不能每個選擇器
  // 都給一個新物件 —— 那樣的話「拿錯元素」這種 bug 永遠測不出來
  // （數值那格也是 <input> 而且排在滑桿前面，只寫 'input' 會拿到它）。
  const box = new Function(`
    const S = { params: { temperature: 0.7 } };
    const saveConfig = () => {};
    const mkEl = (tag, cls, type) => ({
      tag, cls, type, value: '', addEventListener(k, f) { this['on' + k] = f; } });
    const parse = (html) => (html.match(/<(input|span)\\b[^>]*>/g) || []).map((t) => mkEl(
      (t.match(/^<(\\w+)/) || [])[1],
      (t.match(/class="([^"]+)"/) || [])[1] || '',
      (t.match(/type="?(\\w+)"?/) || [])[1] || ''));
    const document = { activeElement: null, createElement: () => {
      const e = { className: '', _els: [] };
      e.querySelector = (sel) => {
        const m = sel.match(/^(\\w+)?(?:\\[type=(\\w+)\\])?(?:\\.(\\w+))?$/);
        return e._els.filter((x) => (!m[1] || x.tag === m[1])
          && (!m[2] || x.type === m[2])
          && (!m[3] || (' ' + x.cls + ' ').includes(' ' + m[3] + ' ')))[0] || null;
      };
      Object.defineProperty(e, 'innerHTML',
        { set(v) { e._els = parse(v); }, get() { return ''; } });
      return e; } };
    const $ = () => ({ appendChild() {} });
    const SLIDERS = [{ id: 'temperature', label: 'T', min: 0, max: 2, step: 0.01, dec: 2 }];
    ${src}
    buildSliders();
    const cfg = SLIDERS[0];
    cfg.el.value = 0.7; cfg.sync();
    return { S, el: cfg.el, badge: cfg.badge,
             drag: (v) => { cfg.el.value = v; cfg.el.oninput(); return cfg.badge.value; },
             type: (v) => { cfg.badge.value = v; cfg.badge.onchange(); return cfg.badge.value; },
             blur: (v) => { cfg.badge.value = v; cfg.badge.onblur(); return cfg.badge.value; } };
  `)();
  // 滑桿與數值是兩個不同的元素，而且拖滑桿要同步更新數值
  assert.notStrictEqual(box.el, box.badge, '滑桿與數值格拿到同一個元素了');
  assert.strictEqual(box.el.type, 'range', '拿到的不是滑桿');
  assert.strictEqual(box.drag(1.5), '1.50', '拖滑桿沒有同步更新數字');
  assert.strictEqual(box.S.params.temperature, 1.5, '拖滑桿沒有寫回參數');
  assert.strictEqual(box.type('1.23'), '1.23', '打進去的值沒有生效');
  assert.strictEqual(box.S.params.temperature, 1.23, '打進去的值沒有寫回參數');
  assert.strictEqual(box.type('9'), '2.00', '超過上限沒有夾回去');
  assert.strictEqual(box.type('-5'), '0.00', '低於下限沒有夾回去');
  assert.strictEqual(box.blur('abc'), '0.00', '打壞了要還原成現值，不能留一個看不懂的狀態');
  console.log('ok   取樣參數可以直接輸入數字');
})();

// 連不上就自動重試。但**只在一個字都還沒收到時** —— 已經吐出東西再重試會重複一段。
(function () {
  const can = new Function(grab('isRetryable') + '\nreturn isRetryable;')();
  const net = new Error('Failed to fetch');

  // 該重試的
  assert.ok(can(net, false), '連不上要重試');
  assert.ok(can(new Error('NetworkError when attempting to fetch'), false));
  assert.ok(can(new Error('HTTP 503：service unavailable'), false));
  assert.ok(can(new Error('HTTP 502'), false));
  assert.ok(can(new Error('HTTP 429'), false), '被限流也該等一下再試');

  // 不該重試的
  assert.ok(!can(net, true), '已經吐出字了還重試會重複一段');
  assert.ok(!can(Object.assign(new Error('x'), { name: 'AbortError' }), false),
    '使用者按停止不是失敗');
  assert.ok(!can(new Error('HTTP 404：model not found'), false), '模型名打錯重試幾次都一樣');
  assert.ok(!can(new Error('HTTP 400：invalid options'), false));
  assert.ok(!can(new Error('看不懂的錯'), false), '認不出來的就不要亂重試');

  const n = new Function(grab('RETRY_MAX', 'const') + '\nreturn RETRY_MAX;')();
  assert.ok(n >= 2 && n <= 10, '重試次數不合理：' + n);
  console.log('ok   連不上會自動重試，吐過字就不重試');
})();

// 一輪跑完要留下「這一輪花了多久」。per-message 的統計看不出這個 ——
// 那裡寫的是「這一次呼叫模型花了幾秒」，一輪十幾次呼叫加上工具執行時間，
// 加起來是完全不同的數字。
(function () {
  const line = new Function(grab('fmtElapsed') + '\n' + grab('fmtTokens') + '\n'
    + grab('turnLine') + '\nreturn turnLine;')();
  assert.strictEqual(line(null), '');
  const out = line({ ms: 152000, rounds: 11, calls: 12, tokens: 59800 });
  assert.ok(out.indexOf('2 分 32 秒') >= 0, out);
  assert.ok(out.indexOf('11 輪') >= 0 && out.indexOf('12 次工具') >= 0, out);

  // 只在跑過工具時記。純聊天那一則的統計本來就寫了總計幾秒，再加一行是重複的。
  const src = script.slice(script.indexOf('function markTurnDone'),
                           script.indexOf('function markTurnDone') + 900);
  assert.ok(/!r\.rounds\) return/.test(src), '沒有跑過工具也記了：' + src.slice(0, 200));
  assert.ok(/r\.t0 = 0/.test(src), '沒有把 t0 清掉，同一輪會記很多次');
  // 額外欄位不能漏進送給模型的 payload
  const api = script.slice(script.indexOf('function apiMessages'),
                           script.indexOf('function apiMessages') + 700);
  assert.ok(!/item\.turn|m\.turn/.test(api),   // 別用 /turn/：return 也會命中
    'turn 欄位跑進 apiMessages 了，那會污染 context');
  assert.ok(/const item = \{ role: m\.role, content: m\.content \}/.test(api),
    'apiMessages 不再是白名單欄位了 —— 那樣掛在訊息上的任何東西都會漏給模型');
  console.log('ok   一輪跑完留下總計時間');
})();

// 選資料夾的路徑框要顯示「現在在哪一層」。
// 原本寫的是 $('brPath').textContent = data.path —— 那是 <input>，設 textContent
// 完全沒作用，所以框裡一直是空的，只看得到反灰的 placeholder。
(function () {
  const src = script.slice(script.indexOf('async function browseTo'),
                           script.indexOf('function openBrowser'));
  assert.ok(src.length > 200, '切不到 browseTo');
  assert.ok(/\$\('brPath'\)\.value = data\.path/.test(src),
    'brPath 又變成用 textContent 了，那對 <input> 沒有作用');
  assert.ok(!/\$\('brPath'\)\.textContent/.test(src), src.slice(0, 200));
  console.log('ok   選資料夾時看得到目前的絕對路徑');
})();

// `@資料夾/` 一次附整個目錄。一定要有上限：一個 node_modules 就足以把
// context 灌爆，而且是在使用者按下 Enter 之後才發現。
(function () {
  const d = new Function(grab('dirsOf') + '\nreturn dirsOf;')();
  const u = new Function(grab('filesUnder') + '\nreturn filesUnder;')();
  const files = ['a.py', 'src/app.ts', 'src/lib/util.ts', 'tests/test_a.py'];

  assert.deepStrictEqual(d(files), ['src/', 'src/lib/', 'tests/'],
    '要推出所有層級的資料夾，而且結尾要有斜線（不然跟同名檔案分不出來）');
  assert.deepStrictEqual(d([]), []);
  assert.deepStrictEqual(d(['onlyfile.py']), [], '沒有資料夾就不要生出一個');

  assert.deepStrictEqual(u(files, 'src/'), ['src/app.ts', 'src/lib/util.ts'],
    '子資料夾底下的也要算進去');
  assert.deepStrictEqual(u(files, 'src/lib/'), ['src/lib/util.ts']);
  assert.deepStrictEqual(u(files, 'nope/'), []);

  // 上限一定要存在，而且要是有限的數字
  const cap = new Function(grab('AT_DIR_FILES', 'const') + '\n'
    + grab('AT_DIR_CHARS', 'const') + '\nreturn [AT_DIR_FILES, AT_DIR_CHARS];')();
  assert.ok(cap[0] > 0 && cap[0] < 1000, '檔案數上限不合理：' + cap[0]);
  assert.ok(cap[1] > 0 && cap[1] < 2000000, '字數上限不合理：' + cap[1]);
  console.log('ok   @資料夾/ 一次附整包，而且有上限');
})();

// 模型寫出來的 `wafer_counter.py:42` 要能點。只認工作區裡真的存在的檔案 ——
// 不驗的話 http://host:8080 與 example.com:443 都會被當成「檔案:行號」。
(function () {
  const t = new Function('S', grab('FILE_REF_RE', 'const') + '\n'
    + grab('fileRefTarget') + '\nreturn fileRefTarget;');
  const files = ['wafer_counter.py', 'src/app.ts', 'a/util.py', 'b/util.py'];
  const hit = t({ atFiles: files });

  assert.strictEqual(hit('wafer_counter.py'), 'wafer_counter.py');
  assert.strictEqual(hit('src/app.ts'), 'src/app.ts');
  // 只寫檔名而不寫路徑：唯一一個同名檔才敢連過去
  assert.strictEqual(hit('app.ts'), 'src/app.ts', '唯一的同名檔應該連得到');
  assert.strictEqual(hit('util.py'), '', '兩個同名檔就不要亂猜');
  // 不存在的一律不連
  assert.strictEqual(hit('nope.py'), '');
  assert.strictEqual(hit('example.com'), '', '網域不是檔案');
  assert.strictEqual(hit('host'), '');
  // 沒有檔案清單時什麼都不連（不能因為清單還沒載入就亂標）
  assert.strictEqual(t({ atFiles: [] })('wafer_counter.py'), '');
  assert.strictEqual(t({})('wafer_counter.py'), '');

  // 正規表示式本身：要抓得到行號，也要能接受沒有行號
  const re = new Function(grab('FILE_REF_RE', 'const') + '\nreturn FILE_REF_RE;')();
  const grabAll = (s) => { re.lastIndex = 0; const o = []; let m;
    while ((m = re.exec(s)) !== null) o.push([m[1], m[2]]); return o; };
  assert.deepStrictEqual(grabAll('見 wafer_counter.py:42 那一行'), [['wafer_counter.py', '42']]);
  assert.deepStrictEqual(grabAll('改了 src/app.ts'), [['src/app.ts', undefined]]);
  // 這些會被抓出來，但 fileRefTarget 會把它們擋掉 —— 兩道一起才安全
  assert.ok(grabAll('http://example.com:8080').length, '正規表示式本來就會誤抓，所以一定要驗檔案清單');
  console.log('ok   檔案:行號 只連工作區裡真的有的檔案');
})();

// 跑的時候要看得出「跑多久」與「在幹嘛」。原本只有輪數與 token 數 ——
// 一支跑三分鐘的指令中間完全沒有動靜，看起來就像當掉了。
(function () {
  const f = new Function(grab('fmtElapsed') + '\nreturn fmtElapsed;')();
  assert.strictEqual(f(0), '0 秒');
  assert.strictEqual(f(1500), '2 秒');
  assert.strictEqual(f(59000), '59 秒');
  assert.strictEqual(f(60000), '1 分 0 秒');
  assert.strictEqual(f(152000), '2 分 32 秒');
  assert.strictEqual(f(3600000), '1 小時 0 分');
  assert.strictEqual(f(-5), '0 秒', '時鐘倒退不能印出負數');

  const cur = new Function(grab('currentTodo') + '\nreturn currentTodo;')();
  assert.strictEqual(cur([]), '');
  assert.strictEqual(cur(null), '');
  assert.strictEqual(cur([{ text: 'a', done: true }]), '', '全做完就不要再說「現在」');
  assert.strictEqual(cur([{ text: 'a', done: true }, { text: 'b' }, { text: 'c' }]), 'b',
    '要挑第一個還沒完成的，不是最後一個');
  console.log('ok   跑的時候看得出跑多久與在幹嘛');
})();

// 跑到一半使用者又打字：不中斷，排隊，下一次送模型時夾帶過去。
// 這是 Claude Code 的做法（queuedMessages + "The user sent a new message while
// you were working:"）。另外兩條路都比較差：鎖住輸入框等於長任務只能乾等；
// 打字就立刻中斷會浪費正在跑的工具。
(function () {
  const box = new Function('S', 'log', `
    const $ = (id) => ({ hidden: true, textContent: '', value: '',
                         set _(v) {} , focus(){} });
    const bars = {};
    const el = (id) => (bars[id] = bars[id] || { hidden: true, textContent: '' });
    const toast = (t) => log.push(['toast', t]);
    const saveChats = () => {};
    const buildUserMsg = (m) => ({ m: m });
    const pin = () => {};
    const thread = { appendChild: (x) => log.push(['render', x.m.text]) };
    `
    + grab('queueMessage').replace("renderQueue();", "")
                          .replace("$('queueBar')", "el('queueBar')") + `
    ` + grab('flushQueue').replace("renderQueue();", "")
                          .replace("$('thread')", "thread") + `
    return { queue: queueMessage, flush: flushQueue };`);

  // 一、有排隊的話才會夾帶
  {
    const S = { queued: [] }, log = [];
    const api = box(S, log);
    const c = { messages: [] };
    assert.strictEqual(api.flush(c), false, '沒有排隊卻說有');
    assert.strictEqual(c.messages.length, 0);
  }
  // 二、排一則 → 夾帶進對話，而且清空佇列（不能送第二次）
  {
    const S = { queued: [] }, log = [];
    const api = box(S, log);
    const c = { messages: [] };
    api.queue('順便也跑一下 lint');
    assert.deepStrictEqual(S.queued, ['順便也跑一下 lint']);
    assert.ok(log.some(function (x) { return x[0] === 'toast'; }), '排隊要告訴使用者');

    assert.strictEqual(api.flush(c), true);
    assert.strictEqual(c.messages.length, 1);
    const m = c.messages[0];
    assert.strictEqual(m.role, 'user', '插話要用 user 角色，模型才會當成使用者說的');
    assert.strictEqual(m.content, '順便也跑一下 lint', '送給模型的是原文，不帶前綴');
    assert.ok(m.text.indexOf('跑到一半補充') >= 0, '畫面上要看得出那不是正常送出的：' + m.text);
    assert.ok(m.queued, '沒有標記成插話，畫面就分不出來');
    assert.deepStrictEqual(S.queued, [], '夾帶完沒有清掉佇列，會送第二次');
    assert.strictEqual(api.flush(c), false, '清掉之後不該再送');
  }
  // 三、排好幾則要併成一則，不是變成好幾個 user 訊息
  {
    const S = { queued: [] }, log = [];
    const api = box(S, log);
    const c = { messages: [] };
    api.queue('第一句'); api.queue('第二句');
    api.flush(c);
    assert.strictEqual(c.messages.length, 1, '兩則插話變成兩個訊息了');
    assert.strictEqual(c.messages[0].content, '第一句\n第二句');
  }
  // 四、沒有對話時不能爆掉
  {
    const S = { queued: ['x'] }, log = [];
    assert.strictEqual(box(S, log).flush(null), false);
  }
  console.log('ok   跑到一半打字會排隊，下一輪夾帶過去');
})();

// 模型問問題時，點選項只是「選起來」，要按送出才算數。
// 原本是點下去就當作答案 —— 一個誤觸就替使用者做了決定，而且沒有回頭路：
// 那則回答已經進到 context 裡了。選項也常常很長，滑過去點錯不難。
(function () {
  const src = script.slice(script.indexOf('function askUser(args) {'),
                           script.indexOf('function confirmTool'));
  assert.ok(src.length > 200, '切不到 askUser');

  // 選項按鈕的 click 只能呼叫 pick，不能直接 done
  const loop = src.slice(src.indexOf("(args.options || [])"), src.indexOf("opts.hidden"));
  assert.ok(/pick\(o, b\)/.test(loop), '選項按鈕沒有接到 pick');
  assert.ok(!/done\(/.test(loop), '選項按鈕又變成點下去就直接送出了：' + loop.slice(0, 160));

  // 送出鍵要存在、預設是停用的、而且接得到 done
  assert.ok(/data-send disabled/.test(src), '送出鍵一開始應該是停用的');
  assert.ok(/send\.addEventListener\('click'[^)]*\)[^;]*done\(/.test(src.replace(/\n/g, ' ')),
    '送出鍵沒有接上 done');
  // 有東西可送才可以按
  assert.ok(/send\.disabled = !answer\(\)/.test(src), '送出鍵沒有跟著有沒有答案開關');
  // 選項與自己打字是二選一，不能兩邊都留著讓人猜送出哪個
  assert.ok(/if \(picked\) input\.value = ''/.test(src), '選了選項沒有清掉輸入框');
  console.log('ok   問問題的選項要按送出才算數');
})();

// 中斷之後可以續跑。能續是因為中斷的東西都留在對話裡：finishStream 會把
// 已經吐出來的半截內容寫進 c.messages，工具結果本來就一則一則存著。
// 所以「繼續」就是拿同一份 messages 再送一次。
(function () {
  const why = new Function('S', 'c', grab('resumeReason')
    + '\nreturn resumeReason(c);');
  const idle = { streaming: false };

  // 被停下來的半截回覆 → 可以續
  assert.ok(why(idle, { messages: [{ role: 'assistant', content: '算到一半', stats: '（已停止）' }] }));
  // 連一個字都還沒吐就被停 → 也可以續，但說法不一樣
  const blank = why(idle, { messages: [{ role: 'assistant', content: '', stats: '（已停止）' }] });
  assert.ok(blank && blank.indexOf('還沒開始寫') >= 0, blank);
  // 工具跑完就斷了，模型還沒接話 → 這是 agent 迴圈中斷最常見的樣子
  assert.ok(why(idle, { messages: [{ role: 'tool', content: '5 passed' }] }));

  // 正常講完的不要給「繼續」—— 那會變成叫模型無故多講一段
  assert.strictEqual(why(idle, { messages: [
    { role: 'assistant', content: '做完了', stats: '120 tokens · 3.2s' }] }), '');
  // 正在跑的時候不給
  assert.strictEqual(why({ streaming: true }, { messages: [
    { role: 'assistant', content: 'x', stats: '（已停止）' }] }), '');
  // 空對話不給
  assert.strictEqual(why(idle, { messages: [] }), '');
  assert.strictEqual(why(idle, null), '');
  // 使用者訊息結尾不給 —— 那是還沒送出，不是中斷
  assert.strictEqual(why(idle, { messages: [{ role: 'user', content: '幫我算' }] }), '');
  console.log('ok   中斷之後可以續跑');
})();

// 重開之後把 serve.py 那一端的狀態接回來。
// 工作區與四個開關都是 serve.py 的行程全域，重啟就回預設；自動模式卻存在
// localStorage 裡。不接回來的症狀是「自動模式顯示全自動，但模型改不動檔案」。
(async function () {
  // restoreServerState 是 async，new Function 造出來的是同步函式，裝不下 await
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  function run(conf, server) {
    const calls = [];
    const box = new AsyncFunction('log', 'conf', 'server', `
      const S = { auto: conf.auto || 'off',
                  ws: { path: server.wsPath || '', write: !!server.write },
                  srv: { tools: !!server.tools, browser: !!server.browser,
                         sandbox: !!server.sandbox, toolsLocal: true } };
      const current = () => server.chatWs !== undefined ? { ws: server.chatWs } : null;
      const applyWorkspace = async (p) => { log.push(['ws', p]); S.ws.path = p; };
      const setServerTools = async (patch) => { log.push(['tools', patch]); };
      const toast = (t) => log.push(['toast', t]);
      const renderWriteBtn = () => {}, renderFeatBtn = () => {}, renderWorkspace = () => {};
      // grab() 是從 function 這個字開始切的，async 前綴會被丟掉 —— 補回去，
      // 不然抓回來的是同步函式，裡面的 await 直接 SyntaxError
      ` + 'async ' + grab('restoreServerState') + `
      return restoreServerState(conf);`);
    return box(calls, conf, server).then(() => calls);
  }

  {
    // serve.py 重啟就回到 off，瀏覽器記得的那一檔要推回去，
    // 不然系統提示會停在「每一次都會問你」而實際上沒有人在按
    const calls = await run(
      { wsPath: '/w', auto: 'ws', srv: { tools: true, write: true, browser: false, sandbox: false } },
      { wsPath: '/w', tools: true, write: true, browser: false, sandbox: false });
    const patch = (calls.filter(function (c) { return c[0] === 'tools'; })[0] || [])[1];
    assert.deepStrictEqual(patch, { auto: 'ws' }, '自動模式沒有推回去：' + JSON.stringify(calls));
    // 但那不是「伺服器的開關」，不該出現在「接回上次的設定」那行 toast 裡
    const said = calls.filter(function (c) { return c[0] === 'toast'; }).map(function (c) { return c[1]; });
    assert.ok(!said.some(function (t) { return /自動模式/.test(t); }),
      '自動模式是瀏覽器的設定，不必再報一次：' + said.join('|'));
  }

  // 一、什麼都沒變就什麼都不做（不要每次開頁面都跳一個 toast）
  {
    const calls = await run(
      { wsPath: '/w', srv: { tools: true, write: true, browser: false, sandbox: false } },
      { wsPath: '/w', tools: true, write: true, browser: false, sandbox: false });
    assert.deepStrictEqual(calls, [], '狀態一樣卻還是動了：' + JSON.stringify(calls));
  }

  // 二、serve.py 重啟後的實況：工作區沒了、開關全關，但瀏覽器記得
  {
    const calls = await run(
      { wsPath: '/w', srv: { tools: true, write: true, browser: false, sandbox: true } },
      { wsPath: '', tools: false, write: false, browser: false, sandbox: false });
    const kinds = calls.map(function (c) { return c[0]; });
    assert.strictEqual(kinds[0], 'ws',
      '工作區一定要先設 —— serve.py 的 ALLOW_WRITE 有 and WORKSPACE is not None，'
      + '沒有工作區的話 write:true 會被靜靜吃掉');
    assert.strictEqual(calls[0][1], '/w');
    const patch = calls.filter(function (c) { return c[0] === 'tools'; })[0][1];
    assert.deepStrictEqual(patch, { enabled: true, write: true, sandbox: true },
      'browser 兩邊都是 false，不該出現在 patch 裡：' + JSON.stringify(patch));
    const said = calls.filter(function (c) { return c[0] === 'toast'; });
    assert.strictEqual(said.length, 1, '接回設定一定要說出來，靜靜打開「修改檔案」不行');
    assert.ok(said[0][1].indexOf('修改檔案') >= 0, said[0][1]);
  }

  // 三、對話自己的工作區優先於全域最後一次
  {
    const calls = await run({ wsPath: '/global', srv: {} }, { wsPath: '', chatWs: '/from-chat' });
    assert.deepStrictEqual(calls[0], ['ws', '/from-chat'], JSON.stringify(calls));
  }
  console.log('ok   重開之後接回 serve.py 那一端的狀態');
})();

// 收工前的檢查：模型說「做完了」的那一刻要能被攔一次。
// 對照 Claude Code 的 Stop hook（回傳 additionalContext 就讓對話繼續）。
// 條件寫死成「寫了測試卻沒跑過」—— 「改了程式就要跑測試」在沒有測試的專案上
// 永遠是誤報，而會誤報的自動提醒最後一定會被使用者關掉。
(function () {
  const box = new Function(
    grab('TEST_FILE_RE', 'const') + '\n' + grab('TEST_CMD_RE', 'const') + '\n'
    + grab('looksLikeTestFile') + '\n' + grab('looksLikeTestRun') + '\n'
    + grab('finishCheck') + `
    return { file: looksLikeTestFile, run: looksLikeTestRun, check: finishCheck };`)();

  // 認得出來的測試檔
  ['test_wafer.py', 'src/test_a.py', 'wafer_test.py', 'main_test.go',
   'ui.test.js', 'ui.spec.tsx', 'tests/anything.py', 'test/foo.rb'].forEach(function (p) {
    assert.ok(box.file(p), p + ' 應該算測試檔');
  });
  // 不能誤判成測試檔 —— 誤判會讓每次寫檔都被嘮叨一次
  ['wafer_counter.py', 'src/latest.py', 'contest.py', 'protest.js',
   'README.md', 'attestation.py'].forEach(function (p) {
    assert.ok(!box.file(p), p + ' 不該算測試檔');
  });

  assert.ok(box.run('run_tests', {}));
  assert.ok(box.run('run_shell', { command: 'python -m pytest -q' }));
  assert.ok(box.run('run_shell', { command: 'npm test' }));
  assert.ok(box.run('run_shell', { command: 'cargo test --all' }));
  assert.ok(!box.run('run_shell', { command: 'python wafer_counter.py' }));
  assert.ok(!box.run('write_file', { path: 'test_a.py' }), '寫測試檔不等於跑測試');

  // 只有「寫了測試又沒跑」才攔
  assert.strictEqual(box.check({ wroteTests: '', ranTests: false, nagged: false }), '');
  assert.strictEqual(box.check({ wroteTests: 'test_a.py', ranTests: true, nagged: false }), '');
  const nag = box.check({ wroteTests: 'test_a.py', ranTests: false, nagged: false });
  assert.ok(nag && nag.indexOf('test_a.py') >= 0, '要講出是哪個檔案：' + nag);
  assert.ok(nag.indexOf('run_tests') >= 0, '要講出該用哪支工具');
  // 一輪只攔一次：模型堅持不跑的話，攔第二次就是無限迴圈
  assert.strictEqual(box.check({ wroteTests: 'test_a.py', ranTests: false, nagged: true }), '');
  console.log('ok   收工前的檢查（寫了測試沒跑）');
})();

// 同一個呼叫連續失敗就不要再送。自動模式下沒有人在按確認，
// 模型用一樣的參數重試會把 25 輪燒完，而畫面看起來它「還在做事」。
(function () {
  const src = script.slice(script.indexOf('const REPEAT_LIMIT'),
                           script.indexOf('/* ══════════════════════ 子代理'));
  const box = new Function(`
    const S = { run: { calls: 0, fails: {} }, streamTools: [], todos: [] };
    let nextOk = false;
    const sent = [];
    const toolPreview = async () => ({ diff: '', risk: 'ok' });
    const autoApprove = () => true;
    const confirmTool = async () => true;
    const renderRunBar = () => {};
    const renderTodos = () => {};
    const runStreamed = async () => '';
    const noteFinishSignals = () => {};
    const READ_ONLY_TOOLS = ['read_file'];
    const touchTree = () => {};
    const apiUrl = (p) => p;
    const fetch = async (u, o) => {
      sent.push(JSON.parse(o.body).name);
      return { ok: nextOk, json: async () => nextOk ? { result: '好了' } : { error: '壞了' } };
    };
    ${src}
    return { S, sent, execTool, callKey, ok: (v) => { nextOk = v; } };
  `)();

  const call = () => box.execTool('read_file', { path: 'a.py' },
    { role: 'tool', tool_name: 'read_file', content: '' });

  Promise.resolve().then(async () => {
    const a = await call();
    const b = await call();
    assert.ok(a.failed && b.failed, '前兩次應該真的送出去並失敗');
    assert.strictEqual(box.sent.length, 2, '前兩次都要真的試');

    const c = await call();
    assert.ok(c.blocked, '第三次應該被擋下來');
    assert.strictEqual(box.sent.length, 2, '第三次不該送出去');
    assert.ok(/換個做法|沒有再跑/.test(c.content), '要告訴模型換做法：' + c.content);

    // 換參數就是另一個呼叫，不受影響
    box.ok(true);
    const d = await box.execTool('read_file', { path: 'b.py' },
      { role: 'tool', tool_name: 'read_file', content: '' });
    assert.ok(!d.blocked && !d.failed, '換了參數不該被擋');
    // 成功過就重新計算
    assert.strictEqual(box.S.run.fails[box.callKey('read_file', { path: 'b.py' })], undefined);
    console.log('ok   重複失敗的呼叫會被擋下來');
  });
})();

// 子代理：自己的 context，只有結論回到主對話。跑得起來、不會遞迴、輪數用完會停、
// 停止鍵停得住 —— 這四件事錯了都會變成無限迴圈或停不下來的背景任務。
(async function () {
  const src = script.slice(script.indexOf('const SUB_ROUNDS'),
                           script.indexOf('async function runTools'));
  const mk = (replies, aborted) => new Function(`
    const S = { model: 'm', srv: {}, run: { calls: 0 }, streamTools: [], toolDefs: [],
                abort: { signal: { aborted: ${!!aborted} } } };
    let turn = 0;
    const calls = [];
    const started = [];
    // 子代理改走 chatStream：外部 API 那條路才跟著能用
    const chatStream = async (payload, signal, on) => {
      started.push(payload.tools.map((d) => d.function.name).join(','));
      const r = ${JSON.stringify(replies)}[turn++] || {};
      if (r.content) on.content(r.content);
      if (r.tools) on.tools(r.tools);
      on.done({});
    };
    const execTool = async (n, a, msg) => { calls.push(n); msg.content = 'OK:' + n; return msg; };
    const READ_ONLY_TOOLS = ['read_file', 'search_files'];
    const toolDefs = () => ['read_file', 'search_files', 'write_file', 'run_shell',
                            'task', 'ask_user_question', 'todo_write']
      .map((n) => ({ function: { name: n } }));
    const agentRules = () => 'rules';
    const buildOptions = () => ({});
    const thinkValue = () => null;
    const msgEl = () => {
      const el = { cls: [], html: '' };
      el.querySelector = () => ({ textContent: '', classList: { add() {} } });
      Object.defineProperty(el, 'innerHTML', { set(v) { el.html = v; }, get() { return el.html; } });
      return el;
    };
    const ico = () => '';
    const $ = () => ({ appendChild() {} });
    const pin = () => {};
    ${src}
    return { runSubagent, subTools, subKind, startSubagents, callArgs, calls, started,
             rounds: SUB_ROUNDS };
  `)();

  // 唯讀子代理只能拿到唯讀工具。全自動之後沒有人在看子代理做什麼 ——
  // 交辦「找出所有用到 X 的地方」不需要寫檔案的權限，那就不要給。
  const box = mk([]);
  assert.deepStrictEqual(box.subTools('explore').map((d) => d.function.name),
    ['read_file', 'search_files'], '唯讀子代理拿到了會改東西的工具');
  assert.deepStrictEqual(box.subTools('work').map((d) => d.function.name),
    ['read_file', 'search_files', 'write_file', 'run_shell'], 'work 少了該有的工具');
  // task 會遞迴、ask_user_question 沒人看得懂上下文、
  // todo_write 會把主代理那條線的待辦蓋掉 —— 兩種都不能給
  ['explore', 'work'].forEach(function (k) {
    const n = box.subTools(k).map((d) => d.function.name);
    ['task', 'ask_user_question', 'todo_write'].forEach(function (bad) {
      assert.ok(n.indexOf(bad) < 0, k + ' 拿到了不該有的 ' + bad);
    });
  });
  assert.strictEqual(box.subKind({}), 'explore', '沒指定要當唯讀，不是預設放權限');
  assert.strictEqual(box.subKind({ type: 'work' }), 'work');
  assert.strictEqual(box.subKind({ type: '亂寫' }), 'explore', '認不得的值要退回唯讀');

  // 正常流程：呼叫一支工具 → 給結論
  const ok = mk([
    { tools: [{ function: { name: 'read_file', arguments: '{"path":"a"}' } }] },
    { content: '結論：a.py 第 3 行' }]);
  assert.strictEqual(await ok.runSubagent({ prompt: '看一下 a.py' }), '結論：a.py 第 3 行');
  assert.deepStrictEqual(ok.calls, ['read_file']);

  // 一直呼叫工具、永遠不給結論 → 要在輪數用完時停下來
  const loop = mk(Array(200).fill(
    { tools: [{ function: { name: 'read_file', arguments: '{}' } }] }));
  const out = await loop.runSubagent({ prompt: '無限迴圈' });
  assert.ok(/輪還沒有結論/.test(out), '輪數用完沒有停：' + out);
  assert.strictEqual(loop.calls.length, loop.rounds, '應該剛好跑 SUB_ROUNDS 輪');

  // 停止鍵要停得住。原本子代理自己開 AbortController，按停止只停得了主迴圈，
  // 子代理會一路跑到輪數用完 —— 全自動之後那可能是好幾分鐘。
  const stop = mk(Array(5).fill(
    { tools: [{ function: { name: 'read_file', arguments: '{}' } }] }), true);
  assert.ok(/停止/.test(await stop.runSubagent({ prompt: '停我' })), '停止鍵停不住子代理');
  assert.strictEqual(stop.calls.length, 0, '按了停止還在呼叫工具');

  // 沒給 prompt 就不要開一個什麼都不知道的子代理
  assert.ok(/錯誤/.test(await mk([]).runSubagent({})));

  // 同一輪好幾個 explore 要一起發。每個子代理是一整條獨立的模型迴圈，
  // 平行省的是真正的牆鐘時間 —— 這跟平行跑 read_file 不是同一回事。
  const task = (t) => ({ function: { name: 'task', arguments: JSON.stringify(t) } });
  const par = mk([{ content: 'done' }]);
  assert.deepStrictEqual(Object.keys(par.startSubagents(
    [task({ prompt: 'a' }), task({ prompt: 'b' }), { function: { name: 'read_file' } }])),
    ['0', '1'], '兩個 explore 沒有一起發');
  assert.deepStrictEqual(Object.keys(par.startSubagents([task({ prompt: 'a' })])), [],
    '只有一個就不必先發，維持原本的順序');
  // 兩個會寫檔案的子代理同時動同一個檔案，收拾起來比省下的時間貴
  assert.deepStrictEqual(Object.keys(par.startSubagents(
    [task({ prompt: 'a', type: 'work' }), task({ prompt: 'b', type: 'work' })])), [],
    'work 型的子代理不可以平行跑');
  console.log('ok   子代理：唯讀預設、停得住、輪數有底、explore 平行跑');
})();

// 4. 開關：要驗證伺服器真的照做，還有全自動與修改檔案的連動（都得 await）
(async function () {
  const src = script.slice(script.indexOf('const FEATURES'), script.indexOf('function autoMenuItem'))
    + script.slice(script.indexOf('function autoMenuItem'), script.indexOf('function renderFeatBtn'))
    + script.slice(script.indexOf('const SW_KEY'), script.indexOf('/* ══════════════════════ 串流執行'))
    + grab('writeReason') + grab('openFeatureMenu');

  // srvKeys＝這個版本的 serve.py 認得哪些開關（舊版沒有 browser）
  const mk = (srvKeys, ws) => new Function(`
    const S = { provider: 'ollama', model: 'm', caps: { m: ['tools'] }, auto: 'off', tools: true,
      srv: { tools: true, toolsLocal: true, browser: false },
      ws: Object.assign({ path: '/w', write: false }, ${JSON.stringify(ws || {})}), plan: false };
    const SRV = ${JSON.stringify(srvKeys)};
    const NAME = { enabled: 'tools' };
    const apiUrl = (x) => x;
    const fetch = async (u, o) => {
      const q = JSON.parse(o.body);
      for (const k in q) { const t = NAME[k] || k; if (t in SRV) SRV[t] = !!q[k]; }
      return { ok: true, json: async () => Object.assign({}, SRV) };
    };
    const toasts = [];
    const toast = (t) => toasts.push(t);
    let menu = null;
    const showMenu = (a, rows) => { menu = rows; };
    const $ = () => ({ classList: { toggle() {} } });
    const renderWriteBtn = () => {};
    const renderFeatBtn = () => {};
    const saveConfig = () => {};
    const toolsReason = () => '';
    const autoLabel = () => 'x';
    const openRules = () => {};
    const AUTO_MODES = [['off', '每一次都問', ''], ['read', '唯讀自動', ''], ['full', '全自動', '']];
    ${src}
    return { S: S, SRV: SRV, toasts: toasts, setServerTools: setServerTools,
             openFeatureMenu: openFeatureMenu, menu: () => menu };
  `)();

  // 舊版 serve.py 收到不認得的鍵會靜靜忽略 —— 畫面會永遠停在「關閉」且沒有錯誤。
  // 連網瀏覽真的踩過這一次（頁面每次重組是新的，Python 還是舊的那個 process）。
  const oldSrv = mk({ tools: true, write: false, plan: false });
  await assert.rejects(() => oldSrv.setServerTools({ browser: true }), /重新啟動 serve\.py/,
    '舊版沒有的開關要講出來，不能默默失敗');
  const newSrv = mk({ tools: true, write: false, plan: false, browser: false });
  await newSrv.setServerTools({ browser: true });
  assert.strictEqual(newSrv.S.srv.browser, true, '連網瀏覽開不起來');
  console.log('ok   開關會確認伺服器有照做');

  // 工作區＝右側的檔案分頁，選單裡不該再重複一份
  newSrv.openFeatureMenu();
  assert.ok(!newSrv.menu().some((r) => r.label && r.label.indexOf('工作區') >= 0),
    '功能選單裡還留著工作區');

  // 全自動＝不再一個個問；但能不能改檔案是另一道開關，兩個要連動
  const auto = mk({ tools: true, write: false, plan: false, browser: false });
  auto.openFeatureMenu();
  auto.menu().filter((r) => /自動模式/.test(r.label || ''))[0].action();
  await auto.menu().filter((r) => r.label === '全自動')[0].action();
  assert.strictEqual(auto.SRV.write, true, '全自動沒有一併開啟修改檔案');
  assert.strictEqual(auto.S.auto, 'full');

  // 沒有工作區就開不了，這時要說原因，不是假裝開好了
  const noWs = mk({ tools: true, write: false, plan: false, browser: false }, { path: '' });
  noWs.openFeatureMenu();
  noWs.menu().filter((r) => /自動模式/.test(r.label || ''))[0].action();
  await noWs.menu().filter((r) => r.label === '全自動')[0].action();
  assert.strictEqual(noWs.SRV.write, false);
  assert.ok(/工作區/.test(noWs.toasts.join('')), '沒講為什麼改不了檔案：' + noWs.toasts.join('|'));
  console.log('ok   全自動連動修改檔案');

  // ── 長時間自動執行 ────────────────────────────────────────────
  // 這一組全部是「放著讓它跑三十分鐘」才會撞到的東西。

  const longRun = new Function(`
    ${grab('MAX_TOOL_ROUNDS', 'const')}
    ${grab('ROUNDS_WARN', 'const')}
    ${grab('OA_TOKEN_BUDGET', 'const')}
    ${grab('fmtElapsed')}
    ${grab('fmtTokens')}
    ${grab('roundsNote')}
    ${grab('budgetStop')}
    const S = { provider: 'ollama' };
    const performance = { now: () => nowMs };
    let nowMs = 0;
    return { roundsNote, budgetStop, MAX_TOOL_ROUNDS, ROUNDS_WARN, OA_TOKEN_BUDGET, S,
             at: (t) => { nowMs = t; } };
  `)();

  // 早期不提醒：每一輪多一句話是實打實的 token，而且太早講也沒東西可以取捨
  assert.strictEqual(longRun.roundsNote(1), '');
  assert.strictEqual(longRun.roundsNote(longRun.MAX_TOOL_ROUNDS - longRun.ROUNDS_WARN - 1), '');
  // 快用完了才講，而且要講得出剩幾輪
  const warn = longRun.roundsNote(longRun.MAX_TOOL_ROUNDS - 4);
  assert.ok(/還剩 4 輪/.test(warn), warn);
  const last2 = longRun.roundsNote(longRun.MAX_TOOL_ROUNDS - 2);
  assert.ok(/收尾/.test(last2), '快沒了要叫它收尾：' + last2);
  const over = longRun.roundsNote(longRun.MAX_TOOL_ROUNDS);
  assert.ok(/用完/.test(over) && /存檔/.test(over), '最後一輪要講清楚怎麼收：' + over);
  console.log('ok   模型知道自己還剩幾輪');

  // 預算只擋有代價的那一邊：本機跑掉的是電費，外部 API 是錢
  const huge = { t0: 0, tokens: longRun.OA_TOKEN_BUDGET * 100 };
  longRun.at(9 * 60 * 60 * 1000);        // 跑九小時
  assert.strictEqual(longRun.budgetStop(huge), '',
    '本機模式不該有預算 —— 目的就是放著讓它跑完，不是跑一半等人按繼續');
  longRun.S.provider = 'openai';
  assert.strictEqual(longRun.budgetStop({ t0: 0, tokens: 0 }), '');
  assert.ok(/tokens/.test(longRun.budgetStop(huge)), '外部 API 燒太多沒有停');
  assert.ok(/計費|錢/.test(longRun.budgetStop(huge)), '要說得出為什麼只有這邊擋');
  assert.strictEqual(longRun.budgetStop(null), '');
  longRun.S.provider = 'ollama';
  // 時鐘不該再出現在停下來的理由裡
  assert.ok(!/TURN_TIME_BUDGET/.test(script), '時間預算沒有清乾淨');
  console.log('ok   預算只擋外部 API（本機放著跑）');

  // 撞到上限不能只丟一句 toast 就沒了：要留得下「怎麼接回去」
  assert.ok(/c\.stopWhy = over/.test(script), 'runTools 撞到上限沒有記下原因');
  assert.ok(/if \(c\.stopWhy\) return c\.stopWhy/.test(script),
    '續跑條看不到「輪數用完」這種停法');
  assert.ok(/delete c\.stopWhy/.test(script), '按了繼續之後沒有清掉，按鈕會一直在');
  // 續跑一定要重算輪數與計時，不然按下去立刻又撞到同一個上限
  const resumeSrc = grab('resumeRun');
  assert.ok(/S\.run = \{/.test(resumeSrc), '續跑沒有重算：' + resumeSrc);
  console.log('ok   撞到上限之後接得回去');

  // 壓縮之後模型只剩摘要。待辦與背景指令 id 不能靠轉述
  const carry = new Function(`
    const S = { todos: [{ text: '做完了', done: true }, { text: '還沒做' }],
                jobs: [{ id: 'job1', cmd: 'npm install', code: null },
                       { id: 'job2', cmd: 'ls', code: 0 }] };
    ${grab('carryOver')}
    return carryOver;
  `)()();
  assert.ok(carry.includes('還沒做'), '沒帶走未完成的待辦：' + carry);
  assert.ok(!carry.includes('做完了'), '做完的還帶著只是佔位置');
  assert.ok(carry.includes('job1') && carry.includes('npm install'), '背景指令沒帶走');
  assert.ok(!carry.includes('job2'), '跑完的背景指令不用帶');
  assert.ok(carry.includes('check_job'), '要告訴模型怎麼收');
  const empty = new Function(`const S = { todos: [], jobs: [] };
    ${grab('carryOver')} return carryOver;`)()();
  assert.strictEqual(empty, '', '什麼都沒有時不該多塞一段');
  console.log('ok   壓縮之後待辦與背景指令還在');

  // 背景先算好的摘要不能存進對話：Promise 進了 localStorage 會變成 {}，
  // 而 {} 是 truthy 的 —— 重整之後會被當成「算好了」然後炸掉
  const preSrc = grab('preCompact');
  assert.ok(/S\.pre = \{/.test(preSrc), '預先壓縮要存在 S：' + preSrc);
  assert.ok(!/c\._pre/.test(script), '預先壓縮存到對話物件上了，重整會壞');
  assert.ok(/S\.streaming/.test(preSrc), '產生中還去背景算，會跟主要的生成搶同一張卡');

  // 丟背景的 run_shell 立刻就回來了，不能走串流那條路
  assert.ok(/S\.streamTools\.indexOf\(name\) >= 0 && !args\.background/.test(script),
    '背景指令還是走 /run 串流，那會開一個永遠不會有輸出的卡片');
  console.log('ok   背景指令不走串流那條路');

  // ── 外部 API 的工具支援 ──────────────────────────────────
  // 工具往返轉成 OpenAI 格式：每一則 tool 訊息都要帶得回去的 tool_call_id。
  {
    const oa = new Function(`${grab('oaToolCall')}\n${grab('oaMsgs')}\nreturn oaMsgs;`)();
    const out = oa([
      { role: 'user', content: '看一下 a.py' },
      { role: 'assistant', content: '',
        tool_calls: [{ function: { name: 'read_file', arguments: { path: 'a.py' } } },
                     { function: { name: 'list_dir', arguments: { path: '.' } } }] },
      { role: 'tool', tool_name: 'read_file', content: '檔案內容' },
      { role: 'tool', tool_name: 'list_dir', content: 'a.py' }
    ]);
    assert.strictEqual(out.length, 4);
    const tcs = out[1].tool_calls;
    assert.strictEqual(tcs.length, 2);
    assert.strictEqual(typeof tcs[0].function.arguments, 'string',
      'arguments 一定要是字串，OpenAI 不收物件');
    assert.strictEqual(JSON.parse(tcs[0].function.arguments).path, 'a.py');
    assert.strictEqual(tcs[0].type, 'function');
    assert.strictEqual(out[2].role, 'tool');
    assert.strictEqual(out[2].tool_call_id, tcs[0].id, '第一則結果要配回第一個呼叫');
    assert.strictEqual(out[3].tool_call_id, tcs[1].id, '第二則配第二個');
    assert.notStrictEqual(tcs[0].id, tcs[1].id, '同一輪的 id 不能撞在一起');

    // 配不到的（舊對話、被壓縮過）退回純文字，不能丟出沒有 id 的 tool 訊息
    const orphan = oa([{ role: 'tool', tool_name: 'read_file', content: 'x' }]);
    assert.strictEqual(orphan[0].role, 'user', '配不到 id 就不能用 tool 這個 role');

    // 兩輪工具往返：第二輪的 id 不能沿用第一輪沒被認領的
    const two = oa([
      { role: 'assistant', content: '', tool_calls: [{ function: { name: 'a', arguments: {} } }] },
      { role: 'tool', tool_name: 'a', content: '1' },
      { role: 'assistant', content: '沒有工具了' },
      { role: 'tool', tool_name: 'b', content: '2' }
    ]);
    assert.strictEqual(two[3].role, 'user', 'assistant 沒帶 tool_calls，後面的結果要退回純文字');
    console.log('ok   工具往返轉成 OpenAI 格式');
  }

  // SSE 把一支工具的 arguments 切成好幾片，要按 index 拼回去
  {
    const src = script.slice(script.indexOf('async function chatStream'),
                             script.indexOf('// 單次、不串流的呼叫'));
    const chunks = [
      { choices: [{ delta: { tool_calls: [{ index: 0, id: 'call_x',
          function: { name: 'edit_', arguments: '{"pa' } }] } }] },
      { choices: [{ delta: { tool_calls: [{ index: 0,
          function: { name: 'file', arguments: 'th":"a.py"}' } }] } }] },
      { choices: [{ delta: { tool_calls: [{ index: 1, id: 'call_y',
          function: { name: 'run_tests', arguments: '{}' } }] } }] },
      { usage: { prompt_tokens: 12, completion_tokens: 3 } }
    ];
    const got = { tools: null, done: null, body: null };
    const run = new Function('GOT', `
      const S = { provider: 'openai' };
      const oaMsgs = (x) => x;
      const streamSse = async (path, body, sig, onObj) => {
        GOT.body = body;
        ${JSON.stringify(chunks)}.forEach(onObj);
      };
      ${src}
      return chatStream;
    `)(got);
    await run({ model: 'gpt-x', messages: [], tools: [{ type: 'function' }] }, null, {
      think: () => {}, content: () => {}, images: () => {},
      tools: (t) => { got.tools = t; }, done: (d) => { got.done = d; }
    });
    assert.ok(got.body.tools, '外部 API 現在要送出工具定義');
    assert.ok(got.tools && got.tools.length === 2, '兩支工具都要拼回來：'
      + JSON.stringify(got.tools));
    assert.strictEqual(got.tools[0].function.name, 'edit_file', '名字被切開也要接得起來');
    assert.strictEqual(got.tools[0].function.arguments, '{"path":"a.py"}',
      'arguments 的片段要照 index 接回去');
    assert.strictEqual(got.tools[0].id, 'call_x');
    assert.strictEqual(got.done.eval_count, 3);
    console.log('ok   SSE 的 tool_calls 片段拼得回來');
  }

  // 外部 API 問不到模型能力，所以改成手動開關
  {
    const ready = new Function('S', grab('toolsReady') + '\nreturn toolsReady();');
    assert.strictEqual(ready({ provider: 'openai', srv: { tools: true }, oa: {}, caps: {} }), false);
    assert.strictEqual(
      ready({ provider: 'openai', srv: { tools: true }, oa: { tools: true }, caps: {} }), true);
    assert.strictEqual(
      ready({ provider: 'openai', srv: { tools: false }, oa: { tools: true }, caps: {} }), false,
      '伺服器那邊沒開工具就是沒開');
    assert.strictEqual(
      ready({ provider: 'ollama', srv: { tools: true }, oa: { tools: true },
              caps: { m: [] }, model: 'm' }), false, 'Ollama 這邊還是看模型能力');

    // 子代理改走 chatStream 之後，外部 API 那條路它自己就處理好了 ——
    // 兩種 provider 拿到的工具清單要一樣，再濾掉 task 等於白白少一支
    const defs = new Function('S', grab('toolDefs') + '\nreturn toolDefs();');
    const all = [{ function: { name: 'task' } }, { function: { name: 'read_file' } }];
    assert.strictEqual(defs({ provider: 'openai', toolDefs: all }).length, 2,
      '外部 API 模式又把 task 濾掉了');
    assert.strictEqual(defs({ provider: 'ollama', toolDefs: all }).length, 2);
    console.log('ok   外部 API 的工具開關');
  }

  // ── 等人回應時的分頁標題 ────────────────────────────────
  {
    const box = new Function(`
      const document = { title: 'ZackLLMGUI' };
      ${grab('BASE_TITLE', 'const')}
      let waitingN = 0;
      ${grab('waitBadge')}
      return { waitBadge: waitBadge, doc: document };
    `)();
    box.waitBadge(true);
    assert.ok(box.doc.title.indexOf('●') === 0, '等人回應時標題要看得出來：' + box.doc.title);
    box.waitBadge(true);
    box.waitBadge(false);
    assert.ok(box.doc.title.indexOf('●') === 0, '還有一張卡在等，標記不能先拿掉');
    box.waitBadge(false);
    assert.strictEqual(box.doc.title, 'ZackLLMGUI', '都回答完就要恢復原狀');
    box.waitBadge(false);                       // 多減一次不能減成負的
    box.waitBadge(true);
    assert.ok(box.doc.title.indexOf('●') === 0, '計數變成負的就再也標不起來了');
    console.log('ok   等人回應時分頁標題會標記');
  }

  // 確認卡與 ask_user_question 都要掛上標記，而且答完要拿掉
  assert.ok(/waitBadge\(true\);[\s\S]{0,200}notifyBg\('等你確認/.test(script),
    '確認卡沒有掛上等待標記');
  assert.ok(/const done = function \(ok\) \{ waitBadge\(false\)/.test(script),
    '確認卡答完沒有把標記拿掉');
  assert.ok(/notifyBg\('模型在問你/.test(script), 'ask_user_question 沒有通知');

  // ── 檔案樹會自己重讀 ────────────────────────────────────
  assert.ok(/READ_ONLY_TOOLS\.indexOf\(name\) < 0\) touchTree\(\)/.test(script),
    '動過檔案的工具跑完沒有重讀檔案樹');
  {
    const src = grab('touchTree');
    assert.ok(/S\.atFiles = null/.test(src), '@檔名 的快取也要一起作廢');
    assert.ok(/clearTimeout\(treeTimer\)/.test(src), '一輪改五個檔要收斂成一次重畫');
    assert.ok(/S\.treeReady = false/.test(src), '沒開著檔案分頁時要留下「該重讀」的記號');
    assert.ok(/expandInto\(kids, e\.path, depth \+ 1, open\)/.test(script),
      '重畫時沒有把展開狀態往下傳，整棵樹會縮回根目錄');
    console.log('ok   檔案樹會自己重讀');
  }

  // ── 中途停掉的輸出也要截 ──────────────────────────────
  {
    const tail = new Function(grab('TAIL_KEEP', 'const') + '\n' + grab('tailLines')
      + '\nreturn tailLines;')();
    const short = ['a', 'b', 'c'].join('\n');
    assert.strictEqual(tail(short), short, '短的不要動它');
    const long = Array.from({ length: 500 }, (_, i) => 'line' + i).join('\n');
    const cut = tail(long);
    assert.ok(cut.split('\n').length < 150, '五百行沒有截：' + cut.split('\n').length);
    assert.ok(cut.indexOf('line499') >= 0, '最後一行要留著');
    assert.ok(cut.indexOf('省略 400 行') >= 0, '要講省略了幾行：' + cut.slice(0, 60));
    // 被省略的那一段裡的錯誤行要撈出來，不然截斷等於把失敗原因丟掉
    const withErr = (['Traceback (most recent call last):']
      .concat(Array.from({ length: 300 }, (_, i) => 'noise' + i))).join('\n');
    assert.ok(tail(withErr).indexOf('Traceback') >= 0, '省略掉的錯誤行要撈回來');
    console.log('ok   中途停掉的輸出也會截斷');
  }
  // 兩條繞過後端截斷的路都要走 tailLines
  assert.ok(!/result = out\.textContent\.trim\(\) \+/.test(script),
    '按停止／連線斷掉那條路又直接用整段輸出了，最多 2MB 會進 context');
  assert.strictEqual((script.match(/tailLines\(out\.textContent/g) || []).length, 2,
    '兩條路都要截');

  // ── 對話存哪裡：IndexedDB 為主，localStorage 是退路 ──────────
  {
    const mk = (idb) => new Function(`
      const store = { fail: false, put: [], drop: [] };
      const localStorage = { setItem: () => { if (store.fail) throw new Error('quota'); } };
      const said = [];
      const toast = (m) => said.push(m);
      const LS_CHATS = 'c';
      const S = { chats: [{ id: 'a', t: 1 }, { id: 'b', t: 2 }] };
      const current = () => S.chats[0];
      const chatPut = (c) => store.fail
        ? Promise.reject(new Error('idb 壞了')) : (store.put.push(c.id), Promise.resolve());
      const chatDrop = (id) => (store.drop.push(id), Promise.resolve());
      let saveWarned = false;
      let useIdb = ${idb};
      let flushTimer = null;
      const dirtyChats = new Set();
      ${grab('lsSet')}
      ${grab('lsSave')}
      ${grab('saveChats')}
      ${grab('flushChats')}
      return { store, said, S, saveChats, flushChats, lsSet, dirtyChats,
               idbOn: () => useIdb };
    `)();

    // 退路：IndexedDB 開不起來時就是舊行為，滿了要講、而且只講一次
    const ls = mk(false);
    assert.strictEqual(ls.lsSet('k', 1), true, '存成功要回 true');
    ls.store.fail = true;
    assert.strictEqual(ls.lsSet('k', 1), false, '存失敗要回 false，不能靜靜吞掉');
    ls.saveChats();
    assert.strictEqual(ls.said.length, 1, '滿了要講一次：' + JSON.stringify(ls.said));
    assert.ok(/匯出|滿/.test(ls.said[0]), '要說得出怎麼辦：' + ls.said[0]);
    ls.saveChats(); ls.saveChats();
    assert.strictEqual(ls.said.length, 1, '每次呼叫都 toast 會洗版');
    ls.store.fail = false; ls.saveChats();
    ls.store.fail = true; ls.saveChats();
    assert.strictEqual(ls.said.length, 2, '好了之後再壞要再講一次');

    // 主線：只寫改動的那一則。整包重寫的話兩個分頁會互相蓋掉，而且串流時每秒好幾 MB
    const db = mk(true);
    db.saveChats();
    assert.deepStrictEqual(db.store.put, [], 'saveChats 不該當場寫，要收成一批');
    assert.deepStrictEqual(Array.from(db.dirtyChats), ['a'], '髒的只有動過的那一則');
    await db.flushChats();
    assert.deepStrictEqual(db.store.put, ['a'], '只寫改動的那一則：' + db.store.put);

    // 陣列裡已經沒有的，flush 時要從資料庫刪掉，不然重整又冒出來
    db.dirtyChats.add('沒了的');
    await db.flushChats();
    assert.deepStrictEqual(db.store.drop, ['沒了的'], '刪掉的對話沒有從資料庫清掉');

    // 寫失敗要退回 localStorage 並且講出來 —— 靜靜掉一整場任務是最糟的失敗方式
    db.store.fail = true;
    db.saveChats();
    await db.flushChats();
    assert.strictEqual(db.idbOn(), false, 'IndexedDB 壞了要退回 localStorage');
    assert.ok(/匯出/.test(db.said[db.said.length - 1]), '要叫人先匯出：' + db.said);
    console.log('ok   對話存進 IndexedDB，壞了退回 localStorage 並且講');
  }

  // ── 自動模式要傳到後端 ────────────────────────────────
  assert.ok(/setServerTools\(\{ auto: m\[0\] \}\)/.test(script),
    '切自動模式沒有同步到後端，系統提示會停在「每一次都會問你」');
  {
    // 驗證迴圈本來只會 !!，而 !!'off' 跟 !!'ws' 都是 true —— 等於沒驗
    const src = grab('setServerTools');
    assert.ok(/typeof patch\[k\] === 'string' \? got === patch\[k\]/.test(src),
      '字串開關要用相等比，用 !! 比等於什麼都沒驗到：' + src.slice(0, 200));
    console.log('ok   自動模式會同步到後端');
  }

  // ── 長回覆不要每 60ms 重解一次整篇 ────────────────────
  assert.ok(/content\.length < BIG_MSG \|\| now - lastRender > BIG_MSG_MS/.test(script),
    '長回覆還是每一輪 flush 都重解整篇 markdown');
  // 但收尾一定要整篇重畫，不然跳過的那一次會變成畫面停在半截
  assert.ok(/fillAssistant\(el, record\)/.test(script),
    'finishStream 沒有整篇重畫，降頻會讓最後一段掉字');
  console.log('ok   長回覆的 markdown 重解會降頻');

  console.log('\n全部通過');
})().catch((e) => { console.error(e); process.exit(1); });
