/* ==========================================================================
   桌面悬浮窗

   和手机版的通知栏常驻通知做的是同一件事：
   **一眼看到此刻在做什么、还剩多久。**

   省资源的做法也和手机版一致：**不轮询**。
   每次更新完，算出「下一次该变是什么时候」，然后只睡到那个时刻。

   ---------------------------------------------------------------------------
   分工：**判断在 Python，感知在 JS**

     JS 负责感知      —— 鼠标进来了/出去了、拖了多少、松手了
     Python 负责判断  —— 该不该缩回去、吸附到哪个边、窗口放哪

   为什么这么分？因为几何计算是纯数学，放 Python 里能写单元测试
   （见 app/dock.py，23 个测试）。放 JS 里就只能靠两只眼睛看了。
   ========================================================================== */

'use strict';

const KIND_COLOR = {
  SLEEP: '#78909c', MEAL: '#f59e0b', CLASS: '#2563eb', STUDY: '#8b5cf6',
  TRAIN: '#ef4444', FREE: '#22c55e', CHORE: '#14b8a6', TRANSIT: '#94a3b8',
};

/** 鼠标离开多久之后才缩回去。
 *  留这段缓冲是为了防止「手抖一下就从边上滑走」——
 *  实测没有它非常难用。 */
const COLLAPSE_DELAY_MS = 380;

const $ = (id) => document.getElementById(id);

let tickTimer = null;
let booted = false;
let dragging = false;
let moved = false;
let lastX = 0;
let lastY = 0;
let collapseTimer = null;

/** 已经按过「关闭」了。
 *
 *  为什么需要它：关闭是**不可逆**的，而且这个窗口很小，用户很容易
 *  连点两下。第二次点击发生在这个窗口被隐藏之前，会再跑一遍
 *  hide_ball()（那里面还会写一次配置文件）。
 *  关掉它的同时把按钮禁用，用户也就能立刻看出「点到了」。
 *
 *  ⚠️ 注意不能靠拖动那套 `moved` 逻辑来兜 —— 那条路是给「单击 vs 拖动」
 *  用的，和这里的重复提交是两回事。 */
let closing = false;

function jslog(msg) {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.log_error) {
      window.pywebview.api.log_error('[ball] ' + msg);
    }
  } catch (_) { /* 日志发不出去也不能再抛错 */ }
}

// ==========================================================================
//  布局切换（由 Python 调用）
// ==========================================================================

/**
 * 切布局。两个入口（applyDock / ballAnim）都走这里，避免两处逻辑走偏。
 *
 * @param {string}  edge  'left' / 'right' / 'top' / 'bottom'，空串表示自由浮动
 * @param {boolean} isTab true = 收起的小方框，false = 展开的卡片
 */
function setLayout(edge, isTab) {
  const b = document.body;

  // ⚠️ 这一行会把 body 上**所有** class 清掉，包括 ball-collapsing。
  // 所以 ballAnim 里必须先调它、再加 ball-collapsing，顺序不能反。
  b.className = isTab ? 'layout-tab' : 'layout-card';

  // dock-* 决定那个方向箭头朝哪边，只有贴边收起时才用得上
  if (edge) b.classList.add('dock-' + edge);
}

/**
 * Python 算完几何之后调这个，告诉网页该显示哪种形态。
 *
 * @param {string} edge       'left' / 'right' / 'top' / 'bottom'，空串表示自由浮动
 * @param {boolean} collapsed 是否已收起
 */
window.applyDock = function (edge, collapsed) {
  setLayout(edge, !!edge && !!collapsed);
};

/**
 * 收起/展开动画开始时调这个。
 *
 * 为什么动画期间**强制用卡片布局**：窗口尺寸是 Python 逐帧改的，
 * 而 `layout-tab` 会把整个面板 `display: none` —— 那样中途就没东西可淡出了，
 * 会看到卡片"啪"地消失、然后一条空框慢慢变窄。
 * 所以动画期间一直用卡片布局，靠 `ball-collapsing` 这个 class 驱动
 * 内容淡出（CSS 过渡，见 ball.html）。等 Python 走完最后一帧，
 * 它再调 applyDock 换成最终布局，那一下是"接上"的，不是硬切。
 *
 * @param {string}  edge       'left' / 'right' / 'top' / 'bottom'
 * @param {boolean} collapsing true = 正在收起（内容淡出），false = 正在展开（淡入）
 */
