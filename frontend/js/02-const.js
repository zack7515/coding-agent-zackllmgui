/* ══════════════════════ 常數 ══════════════════════ */
const DEFAULTS = {
  temperature: 0.8, top_p: 0.9, top_k: 40, min_p: 0, repeat_penalty: 1.1,
  num_ctx: 64, num_predict: -1, seed: -1, stop: '', keep_alive: '5m', system: '',
  num_keep: '', num_batch: '', num_gpu: '', num_thread: '', draft_num_predict: ''
};
const SLIDERS = [
  { id: 'temperature', label: 'Temperature', min: 0, max: 2, step: 0.01, dec: 2 },
  { id: 'top_p', label: 'Top P', min: 0, max: 1, step: 0.01, dec: 2 },
  { id: 'top_k', label: 'Top K', min: 0, max: 100, step: 1, dec: 0 },
  { id: 'min_p', label: 'Min P', min: 0, max: 1, step: 0.01, dec: 2 },
  { id: 'repeat_penalty', label: 'Repeat penalty', min: 0.5, max: 2, step: 0.01, dec: 2 }
];
const THINK_LEVELS = [['關', false], ['低', 'low'], ['中', 'medium'], ['高', 'high'], ['最高', 'max']];
const THINK_TOGGLE = [['關閉', false], ['開啟', true]];
// 對話框打 / 就會出現的功能清單。skills 會接在這後面（那是資料，不是寫死的）。
// 放在這裡而不是散在各處：使用者看得到的入口就這一份。
const SLASH_CMDS = [
  ['compact', '壓縮對話，把較早的訊息濃縮成摘要', function () { compactChat(); }],
  ['files', '打開右側的檔案分頁', function () { openWorkspace(); }],
  ['workspace', '換一個工作區資料夾', function () { openBrowser(); }],
  ['write', '切換「模型可以修改檔案」', function () { toggleWrite(); }],
  ['rewind', '把檔案還原到某個時間點', function () { openRewind(); }],
  ['tools', '功能與工具（本機工具、計畫模式、自動模式）', function () { openFeatureMenu(); }],
  ['export', '匯出這個對話（HTML / Markdown / JSON）', function () { exportMenu(); }],
  ['agents', '看有哪些子代理在跑：追出上層是誰、把它連同背景指令中斷',
   function () { openAgents(); }],
  ['new', '開一個新對話', function () { newChat(true); }]
];

const FONT_MIN = 70, FONT_MAX = 200;    // 百分比。再小看不到、再大版面會爆

// 頁面若是被本機的 serve.py 端出來的，就走同源代理，完全避開 CORS。
const SAME_ORIGIN = location.protocol === 'http:' || location.protocol === 'https:';
const DEFAULT_HOST = SAME_ORIGIN ? location.origin : 'http://localhost:11434';

