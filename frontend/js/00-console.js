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
  if (clientErrSending || clientErrs.length >= CLIENT_ERR_MAX) return;
  clientErrs.push({ kind: kind, text: String(text || '').slice(0, 1000),
                    where: String(where || '').slice(0, 300) });
  if (clientErrTimer) return;
  clientErrTimer = setTimeout(flushClientErrs, CLIENT_ERR_WAIT);
}

function flushClientErrs() {
  clientErrTimer = 0;
  const rows = clientErrs;
  clientErrs = [];
  // 直接開 HTML 檔的時候沒有後端可送。apiUrl 住在 04-api.js，
  // 載入到一半丟的錯會比它早 —— 所以這裡問一次，不要假設它在。
  if (!rows.length || typeof apiUrl !== 'function') return;
  clientErrSending = true;
  try {
    fetch(apiUrl('/clienterr'), {
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
  noteClientErr('console.error',
    [].map.call(arguments, function (a) {
      return (a && a.stack) || (typeof a === 'object' ? JSON.stringify(a) : String(a));
    }).join(' '), '');
  _consoleError.apply(console, arguments);
};