window.ballAnim = function (edge, collapsing) {
  setLayout(edge, false);
  document.body.classList.toggle('ball-collapsing', !!collapsing);
};

// ==========================================================================
//  刷新
// ==========================================================================

async function refresh() {
  let d;
  try {
    d = await pywebview.api.ball_state();
  } catch (e) {
    return;
  }
  if (!d || !d.ok) return;

  const now = d.now;

  if (!now) {
    $('dot').style.background = '#94a3b8';
    $('now').textContent = '今天没有安排';
    $('remain').innerHTML = '';
    $('next').textContent = `第 ${d.week} 周 · ${d.dayTypeLabel}`;
    clearTimeout(tickTimer);
    tickTimer = setTimeout(refresh, 60000);
    return;
  }

  $('dot').style.background = KIND_COLOR[now.kind] || '#94a3b8';
  $('now').textContent = now.title;

  const remain = now.remain;
  if (remain >= 60) {
    const h = Math.floor(remain / 60);
    const m = remain % 60;
    $('remain').innerHTML = m === 0 ? `${h}<i>小时</i>` : `${h}<i>时</i>${m}<i>分</i>`;
  } else {
    $('remain').innerHTML = `${remain}<i>分</i>`;
  }
  $('remain').style.color = KIND_COLOR[now.kind] || '#2563eb';

  const bits = [];
  bits.push(d.next ? `接着 ${d.next.time}　${d.next.title}` : '今天没有下一项了');
  if (now.place) bits.push(now.place);
  $('next').textContent = bits.join('　·　');

  // ---- 决定下一次什么时候再算 ----
  //
  // 这就是「不轮询」的全部：把下一次需要改变的时刻换算成毫秒，到点再醒。
  // 三个候选取最小：整分钟 / 当前这格结束的时刻 / 60 秒封顶（兜底）。
  const secNow = new Date().getSeconds();
  const delay = Math.min((60 - secNow) * 1000 + 60,
                         Math.max(1000, (remain * 60 - secNow) * 1000),
                         60000);
  clearTimeout(tickTimer);
  tickTimer = setTimeout(refresh, delay);
}

// ==========================================================================
//  鼠标进出 → 通知 Python 决定要不要滑出/缩回
// ==========================================================================

function cancelCollapse() {
  if (collapseTimer) {
    clearTimeout(collapseTimer);
    collapseTimer = null;
  }
}

function scheduleCollapse() {
  cancelCollapse();
  collapseTimer = setTimeout(() => {
    collapseTimer = null;
    // 拖动过程中绝不能缩回去 —— 那会把正在拖的东西从手底下抽走
    if (!dragging) pywebview.api.ball_hover(false);
  }, COLLAPSE_DELAY_MS);
}

function onPointerEnter() {
  cancelCollapse();
  pywebview.api.ball_hover(true);
}

function onPointerLeave() {
  if (dragging) return;          // 拖动中绝不缩回去
  scheduleCollapse();
}

/* --------------------------------------------------------------------------
   判断「鼠标还在不在这个窗口里」—— 两种写法都接上

   `mouseenter` / `mouseleave` 挂在 document 上是最直观的写法，
   但它在某些环境下不触发（尤其是无边框窗口 + WebView2 这种组合）。
   一旦漏掉，表现就是「鼠标走了它不缩回去」，一直杵在屏幕边上。

   所以再补一套 `mouseover` / `mouseout` + `relatedTarget` 判断：
   relatedTarget 为空表示指针来自/去往窗口之外。这是更老但更可靠的办法。

   两套都接，谁先触发算谁的。重复触发不要紧 ——
   enter 会取消定时器、leave 会重设定时器，幂等。
   -------------------------------------------------------------------------- */
document.addEventListener('mouseenter', onPointerEnter);
document.addEventListener('mouseleave', onPointerLeave);

document.addEventListener('mouseover', (e) => {
  if (!e.relatedTarget && !e.fromElement) onPointerEnter();
});