// 內建的系統提示預設，選了就直接填進系統提示欄
// 本機跑的模型通常比雲端小，指令寫得越具體、限制越明確，結果越穩。
// 每組都給角色、規則、輸出格式、禁止事項四段，不要只寫一句話。
const PRESETS = [
  ['通用助理',
   '你是一位可靠的繁體中文助理。\n\n' +
   '規則：\n' +
   '1. 先直接回答問題，再視需要補充說明；不要用「這是一個好問題」之類的開場白。\n' +
   '2. 只根據已知資訊回答。不知道、不確定、資料可能過期時，直接說「我不確定」並說明缺什麼資訊。\n' +
   '3. 不要編造人名、數字、日期、網址、API 名稱或引用來源。寧可說沒有，也不要生一個看起來合理的。\n' +
   '4. 使用者用什麼語言問，就用什麼語言答；中文一律用繁體中文與臺灣慣用語。\n' +
   '5. 需要條列時每點一行，避免整段長文；一般問題控制在 300 字以內。\n\n' +
   '禁止：重複使用者的問題、為自己是 AI 道歉、在結尾問「還有什麼需要幫忙的嗎」。'],

  ['技術問答',
   '你是一位資深軟體工程師，正在回答同事的問題。對方看得懂程式碼，不需要基礎教學。\n\n' +
   '規則：\n' +
   '1. 先給結論或可直接執行的做法，再解釋為什麼；解釋控制在三句話以內。\n' +
   '2. 程式碼一律放進標註語言的 code block，例如 ```python。可以直接貼上執行，不要用 ... 省略關鍵行。\n' +
   '3. 指令要寫完整，包含必要的旗標；跨平台差異（Linux / Windows）分開寫。\n' +
   '4. 有前提假設（版本、作業系統、已安裝套件）就明講。\n' +
   '5. 不確定某個 API、參數或函式是否存在時，直接說不確定並建議如何查證，' +
   '絕不要生一個看起來像真的的函式名稱。\n' +
   '6. 有多種做法時只推薦一種並說明理由，其餘最多一句話帶過。\n\n' +
   '禁止：客套話、免責聲明、把簡單問題答成教學文、沒被問到就重寫使用者的整份程式。'],

  ['程式碼審查',
   '你是嚴格但務實的 code reviewer。你的工作是找出真正會出事的地方。\n\n' +
   '規則：\n' +
   '1. 依嚴重度排序輸出：先「錯誤」（會壞、會回傳錯結果），再「風險」' +
   '（邊界情況、資源沒關、競態、安全性），最後「建議」（可讀性、簡化）。\n' +
   '2. 每一項固定三行：\n' +
   '   - 位置：檔名或函式名，有行號就寫行號\n' +
   '   - 問題：什麼輸入或情境下會出事，以及會出什麼事\n' +
   '   - 修法：具體怎麼改，能給程式碼就給\n' +
   '3. 只講你在這段程式碼裡真的看到的問題。看不到全貌時，說明你需要哪個檔案才能判斷。\n' +
   '4. 沒有問題就直接說「沒有發現需要修的地方」，不要為了湊數而挑毛病。\n\n' +
   '禁止：稱讚、總結性的空話（例如「整體結構良好」）、把個人風格偏好講成錯誤。'],

  ['中英翻譯',
   '你是專業譯者。收到中文輸出英文，收到英文輸出繁體中文（臺灣用語）。\n\n' +
   '規則：\n' +
   '1. 只輸出譯文。不要附上原文、不要解釋、不要加註釋，除非使用者要求。\n' +
   '2. 完整保留原本的排版：換行、清單、標題層級、表格、code block 一律照舊。\n' +
   '3. code block 內的程式碼不翻譯；只翻譯裡面的註解與字串（若使用者要求）。\n' +
   '4. 專有名詞、產品名、API 名、指令保留原文；業界慣用的英文縮寫（如 API、GPU）不必硬翻。\n' +
   '5. 語氣跟著原文走：技術文件就精確，口語就自然，不要自行加上客套或修飾。\n' +
   '6. 遇到有歧義、無法判斷的句子，選最通順的譯法，並在全文最後用一行 " 註：" 說明。\n\n' +
   '禁止：逐字直譯出不通順的中文、擅自增刪內容、把一句話擴寫成一段。'],

  ['重點摘要',
   '你負責把長文濃縮成可以快速掃過的重點。\n\n' +
   '規則：\n' +
   '1. 最多七點，每點一行、一句話，不要換行成段落。\n' +
   '2. 保留具體的數字、日期、人名、結論、決議與待辦；這些是摘要的價值所在。\n' +
   '3. 完全依照原文，不要推論、不要補充原文沒有的背景知識。\n' +
   '4. 原文有明確結論或行動項目時，放在第一點。\n' +
   '5. 原文分成幾個主題時，用「主題：內容」的格式讓人一眼分辨。\n' +
   '6. 原文太短（少於三句）就直接說「原文已經夠短，不需要摘要」。\n\n' +
   '禁止：開場白、結語、「本文主要在講」這種句型、把不確定的推測寫成事實。'],

  ['Shell 專家',
   '你精通 Linux、macOS 與 PowerShell，正在協助一位工程師操作終端機。\n\n' +
   '規則：\n' +
   '1. 先給可直接複製執行的指令，放進 ```bash 或 ```powershell 區塊。\n' +
   '2. 指令後面用一到兩句說明它做什麼；只在有副作用時才多寫。\n' +
   '3. 會刪除檔案、覆寫資料、改動系統設定、需要 sudo 的指令，一定要先用一行「⚠ 風險：」' +
   '說明影響範圍，並提供先確認的做法（例如先 --dry-run 或先 ls 看一遍）。\n' +
   '4. 一次只解一個問題，不要把五種替代做法全部列出來。\n' +
   '5. 不確定使用者的發行版或 shell 時，先問，或明講你假設的是哪一種。\n' +
   '6. 不要使用需要額外安裝的工具，除非先說明怎麼安裝。\n\n' +
   '禁止：把危險指令寫得像沒事一樣、用 rm -rf 當範例、憑空發明不存在的旗標。'],

  ['文字潤稿',
   '你是中文編輯，負責讓文字更清楚，而不是更花俏。\n\n' +
   '規則：\n' +
   '1. 只輸出改寫後的完整版本，保留原本的段落與排版。\n' +
   '2. 修掉贅字、重複、翻譯腔與過長的句子；一句話講一件事。\n' +
   '3. 保持原意、語氣與立場。作者說「大概」就不要改成「一定」。\n' +
   '4. 統一術語與標點；中英文之間留一個空格；數字與單位維持原樣。\n' +
   '5. 專有名詞、程式碼、引號內的原文一律不動。\n' +
   '6. 有語意不清、無法判斷作者意思的句子，保留原句，並在全文最後用「待確認：」列出來。\n\n' +
   '禁止：加入原文沒有的內容、擅自刪掉整段、把口語稿改成公文腔。']
];

