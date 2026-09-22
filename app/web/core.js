/*
共享的基础工具 —— 选择器、日志、调后端、弹窗、建 DOM 节点。

纯工具，不碰业务；六个页面和 app.js 都会用到它们。
从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

const $ = (id) => document.getElementById(id);

/**
 * 把前端的关键节点记到后端的日志文件里。
 *
 * 为什么需要这个：前端一旦「静默不执行」，窗口照样开着，只是什么都不做 ——
 * 既没有弹窗也没有报错，完全无从下手。打点能直接告诉我们卡在哪一步。
 */
function jslog(msg) {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.log_error) {
      window.pywebview.api.log_error(msg);
    }
  } catch (_) { /* 日志发不出去也不能再抛错 */ }
}
// ==========================================================================
//  基础工具
// ==========================================================================

/** 调用后端，统一处理异常。后端抛异常时前端会收到 rejected promise，
 *  不接住的话界面上什么都不会发生 —— 那种「点了没反应」最难查。 */
async function call(method, ...args) {
  try {
    return await pywebview.api[method](...args);
  } catch (err) {
    toast('出错了：' + (err && err.message ? err.message : err));
    console.error(method, err);
    return { ok: false, message: String(err) };
  }
}

let toastTimer = null;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

/**
 * 弹窗。
 *
 * buttons: [{ label, value, primary, danger }]
 * 返回被点按钮的 value；如果用户按 ESC 或点遮罩，返回 null。
 */
function modal({ title, body, buttons }) {
  return new Promise((resolve) => {
    $('modalTitle').textContent = title;

    const bodyEl = $('modalBody');
    bodyEl.innerHTML = '';
    if (body instanceof Node) bodyEl.appendChild(body);
    else bodyEl.textContent = body || '';

    const actions = $('modalActions');
    actions.innerHTML = '';
    (buttons || [{ label: '知道了', value: true, primary: true }]).forEach((b) => {
      const btn = document.createElement('button');
      btn.className = 'btn' + (b.primary ? ' primary' : '') + (b.danger ? ' danger' : '');
      btn.textContent = b.label;
      btn.onclick = () => { close(b.value); };
      actions.appendChild(btn);
    });

    const mask = $('modalMask');
    const onMask = (e) => { if (e.target === mask) close(null); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };

    function close(value) {
      mask.classList.remove('show');
      mask.removeEventListener('click', onMask);
      document.removeEventListener('keydown', onKey);
      resolve(value);
    }

    mask.addEventListener('click', onMask);
    document.addEventListener('keydown', onKey);
    mask.classList.add('show');
  });
}

function confirmBox(title, body, okLabel, danger) {
  return modal({
    title,
    body,
    buttons: [
      { label: '取消', value: false },
      { label: okLabel, value: true, primary: !danger, danger: !!danger },
    ],
  }).then((v) => v === true);
}

/** 把后端的提示词塞进一个可滚动、可全选的框里 */
function promptDialog(title, text) {
  const box = document.createElement('div');
  box.className = 'prompt-box';
  box.textContent = text;
  return modal({
    title,
    body: box,
    buttons: [
      { label: '关闭', value: null },
      { label: '复制全部', value: 'copy', primary: true },
    ],
  }).then((v) => {
    if (v === 'copy') {
      navigator.clipboard.writeText(text).then(
        () => toast(`已复制（${text.length} 字）`),
        () => toast('复制失败，可以手动选中复制')
      );
    }
  });
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/**
 * 把一个输入控件包成「标签 + 控件」的一格，供 .field-grid 布局用。
 *
 * 课程表单和作息表单原本各写过一遍一模一样的这段，所以提到这里。
 * [full] 为真时占满整行（长输入框用）。
 *
 * 提上来是安全的：它只用到上面那个 el()，不捕获任何局部变量。
 */
function mk(labelText, node, full) {
  const wrap = el('div', full ? 'full' : '');
  wrap.appendChild(el('label', null, labelText));
  wrap.appendChild(node);
  return wrap;
}

/**
 * 建一个「第 1-10 节」的下拉框，并选中指定节次。
 *
 * 课程表单要两个（起止节次），作息表单也要两个（课表格子的节次范围）——
 * 原本是两处一模一样的循环。抽成「建一个」而不是「建一对」，
 * 调用方写起来更直白，也少一层 forEach。
 *
 * ⚠️ 这里的上限 10 是**写死**的，和后端 slots.MAX_NODE 是两份独立数据。
 * bootstrap 其实已经回传了 maxNode，界面却没用上 —— 属于已知待办，
 * 改动会牵连界面行为，不在本次结构调整范围内。
 */
function nodeSelect(selected) {
  const sel = document.createElement('select');
  sel.className = 'input';
  for (let i = 1; i <= 10; i++) {
    const o = document.createElement('option');
    o.value = i;
    o.textContent = '第 ' + i + ' 节';
    sel.appendChild(o);
  }
  sel.value = selected;
  return sel;
}
function toMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/**
 * 改起床时间的专用对话框。
 *
 * 为什么要单独做一个，而不是让用户去列表里改两格？
 * 因为「睡觉」和「起床、洗漱」是相邻的两格，一格一格改会互相报重叠，
 * **用户会陷入死锁** —— 每一句报错听起来都对，但就是改不动。
 * 一次性改两格，才是符合用户真实意图的操作单位。
 */