document.addEventListener('mouseout', (e) => {
  if (!e.relatedTarget && !e.toElement) onPointerLeave();
});

// ==========================================================================
//  拖动 / 点击
// ==========================================================================
//
// 不用 pywebview 的 easy_drag，因为它会让**整个窗口**都变成拖动区，
// 那样按钮就点不动了。这里自己实现：
//   · 带位移的按下-移动-松开 = 拖动窗口
//   · 没位移的按下-松开       = 当作一次点击

document.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;                 // 只响应左键
  if (e.target.closest('button')) return;     // 按钮自己处理点击
  dragging = true;
  moved = false;
  lastX = e.screenX;
  lastY = e.screenY;
  cancelCollapse();
  // 如果正贴边收起，让 Python 先把它弹出来再拖。
  // 否则用户按住的是一条 26 像素宽的细缝，拖起来手感很怪，
  // 而且拖到中间之前他一直看到的是一条线，很容易以为「拖没了」。
  pywebview.api.ball_drag_start();
  e.preventDefault();
});

document.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  const dx = e.screenX - lastX;
  const dy = e.screenY - lastY;
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
  moved = true;
  lastX = e.screenX;
  lastY = e.screenY;
  // 交给 Python 移动窗口 —— 网页改不了原生窗口的位置
  pywebview.api.move_ball(dx, dy);
});

document.addEventListener('mouseup', () => {
  if (!dragging) return;
  dragging = false;

  if (moved) {
    // 拖完了，让 Python 判断要不要吸附到边缘
    pywebview.api.ball_drag_end();
  } else {
    // 没移动 = 一次单击。收起的标签上单击 = 拉出来；展开的卡片上单击 = 打开主窗口
    if (document.body.classList.contains('layout-tab')) {
      pywebview.api.ball_slide_out();
    } else {
      pywebview.api.show_main();
    }
  }
});

$('btnMain').addEventListener('click', (e) => {
  e.stopPropagation();
  pywebview.api.show_main();
});

/* 关闭悬浮窗。
 *
 * 不只是把窗口藏起来 —— 连设置里的开关一起关掉（见 api.hide_ball），
 * 否则重启程序它又冒出来了，用户会以为没关掉。
 * 想再打开：主窗口「设置」页里的开关，或托盘菜单。
 */
$('btnClose').addEventListener('click', (e) => {
  e.stopPropagation();
  if (closing) return;          // 见下：防连点
  closing = true;
  $('btnClose').disabled = true;
  pywebview.api.hide_ball();
});

/** 提醒时跳一下，由 Python 调用 */
window.pulse = function () {
  const panel = $('panel');
  panel.classList.remove('pulse');
  void panel.offsetWidth;      // 强制重算样式，否则连续加同一个 class 不重放动画
  panel.classList.add('pulse');
};

/** 悬浮窗被重新打开时，把「关闭」按钮恢复可用。
 *
 *  为什么需要：这个窗口是**长期存在**的，不是关掉就销毁。
 *  用户在设置里关掉再打开，如果 `closing` 还停在 true、按钮还是禁用的，
 *  悬浮窗就变成"关不掉了"——而且没有任何报错，只是点了没反应。
 *
 *  由 Python 在每次 show() 之后调用（见 main._toggle_ball 和
 *  _on_settings_changed），和 applyDock 一样是「后端推给前端」的模式。 */
window.ballShown = function () {
  closing = false;
  const btn = $('btnClose');
  if (btn) btn.disabled = false;
};

// ==========================================================================
//  启动
// ==========================================================================
//
// 两条路都要接：pywebview 注入 API 需要一点时间，但页面可能加载得更快，
// 那样 pywebviewready 会在 addEventListener 注册之前就发完 ——
// 表现是窗口开着但一直显示「正在加载…」。

function start() {
  if (booted) return;
  booted = true;
  jslog('启动');
  refresh();
}

if (window.pywebview && window.pywebview.api) {
  start();
} else {
  window.addEventListener('pywebviewready', start);
}

window.addEventListener('error', (e) => jslog(`JS 错误：${e.message} @ ${e.lineno}`));