// 壓縮對話用的指令。做法參考 Claude Code 的 /compact：留下之後還會用到的事實，
// 丟掉寒暄與已經解決的枝節。
const COMPACT_PROMPT =
  '你的工作是把一段對話紀錄壓縮成摘要，讓另一個模型只看摘要就能無縫接續這場對話。\n\n' +
  '請依序輸出這幾節（沒有內容的節就整節省略，不要寫「無」）：\n' +
  '1. 目標：使用者想達成什麼，包含明確講過的限制與偏好。\n' +
  '2. 已完成：做了什麼、得到什麼結論。\n' +
  '3. 關鍵事實：檔名、路徑、指令、參數、版本、數值、錯誤訊息等之後還會用到的細節，' +
  '一律照抄原文，不要改寫、不要四捨五入。\n' +
  '4. 程式碼：後續還會沿用的片段，放進標註語言的 code block；只留必要的部分。\n' +
  '5. 待辦：還沒做完或使用者明確要求接下來要做的事。\n' +
  '6. 注意事項：試過但失敗的做法、使用者否決過的方案（避免重蹈覆轍）。\n\n' +
  '規則：只寫對話裡真的出現過的內容，不要推論或補充；' +
  '寧可長一點也不要漏掉具體數值；使用對話原本的語言；不要加開場白或結語。\n\n' +
  '以下是要壓縮的對話紀錄：\n\n';

// 串流時每 60ms 會把**整篇**重新 renderMarkdown 一次。短回覆無所謂，
// 長回覆到後面就是每秒十六次全文重解。超過這個長度改成每 250ms 一次 ——
// 眼睛看不出差別（字還是一直在長），CPU 差很多。
// 最後一次渲染不靠這條：finishStream() 收尾時本來就會整篇重畫。
const BIG_MSG = 20000;
const BIG_MSG_MS = 250;

const PASTE_LIMIT = 1200;      // 貼上超過這個長度就自動收成附件

// 附加文字檔時用來決定 code block 的語言標籤
const EXT_LANG = {
  js:'javascript', mjs:'javascript', cjs:'javascript', jsx:'javascript',
  ts:'typescript', tsx:'typescript', py:'python', rb:'ruby', go:'go', rs:'rust',
  java:'java', kt:'kotlin', c:'c', h:'c', cc:'cpp', cpp:'cpp', hpp:'cpp', cs:'csharp',
  php:'php', swift:'swift', sh:'bash', bash:'bash', zsh:'bash', ps1:'powershell',
  sql:'sql', json:'json', yml:'yaml', yaml:'yaml', toml:'toml', ini:'ini', cfg:'ini',
  conf:'ini', env:'bash', xml:'xml', html:'html', htm:'html', css:'css', scss:'css',
  vue:'html', svelte:'html', dockerfile:'bash', makefile:'bash', csv:'csv', tsv:'csv',
  diff:'diff', patch:'diff', log:'', txt:'', md:'markdown', rst:'', tex:''
};
const DOC_EXT = ['pdf', 'docx', 'odt', 'pptx'];

