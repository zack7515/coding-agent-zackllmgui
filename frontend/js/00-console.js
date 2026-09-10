/* ══════════════════════ 瀏覽器丟出來的錯誤 ══════════════════════ */
// 工具的輸出都回得到模型手上（run_shell 的 stdout、linter、收尾驗證），
// 只有這一類靜靜地留在 F12 裡。而改壞前端最常見的樣子就是它：頁面照樣載入、
// 版面看起來正常，只有某一顆按鈕按下去沒反應。
//
// 這個檔排在最前面（build.py 照檔名排序），因為載入當下丟的錯也要抓得到。
// 送出去的是**這個網頁自己的**錯誤，不是模型做出來的其他頁面 —— 那個要開
// 瀏覽器去看，見 plan-agent 2.23。
const CLIENT_ERR_MAX = 20;        // 一次最多送這麼多：render 迴圈裡的錯會一直丟
const CLIENT_ERR_WAIT = 800;      // 攢一下再送，一個錯常常連帶三四個
let clientErrs = [];
let clientErrTimer = 0;
let clientErrSending = false;     // 送的過程再丟錯就不要再送，不然會自己餵自己

function noteClientErr(kind, text, where) {
  if (clientErrs.length >= CLIENT_ERR_MAX) return;
  clientErrs.push({ kind: kind, text: String(text || '').slice(0, 1000),
                    where: String(where || '').slice(0, 300) });
  // 送的過程中丟的錯照收，只是不另外排一次（排了就會自己餵自己）。
  // 丟掉的話，送不出去的那一刻起這個頁面就等於全聾了。
  if (clientErrTimer || clientErrSending) return;
  clientErrTimer = setTimeout(flushClientErrs, CLIENT_ERR_WAIT);
}

function flushClientErrs() {
  clientErrTimer = 0;
  if (!clientErrs.length) return;
  if (clientErrSending) {                    // 上一批還在路上，等它
    clientErrTimer = setTimeout(flushClientErrs, CLIENT_ERR_WAIT);
    return;
  }
  // SAME_ORIGIN 與 apiUrl 都住在後面的檔案，載入到一半丟的錯會比它們早 ——
  // 讀不到就整批留著（下一條錯會再排一次），不要在這裡把它清掉。
  let url = '';
  try { url = SAME_ORIGIN ? apiUrl('/clienterr') : ''; } catch (e) { return; }
  const rows = clientErrs;
  clientErrs = [];
  // 直接開 HTML 檔的時候沒有後端可送。攢著只會一直佔著那 20 個位子，
  // 而且 apiUrl 在這種情況會指到 Ollama 主機 —— 那裡不該收到堆疊。
  if (!url) return;
  clientErrSending = true;
  // 送不出去也要把旗子放掉。卡在 true 的話後面所有的錯都不會再送出去，
  // 防遞迴的旗子就變成永久靜音。
  setTimeout(function () { clientErrSending = false; }, 5000);
  try {
    fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ errors: rows, url: location.pathname })
    }).catch(function () { /* 送不到就算了，這不是能拿來報錯的地方 */ })
      .then(function () { clientErrSending = false; });
  } catch (e) {
    clientErrSending = false;
  }
}

window.addEventListener('error', function (e) {
  // 資源載入失敗（img、script）的事件長得不一樣：沒有 error 物件，target 才是主角
  if (e.error || e.message) {
    noteClientErr('error', (e.error && e.error.stack) || e.message,
                  e.filename ? e.filename + ':' + e.lineno + ':' + e.colno : '');
  } else if (e.target && e.target.src) {
    noteClientErr('resource', '載入失敗：' + e.target.src, e.target.tagName);
  }
}, true);

window.addEventListener('unhandledrejection', function (e) {
  const r = e.reason;
  noteClientErr('unhandled', (r && (r.stack || r.message)) || String(r), '');
});

// 自己 catch 起來然後 console.error 的也算 —— 那些同樣是「壞了但畫面不說」。
const _consoleError = console.error;
console.error = function () {
  // 整段包起來：診斷用的東西弄壞呼叫它的人，就會把「有記下來的錯」
  // 變成「沒人接的錯」，連 F12 裡都看不到。
  try {
    noteClientErr('console.error',
      [].map.call(arguments, function (a) {
        if (a && a.stack) return a.stack;
        if (typeof a !== 'object' || a === null) return String(a);
        // 環狀結構（DOM 節點就是）會讓 stringify 直接丟例外
        try { return JSON.stringify(a); } catch (e) { return String(a); }
      }).join(' '), '');
  } catch (e) { /* 記不下來也要讓原本那行印出去 */ }
  _consoleError.apply(console, arguments);
};