// 工具定義由 serve.py 供給（/upstream、/tools、/workspace 都會回傳目前這一份）。
// 前端不自己維護一份 schema：兩邊各寫一次，遲早會對不上。
// delete_file 也在這一級：它跟 edit_file 一樣走 ws_path()、一樣先備份、
// 一樣進 journal，所以一樣倒得回來。歸在別級的話模型就會退回去用 rm ——
// 而 rm 是三者之中唯一沒有還原點的。
const WRITE_TOOLS = ['write_file', 'edit_file', 'delete_file'];

// 工具迴圈跑到一半時輸入框的提示。**同時當成「現在可以排隊」的判斷依據** ——
// blockComposer 也用在「尚未連線」「沒有可用模型」，那兩種情況排隊沒有意義
// （沒有東西會來收），所以認的是這一句而不是「有沒有被 block」。
const RUNNING_HINT = '跑到一半也可以打字，Enter 會排隊，這一輪跑完就送出';

// `@資料夾/` 一次附整個目錄的上限。**一定要有上限**：一個 node_modules
// 就足以把 context 灌爆，而且是在使用者按下 Enter 之後才發現。
// 超過就停下來並且**講清楚少了哪幾個**，不要靜靜截斷。
// 連不上就自動重試。Ollama 在載入大模型或正在服務別的請求時會拒絕連線，
// 那種失敗過幾秒就好了 —— 原本是直接丟一張錯誤卡，人得自己重送一次。
// 「連不上」的提示。**同時是「這種擋法可以被重試機制接手」的判斷依據** ——
// 沒有這一條的話，Ollama 掛掉時連送都送不出去，重試永遠沒機會跑，
// 而「連不上」正是最需要重試的情況（模型正在載入、服務剛重啟）。
// 一輪跑完之後留在對話裡的那一行。**per-message 的統計看不出這個** ——
// 那裡寫的是「這一次呼叫模型花了幾秒」，一輪十幾次呼叫加上工具執行時間，
// 加起來是完全不同的數字。長任務要看的是這個。
function turnLine(t) {
  if (!t) return '';
  return '這一輪 ' + fmtElapsed(t.ms) + ' · ' + t.rounds + ' 輪 · '
    + t.calls + ' 次工具 · ' + fmtTokens(t.tokens) + ' tokens';
}

const CONN_HINT = '尚未連線，無法送出';
const RETRY_MAX = 4;
const RETRY_BASE_MS = 2000;

// **只重試「這一次根本沒接上」的錯誤。** 已經開始吐字之後再重試會重複一段，
// 而 4xx 重試幾次都一樣（模型名打錯、參數不合法）。
function isRetryable(err, gotAnything) {
  if (gotAnything) return false;
  if (err && err.name === 'AbortError') return false;
  const raw = String((err && err.message) || err || '');
  if (/failed to fetch|networkerror|load failed|connection|ECONNREFUSED/i.test(raw)) return true;
  const m = raw.match(/HTTP (\d{3})/);
  return !!m && ['500', '502', '503', '504', '429'].indexOf(m[1]) >= 0;
}

const AT_DIR_FILES = 40;
const AT_DIR_CHARS = 120000;

// 從扁平的檔案清單推出有哪些資料夾。清單本來就在手上，不必再問一次伺服器。
function dirsOf(files) {
  const seen = {};
  (files || []).forEach(function (p) {
    const parts = p.split('/');
    for (let i = 1; i < parts.length; i++) seen[parts.slice(0, i).join('/') + '/'] = 1;
  });
  return Object.keys(seen).sort();
}

function filesUnder(files, dir) {
  return (files || []).filter(function (p) { return p.indexOf(dir) === 0; });
}

// 「跑了多久」的人話。長任務時這是唯一看得出「它還在動」的東西之一。
function fmtElapsed(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return s + ' 秒';
  const m = Math.floor(s / 60);
  return m < 60 ? m + ' 分 ' + (s % 60) + ' 秒'
                : Math.floor(m / 60) + ' 小時 ' + (m % 60) + ' 分';
}

// topbar 的系統用量。順序＝顯示順序＝窄畫面時誰活下來，
// VRAM 排第一是因為它是唯一一個「爆了不會報錯、只會慢十倍」的東西：
// 塞不下的層會被 Ollama 搬回 CPU，畫面上什麼都看不出來。
const SYS_METRICS = [
  ['vram', 'VRAM', '顯示卡記憶體。模型塞不下就會被搬回 CPU 跑'],
  ['ram', 'RAM', '系統記憶體'],
  ['cpu', 'CPU', '這台機器的 CPU 忙碌程度'],
  ['gpu', 'GPU', '顯示卡的運算使用率']
];

// 一格用量要顯示的字，外加 0～1 的滿載程度（畫顏色與長條用）。
// 拿不到資料就回 null —— 那一格整個不畫，而不是畫一個「—」讓人以為是 0。
function sysCell(id, d) {
  if (!d) return null;
  const card = (d.gpu || [])[0];
  if (id === 'vram') {
    if (!card || !card.vram || !card.vram.total) return null;
    return { text: card.vram.used.toFixed(1) + '/' + card.vram.total.toFixed(0) + ' G',
             at: card.vram.used / card.vram.total };
  }
  if (id === 'ram') {
    if (!d.ram || !d.ram.total) return null;
    return { text: d.ram.used.toFixed(1) + '/' + d.ram.total.toFixed(0) + ' G',
             at: d.ram.used / d.ram.total };
  }
  if (id === 'cpu') {
    if (typeof d.cpu !== 'number' || d.cpu < 0) return null;
    return { text: d.cpu.toFixed(0) + '%', at: d.cpu / 100 };
  }
  if (id === 'gpu') {
    if (!card || typeof card.util !== 'number') return null;
    return { text: card.util + '%', at: card.util / 100 };
  }
  return null;
}

// 顏色只有三段：夠用、快滿了、滿了。再細分沒有人看得出差別。
function sysLevel(at) { return at >= 0.92 ? 'full' : (at >= 0.75 ? 'hot' : ''); }

// 等第一個字時，訊息裡要畫的那一行。回空字串＝不用畫
// （思考內容自己在動，看得到就不必再講一次）。
function waitText(ms, thinking, showThink) {
  if (!thinking) return '等模型回應… ' + fmtElapsed(ms);
  if (showThink) return '';
  return '思考中… ' + fmtElapsed(ms) + '（已寫 ' + thinking.length +
    ' 字，「顯示思考」關著所以看不到內容）';
}

// 現在正在做的那一項：第一個還沒完成的待辦。
// 對照 Claude Code 的 spinner，它會顯示 `Next: <下一項待辦>` —— 輪數與 token 數
// 說明「跑了多少」，這一句說明「在幹嘛」，兩者缺一不可。
function currentTodo(todos) {
  const t = (todos || []).filter(function (x) { return !x.done; })[0];
  return t ? t.text : '';
}

// 「模型說做完了」的那一刻要攔一次的依據。
// 對照 Claude Code 的 Stop hook：它的回傳可以帶 additionalContext，
// schema 原文寫「the conversation continues so the model can act on it」——
// 也就是模型要停下來時還能被推回去繼續。這裡不做成可設定的 hook（那個拿掉了），
// 條件寫死成一條：**寫了測試卻一次都沒跑過**。
//
// 刻意不做「改了程式就要跑測試」：那條在沒有測試的專案上永遠是誤報，
// 而誤報的自動提醒最後一定會被關掉。寫了測試沒跑，沒有第二種解釋。
// 這兩條不折行，而且不能以 \/ 收尾：tests/test_gui.js 的 grab() 只吃得下一行
// 寫完的 const，而且會把 // 之後當成行尾註解砍掉 —— 原本結尾是 tests?\//i，
// 那個 \// 會被當成註解起點，抓回去的正規表示式就少了收尾的斜線。
const TEST_FILE_RE = /(^|\/)tests?\/|(^|\/)(test_[^/]+\.py|[^/]+_test\.(py|go|rb)|[^/]+\.(test|spec)\.[jt]sx?)$/i;
const TEST_CMD_RE = /\b(pytest|unittest|jest|vitest|mocha|ava|go test|cargo test|npm (run )?test|yarn test|pnpm test)\b/i;

function looksLikeTestFile(path) { return TEST_FILE_RE.test(String(path || '')); }
function looksLikeTestRun(name, args) {
  if (name === 'run_tests') return true;
  return name === 'run_shell' && TEST_CMD_RE.test(String((args || {}).command || ''));
}

// 迴圈要結束了 —— 回傳一句要塞回去的話，或空字串代表可以收工。
// ponytail: 只有一條規則（寫了測試沒跑），一輪只攔一次。
//          「改了程式就要跑測試」在沒有測試的專案上永遠是誤報，所以不做。
//           要更多條件的話這裡改成一串 check 依序跑，但先想清楚誤報的代價。
function finishCheck(run) {
  if (!run || run.nagged) return '';                 // 一輪只攔一次，不然模型堅持不跑會卡死
  if (!run.wroteTests || run.ranTests) return '';
  return '你在這一輪寫了測試（' + run.wroteTests + '）卻一次都沒有跑過。'
    + '用 run_tests 跑一次；沒過就修到過，然後再說做完了。';
}

function toolDefs() {
  // 子代理改走 chatStream 之後，外部 API 那條路它自己就處理好了，不必再濾掉 task。
  return S.toolDefs || [];
}

// 後端的 tail_of() 的前端版。**中途停掉那條路繞過了後端的截斷** ——
// 正常結束時結果是後端算好的（留最後 100 行），按停止或連線斷掉時用的是
// 瀏覽器自己累積的整段，而那一段最多會到 MAX_RUN_BYTES（2MB）。
// 沒有這一支的話「按停止」比「讓它跑完」更容易把 context 撐爆。
const TAIL_KEEP = 100;

function tailLines(text, keep) {
  const lines = String(text || '').split('\n');
  keep = keep || TAIL_KEEP;
  if (lines.length <= keep) return String(text || '');
  const bad = lines.slice(0, -keep)
    .filter(function (ln) { return /(FAILED|ERROR|Traceback|assert |Exception)/.test(ln); })
    .slice(-40);
  const head = '（前面省略 ' + (lines.length - keep) + ' 行'
    + (bad.length ? '，以下是其中的錯誤行）\n' + bad.join('\n') + '\n…\n' : '）\n');
  return head + lines.slice(-keep).join('\n');
}

// **失控煞車，不是預算。** 這個數字唯一擋的是模型繞圈（A→B→A→B ——
// REPEAT_LIMIT 只認得參數一模一樣的重試，繞圈認不出來）。
// 原本是 25，而 25 輪對「改一個檔 → 跑測試 → 再改」的任務常常不夠，
// 停下來等人按「繼續」就違反了放著跑的目的。
const MAX_TOOL_ROUNDS = 200;
const ROUNDS_WARN = 5;          // 剩這麼多輪才提醒。太早講只是每輪多燒一句話

// 撞到上限之前先讓模型知道。不講的話它不會安排優先順序 ——
// 可能把 20 輪花在到處讀檔，剩 5 輪才開始動手，然後停在一半。
function roundsNote(depth) {
  const left = MAX_TOOL_ROUNDS - depth;
  if (left > ROUNDS_WARN) return '';
  if (left <= 0) {
    return '（工具輪數用完了，這是最後一次回答。把已經做好的存檔，'
      + '然後講清楚做到哪裡、還剩什麼沒做。）';
  }
  return '（工具還剩 ' + left + ' 輪就會停下來。'
    + (left <= 2 ? '開始收尾：先存檔，再講清楚進度。' : '先做最重要的那幾件事。') + '）';
}

// **本機模型不設預算。** 原本有一個 30 分鐘的時鐘上限，撞到就停下來等人按
// 「繼續」—— 但那正好違反「放著讓它自己跑完」這個目的，而本機跑掉的
// 只有電費與時間，沒有別人在替你結帳。
//
// 外部 API 是另一回事：那裡的 token 是錢，而且是自動模式下沒人看著燒的。
// 所以護欄留在有代價的那一邊，用 token 不用時鐘（時鐘跟花費沒有關係，
// 等對方 API 排隊也是時間）。撞到不是結束，是停下來給一顆「繼續」。
const OA_TOKEN_BUDGET = 150000;

function budgetStop(run) {
  if (!run || S.provider !== 'openai') return '';
  if (run.tokens > OA_TOKEN_BUDGET) {
    return '這一輪已經用掉 ' + fmtTokens(run.tokens)
      + ' tokens（外部 API 是按量計費的），先停下來讓你看一眼';
  }
  return '';
}

// 自動模式：哪些工具不必每次問。危險指令永遠會問，不受這裡影響。
const AUTO_MODES = [
  ['off', '每一次都問', '每個工具呼叫都要你按執行'],
  ['read', '唯讀自動', '讀檔、搜尋、列目錄自動放行；改檔案、跑指令仍要你點頭'],
  ['edit', '改檔案自動', '連改檔案也自動放行；run_shell、run_tests 仍要你點頭'],
  ['full', '跑指令自動', 'run_shell／run_tests／setup_env 也自動放行；'
    + 'rm、sudo、pip install 這種風險指令仍要你點頭'],
  ['ws', '工作區內全自動', '連 rm、mv、chmod 也自動放行 —— 但只限路徑全都在工作區裡的；'
    + '動到工作區外、sudo、裝套件仍要你點頭。沙盒開著時全部不問（沙盒本身就出不去）']
];
const READ_ONLY_TOOLS = ['read_file', 'list_dir', 'search_files', 'fetch_url',
  'todo_write', 'load_skill'];   // load_skill 只是讀 serve.py 旁邊的一份說明

function autoLabel() {
  const m = AUTO_MODES.filter(function (x) { return x[0] === S.auto; })[0] || AUTO_MODES[0];
  return m[1];
}

// 回傳 true 代表這一次不用問人。
// 順序（第一個成立的說了算）：
//   deny 規則 > 風險指令一律問 > allow 規則 > 自動模式
// allow **不能**蓋過風險指令 —— 那條保證是寫在文件上的，
// 不能被一個設定檔悄悄拿掉。deny 由伺服器真的擋，這裡只是不要白問一次。
function autoApprove(name, risk, rule, scope) {
  if (rule && rule.action === 'deny') return false;
  if (risk === 'block') return false;             // 這一級 serve.py 直接拒絕，本來就跑不了
  // 危險指令要人看過。唯一的例外是「工作區內全自動」加上後端算出這行指令
  // 動到的路徑全都在工作區裡（scope === 'ws'，ws_scoped() 判的；沙盒開著時
  // 每一行指令都算，因為沙盒外面是唯讀的）——
  // 那一類改壞了還有 git 與 .zackllmgui-backup/ 救得回來，而且它是
  // 「放著跑測試」最常撞到的一格（rm 掉 __pycache__、mv 檔案）。
  if (risk && risk !== 'ok' && !(S.auto === 'ws' && scope === 'ws')) return false;
  if (rule && rule.action === 'allow') return true;
  if (rule && rule.action === 'ask') return false;
  if (S.auto === 'read') return READ_ONLY_TOOLS.indexOf(name) >= 0;
  // 改檔案自動、跑指令要問。這一格是平常該待的地方：改檔案佔了工具呼叫
  // 一半以上，而且**改檔案有還原點**（journal + backup），點錯了倒得回來；
  // run_shell 沒有。少了這一格，人的實際反應是「改十個檔要點十次，乾脆整個放開」
  // —— 那才是真正的風險。setup_env 不算：它要連網裝套件，那是指令不是改檔案。
  if (S.auto === 'edit') {
    return READ_ONLY_TOOLS.indexOf(name) >= 0 || WRITE_TOOLS.indexOf(name) >= 0;
  }
  // 計畫還是要人核准
  if (S.auto === 'full' || S.auto === 'ws') return name !== 'submit_plan';
  return false;
}

const FIELDS = ['num_ctx', 'num_predict', 'seed', 'keep_alive', 'stop',
  'num_keep', 'num_batch', 'num_gpu', 'num_thread', 'draft_num_predict'];

// [欄位, 一句話說明, 上限的算法]
// max 是函式的原因：num_gpu 看模型有幾層、num_thread 看這台機器有幾顆核心，
// 兩個都要等載入之後才知道，寫死在 HTML 裡會是錯的。
const PARAM_HELP = [
  ['num_ctx', 'context 視窗長度，單位是 K（1K = 1024 tokens，填 64 就是 65536）。' +
    '整段對話加上模型的回覆都要塞得進去，' +
    '超過就會從最前面開始被丟掉。開大不是免費的：KV cache 會跟著線性長，' +
    '記憶體不夠時 Ollama 會把層數搬回 CPU，速度直接掉一個數量級。',
   function () {
     // 上限是模型自己的 context_length（伺服器的 /api/show）。填得比它大的話
     // Ollama 不會報錯，它就是默默用模型的上限 —— 那是最難查的一種「變笨」。
     const max = S.ctxMax[S.model] || 0;
     return max
       ? { max: Math.floor(max / 1024), unit: 'K',
           note: (S.model || '這個模型') + ' 支援到 ' + Math.floor(max / 1024) + 'K' }
       // 問不到就不要顯示上限。掰一個數字出來比沒有更糟 ——
       // 使用者會以為那是真的，然後照著它調。
       : { max: 0, unit: 'K', note: '問不到這個模型的 context 上限，這裡不設限制' };
   }, 'num_ctx（K）'],
  ['num_predict', '這一次最多產生幾個 token。-1 是不限、-2 是填滿 context。' +
    '思考模式的內容也算在裡面 —— 想限制模型想太久就用這個。', null],
  ['seed', '亂數種子。填同一個數字加上同樣的參數與提示，輸出就可以重現。' +
    '-1 或空白是每次都隨機。', null],
  ['keep_alive', '產生完之後模型在記憶體裡待多久。5m、1h 這種寫法，' +
    '0 是用完立刻卸載，-1 是一直留著。留著下一次不用重載，代價是佔著顯示記憶體。', null],
  ['stop', '遇到這些字串就停下來，多組用逗號分隔。' +
    '模型不會把停止字串本身輸出出來。', null],
  ['num_keep', '重新計算 context 時，開頭要保留幾個 token 不被丟掉。' +
    '通常留給系統提示，讓它在長對話裡不會被擠掉。', null],
  ['num_batch', '一次餵給模型幾個 token 做 prompt 評估。調大讀提示比較快、' +
    '但瞬間佔用的記憶體也比較多。記憶體吃緊時往下調（256、128）。', null],
  ['num_gpu', '要把幾層搬到 GPU 上跑。0 是全部用 CPU，' +
    '設成模型的層數就是整個放進 GPU。放不下時 Ollama 會自己減，' +
    '手動指定通常是為了留顯示記憶體給別的東西。',
   function () {
     // 上限是模型自己的層數，從伺服器的 /api/show（model_info 的 *.block_count）讀來，
     // 跟誰開這個網頁無關 —— 這個數字本來就是「跑模型那一端」的事實。
     return S.layers[S.model]
       ? { max: S.layers[S.model],
           note: S.model + ' 有 ' + S.layers[S.model] + ' 層（來自伺服器的 /api/show）' }
       : { max: 0, note: '要先選好模型才知道層數' };
   }],
  ['num_thread', 'CPU 推論要用幾條執行緒。預設是實體核心數，' +
    '設超過核心數不會更快（互相搶反而更慢）。' +
    '要留 CPU 給別的工作時才往下調。注意它是套用在**跑 Ollama 的那一台**，' +
    '不是開這個網頁的這台 —— 兩者不同機器時這裡讀不到上限。',
   function () {
     // 這個數字只有「serve.py 跟 Ollama 同一台」時才拿得到（serve.py 回報 os.cpu_count()）。
     // Ollama 的 API 沒有任何一支回報主機的核心數，所以遠端時就是不設上限，
     // 不要拿開網頁這台的核心數去冒充。
     if (!ollamaIsLocal()) {
       return { max: 0,
                note: 'Ollama 在 ' + (S.upstream || '遠端') +
                      '，它的 API 不回報主機核心數，所以這裡不設上限' };
     }
     return S.cpus ? { max: S.cpus, note: '伺服器 ' + S.cpus + ' 顆邏輯核心（os.cpu_count）' }
                   : { max: 0, note: '（讀不到核心數）' };
   }],
  ['draft_num_predict', '推測解碼（speculative decoding）一次讓草稿模型先猜幾個 token。' +
    '猜中就一次收下、省掉好幾輪；猜錯就白算。只有搭配草稿模型（例如 MTP 頭）才有意義。',
   null]
];

// key 沿用舊名：改掉的話大家存在瀏覽器裡的對話會全部讀不到
const LS_CONF = 'ollama_gui.config';
const LS_CHATS = 'ollama_gui.chats';
const LS_PRESETS = 'ollama_gui.presets';

