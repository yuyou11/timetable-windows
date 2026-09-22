/* ==========================================================================
   时间规划表 · 电脑版 前端逻辑
   ==========================================================================

   和后端的分工：

     后端（Python）  算时间轴、读写文件、校验数据
     前端（这里）    只负责画界面、收集用户输入

   **所有判断「对不对」的逻辑都在后端**，前端不重复实现一遍。
   否则两边规则一旦不一致，就会出现「前端说能存、后端说不行」的怪现象。

   调用后端： pywebview.api.方法名(参数)  →  返回 Promise
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

jslog('app.js 已加载');

let STATE = {
  term: null,
  settings: null,
  courses: [],
  templates: null,
  activeType: 'A',
  weekOffset: 0,
};

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

/* ⚠️ 这张表 ball.js 里还有一份，**两边的值必须一模一样**。
 *
 * 为什么是复制而不是共享：前端没有模块系统（两个页面各自独立加载脚本），
 * 拿不到同一个常量。那就只能复制，然后**用一条测试盯着它** ——
 * 否则迟早漂移，而且漂移了看不出来。
 *
 * 实测漂移过一次：这边 CLASS 是 #3b82f6、TRANSIT 是 #cbd5e1，
 * 悬浮窗那边是 #2563eb 和 #94a3b8 —— 同一门课在两个窗口显示两种颜色。
 * 现在统一成**较深的那一套**。
 *
 * 为什么取深的那一套，而不是「主界面这一套」：悬浮窗上同一个色值既要当
 * 7px 小圆点的背景、又要当剩余时间的**文字颜色**，而文字在白色卡片上用
 * #cbd5e1 几乎看不见。深色在两个场景都能用，浅色只有主窗口能用 ——
 * **消除漂移时要选那个两边都能工作的值**，而不是简单选"主要的那个"。
 *
 * 核对它的测试：tests/test_ball_ui.py 里那条 KIND_COLOR 一致性用例。 */
const KIND_COLOR = {
  SLEEP: '#78909c', MEAL: '#f59e0b', CLASS: '#2563eb', STUDY: '#8b5cf6',
  TRAIN: '#ef4444', FREE: '#22c55e', CHORE: '#14b8a6', TRANSIT: '#94a3b8',
};

const KIND_LABEL = {
  SLEEP: '睡觉', MEAL: '吃饭', CLASS: '上课', STUDY: '自习',
  TRAIN: '运动', FREE: '自由', CHORE: '杂事', TRANSIT: '路上',
};

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

// ==========================================================================
//  页面切换
// ==========================================================================

function switchPage(name) {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.classList.toggle('active', b.dataset.page === name);
  });
  document.querySelectorAll('.page').forEach((p) => {
    p.classList.toggle('active', p.id === 'page-' + name);
  });

  if (name === 'today') renderToday();
  if (name === 'week') renderWeek();
  if (name === 'courses') renderCourses();
  if (name === 'templates') renderTemplates();
  if (name === 'settings') renderSettings();
}

// ==========================================================================
//  今天
// ==========================================================================

async function renderToday() {
  const t = await call('get_today');
  if (!t || !t.moments) return;

  $('todayDate').textContent = `${t.dateText} · ${t.weekday}`;
  $('todaySub').textContent = `第 ${t.week} 教学周 · ${t.dayTypeLabel}`;
  $('brandSub').textContent = `第 ${t.week} 周 · ${t.weekday}`;

  if (t.now) {
    $('nowTitle').textContent = t.now.title;
    const place = t.now.place ? ` · ${t.now.place}` : '';
    $('nowMeta').textContent =
      `${t.now.timeStart}–${t.now.timeEnd}${place} · ${t.now.durationText} · 还剩 ${t.now.remain} 分钟`;
    $('nowProgress').style.width = t.now.progress + '%';
  } else {
    $('nowTitle').textContent = '—';
    $('nowMeta').textContent = '';
    $('nowProgress').style.width = '0%';
  }

  $('nowNext').textContent = t.next
    ? `接着　${t.next.time}　${t.next.title}`
    : '今天没有下一项了';

  // ---- 明日预告 ----
  const tm = t.tomorrow;
  $('tomorrowHead').textContent = `${tm.weekday} · 第 ${tm.week} 周 · ${tm.dayTypeLabel}`;
  $('tomorrowWake').textContent = tm.wake || '—';
  $('tomorrowFirst').textContent = tm.first
    ? `接着　${tm.first.time}　${tm.first.title}` : '';
  if (tm.courseCount === 0) {
    $('tomorrowCourses').textContent = '明天没有课';
    $('tomorrowCourses').className = 'dim';
  } else {
    $('tomorrowCourses').textContent =
      `有 ${tm.courseCount} 节课：${tm.courseNames.join('、')}`;
    $('tomorrowCourses').className = '';
  }
  $('tomorrowLast').textContent = tm.last
    ? `最后一项　${tm.last.time}–${tm.last.end}　${tm.last.title}` : '';

  // ---- 时间线 ----
  const tl = $('timeline');
  tl.innerHTML = '';
  t.moments.forEach((m) => {
    const row = el('div', 'tl-row' + (m.isNow ? ' now' : ''));

    const time = el('div', 'tl-time');
    time.appendChild(el('div', 'tl-start', m.timeStart));
    time.appendChild(el('div', 'tl-end', m.timeEnd));

    const bar = el('div', 'tl-bar');
    bar.style.background = KIND_COLOR[m.kind] || 'var(--border)';

    const body = el('div', 'tl-body');
    body.appendChild(el('div', 'tl-title', m.title));
    if (m.note) body.appendChild(el('div', 'tl-note', m.note));

    const side = el('div', 'tl-side');
    if (m.durationText) side.appendChild(el('div', 'tl-chip', m.durationText));
    if (m.place) side.appendChild(el('div', 'tl-place', m.place));

    row.append(time, bar, body, side);
    tl.appendChild(row);
  });
}

// ==========================================================================
//  课表
// ==========================================================================

async function renderWeek() {
  const w = await call('get_week', STATE.weekOffset);
  if (!w || !w.rows) return;

  $('weekTitle').textContent = w.isCurrent ? `第 ${w.week} 教学周 · 本周` : `第 ${w.week} 教学周`;
  $('weekRange').textContent = w.rangeText;

  const hints = $('dayHints');
  hints.innerHTML = '';
  w.days.forEach((d) => {
    hints.appendChild(el('span', null, `${d.label}·${d.shortType}`));
  });

  const table = $('weekGrid');
  table.innerHTML = '';

  const thead = el('thead');
  const hrow = el('tr');
  hrow.appendChild(el('th', 'col-time', '节次'));
  w.days.forEach((d) => hrow.appendChild(el('th', null, d.label)));
  thead.appendChild(hrow);
  table.appendChild(thead);

  const tbody = el('tbody');
  w.rows.forEach((r) => {
    const tr = el('tr');

    const tdTime = el('td', 'col-time');
    tdTime.appendChild(el('div', null, r.label));
    tdTime.appendChild(el('div', null, r.startTime));
    tdTime.appendChild(el('div', null, r.endTime));
    tr.appendChild(tdTime);

    r.cells.forEach((c) => {
      const td = el('td', 'cell');
      const inner = el('div', 'cell-inner' + (c.name ? ' has' : ''));
      if (c.name) {
        inner.appendChild(el('div', 'cell-name', c.name));
        if (c.place) inner.appendChild(el('div', 'cell-place', c.place));
        inner.onclick = () => modal({
          title: c.name,
          body: `${c.nodesText}\n教室：${c.place || '未注明'}\n周次：${c.weeksText}`,
        });
      }
      td.appendChild(inner);
      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
}

// ==========================================================================
//  课程编辑
// ==========================================================================

async function loadCourses() {
  STATE.courses = await call('get_courses');
  return STATE.courses;
}

async function renderCourses() {
  await loadCourses();
  const list = $('courseList');
  list.innerHTML = '';

  if (!STATE.courses.length) {
    list.appendChild(el('p', 'hint', '课表是空的。点右上角「添加课程」，或者到「导入导出」里导入一份。'));
    return;
  }

  STATE.courses.forEach((c) => {
    const row = el('div', 'row' + (c.enabled ? '' : ' off'));

    const sw = el('label', 'switch');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = c.enabled;
    cb.onchange = async () => {
      const r = await call('toggle_course', c.index, cb.checked);
      if (r.ok) { STATE.courses = r.courses; renderCourses(); }
      else toast(r.message);
    };
    sw.appendChild(cb);
    sw.appendChild(el('span'));

    const main = el('div', 'row-main');
    main.appendChild(el('div', 'row-title', c.name));
    main.appendChild(el('div', 'row-sub',
      `${c.weekday} ${c.nodesText} · ${c.weeksText}${c.place ? ' · ' + c.place : ''}`));

    const actions = el('div', 'row-actions');
    const edit = el('button', 'btn mini', '编辑');
    edit.onclick = () => courseDialog(c);
    const del = el('button', 'btn mini danger', '删除');
    del.onclick = async () => {
      if (!await confirmBox('删除课程', `确定删除「${c.name}」吗？`, '删除', true)) return;
      const r = await call('delete_course', c.index);
      if (r.ok) { STATE.courses = r.courses; renderCourses(); toast(r.message); }
      else toast(r.message);
    };
    actions.append(edit, del);

    row.append(sw, main, actions);
    list.appendChild(row);
  });
}

/** 课程的新增 / 编辑表单 */
function courseDialog(existing) {
  const isNew = !existing;
  const c = existing || {
    name: '', dayOfWeek: 1, startNode: 1, endNode: 2, weeks: '*', place: '', enabled: true,
  };

  const form = el('div', 'field-grid');

  const inName = document.createElement('input');
  inName.className = 'input';
  inName.value = c.name;
  inName.placeholder = '例如：高等数学A(Ⅰ)';

  const inDay = document.createElement('select');
  inDay.className = 'input';
  ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].forEach((label, i) => {
    const o = document.createElement('option');
    o.value = i + 1;
    o.textContent = label;
    if (i + 1 === c.dayOfWeek) o.selected = true;
    inDay.appendChild(o);
  });

  const inStart = nodeSelect(c.startNode);
  const inEnd = nodeSelect(c.endNode);

  const inWeeks = document.createElement('input');
  inWeeks.className = 'input';
  inWeeks.value = Array.isArray(c.weeks) && c.weeks.length === 19
    ? '*' : (existing ? c.weeksText.replace(' 周', '') : '*');
  inWeeks.placeholder = '例如 2-4,6-17 或 3-17/2 或 *';

  const inPlace = document.createElement('input');
  inPlace.className = 'input';
  inPlace.value = c.place || '';
  inPlace.placeholder = '例如：教一-101';

  form.append(
    mk('课程名', inName, true),
    mk('星期', inDay),
    mk('起始节次', inStart),
    mk('结束节次', inEnd),
    mk('上课周次', inWeeks),
    mk('教室', inPlace),
  );

  const hint = el('p', 'hint');
  hint.textContent = '周次写法："3" 单周次，"2-4" 区间，"3-17/2" 单周，"2-16/2" 双周，"2-4,6-17" 分段，"*" 全学期。中文逗号也能识别。';
  form.appendChild(hint);

  return modal({
    title: isNew ? '添加课程' : '编辑课程',
    body: form,
    buttons: [
      { label: '取消', value: null },
      { label: '保存', value: 'save', primary: true },
    ],
  }).then(async (v) => {
    if (v !== 'save') return;
    const payload = {
      name: inName.value,
      dayOfWeek: Number(inDay.value),
      startNode: Number(inStart.value),
      endNode: Number(inEnd.value),
      weeks: inWeeks.value,
      place: inPlace.value,
      enabled: c.enabled,
    };
    const r = await call('save_course', payload, isNew ? -1 : c.index);
    if (r.ok) { STATE.courses = r.courses; renderCourses(); toast(r.message); }
    else {
      // 校验失败时把后端的原话显示出来，并重新打开表单，
      // 让用户不用从头再填一遍 —— 这是「报错要能直接照着改」的界面版本
      await modal({ title: '保存失败', body: r.message });
      courseDialog({ ...payload, weeksText: payload.weeks, index: c.index });
    }
  });
}

// ==========================================================================
//  作息编辑
// ==========================================================================

async function loadTemplates() {
  STATE.templates = await call('get_templates');
  return STATE.templates;
}

async function renderTemplates() {
  const t = STATE.templates || await loadTemplates();
  if (!t || !t.types) return;

  // ---- 日型标签 ----
  const tabs = $('typeTabs');
  tabs.innerHTML = '';
  t.types.forEach((type) => {
    const btn = el('button', 'type-tab' + (type.key === STATE.activeType ? ' active' : ''), type.label);
    if (type.isCustom) btn.appendChild(el('span', 'dot'));
    btn.onclick = () => { STATE.activeType = type.key; renderTemplates(); };
    tabs.appendChild(btn);
  });

  const active = t.types.find((x) => x.key === STATE.activeType) || t.types[0];
  STATE.activeType = active.key;

  $('typeInfo').textContent = active.wake
    ? `这一天 ${active.wake} 起床${active.isCustom ? '（已自定义，带圆点的标签表示改过）' : '（内置模板）'}`
    : '这一天没有上午的睡眠段，界面上不会显示起床时间';

  // ---- 格子列表 ----
  const list = $('blockList');
  list.innerHTML = '';
  active.blocks.forEach((b, i) => {
    const row = el('div', 'row');

    const dot = el('div', 'kind-dot');
    dot.style.background = KIND_COLOR[b.kind] || 'var(--border)';

    const time = el('div', 'row-time');
    time.appendChild(el('div', null, b.start));
    time.appendChild(el('div', null, b.end));

    const main = el('div', 'row-main');
    main.appendChild(el('div', 'row-title', b.title));
    const bits = [KIND_LABEL[b.kind] || b.kind, b.durationText];
    if (b.nodes) bits.push(`第 ${b.nodes[0]}-${b.nodes[1]} 节课表格子`);
    if (b.note) bits.push(b.note);
    main.appendChild(el('div', 'row-sub', bits.filter(Boolean).join(' · ')));

    const actions = el('div', 'row-actions');
    const edit = el('button', 'btn mini', '编辑');
    edit.onclick = () => blockDialog(active.key, b, i);
    const del = el('button', 'btn mini danger', '删除');
    del.onclick = async () => {
      const r = await call('delete_template_block', active.key, i);
      if (r.ok) { STATE.templates = r.templates; renderTemplates(); toast(r.message); }
      else toast(r.message);
    };
    actions.append(edit, del);

    row.append(dot, time, main, actions);
    list.appendChild(row);
  });
}

function blockDialog(dayType, existing, index) {
  const isNew = existing === null || existing === undefined;
  const b = existing || { start: '08:00', end: '09:00', title: '', note: '', kind: 'CHORE', nodes: null };

  const form = el('div', 'field-grid');

  const inTitle = document.createElement('input');
  inTitle.className = 'input';
  inTitle.value = b.title;
  inTitle.placeholder = '这一格做什么，例如：早餐';

  const inStart = document.createElement('input');
  inStart.className = 'input';
  inStart.type = 'time';
  inStart.value = b.start;

  const inEnd = document.createElement('input');
  inEnd.className = 'input';
  inEnd.type = 'time';
  inEnd.value = b.end === '24:00' ? '23:59' : b.end;

  const inKind = document.createElement('select');
  inKind.className = 'input';
  Object.keys(KIND_LABEL).forEach((k) => {
    const o = document.createElement('option');
    o.value = k;
    o.textContent = KIND_LABEL[k] + `（${k}）`;
    if (k === b.kind) o.selected = true;
    inKind.appendChild(o);
  });

  const inNote = document.createElement('input');
  inNote.className = 'input';
  inNote.value = b.note || '';
  inNote.placeholder = '补充说明，可留空';

  const inNodesOn = document.createElement('input');
  inNodesOn.type = 'checkbox';
  inNodesOn.checked = !!b.nodes;
  const nodesWrap = el('label', null);
  nodesWrap.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:13px;color:var(--text-dim)';
  nodesWrap.append(inNodesOn, document.createTextNode('这是课表格子（有课时自动换成课程名和教室）'));

  const inNodeA = nodeSelect(b.nodes ? b.nodes[0] : 1);
  const inNodeB = nodeSelect(b.nodes ? b.nodes[1] : 2);

  form.append(
    mk('这一格做什么', inTitle, true),
    mk('开始', inStart),
    mk('结束', inEnd),
    mk('性质（决定颜色）', inKind),
    mk('备注', inNote, true),
    mk('', nodesWrap, true),
    mk('占位节次 起', inNodeA),
    mk('占位节次 止', inNodeB),
  );

  const hint = el('p', 'hint');
  hint.textContent = '结束时间填 23:59 表示到这一天结束（存储时会写成 24:00）。同一套模板里格子之间不能重叠。';
  form.appendChild(hint);

  return modal({
    title: isNew ? '添加一格' : '编辑一格',
    body: form,
    buttons: [
      { label: '取消', value: null },
      { label: '保存', value: 'save', primary: true },
    ],
  }).then(async (v) => {
    if (v !== 'save') return;
    const payload = {
      title: inTitle.value,
      start: inStart.value,
      // 后端认识 "24:00"，但 <input type="time"> 不接受这个值，
      // 所以界面上用 23:59 代替，到这里再换回去。
      // 顺带处理：如果用户选的结束时间早于开始时间，按「到当天结束」理解
      end: inEnd.value,
      note: inNote.value,
      kind: inKind.value,
      nodes: inNodesOn.checked ? [Number(inNodeA.value), Number(inNodeB.value)] : null,
    };
    const startMin = toMinutes(payload.start);
    const endMin = toMinutes(payload.end);
    if (endMin <= startMin) payload.end = '24:00';

    const r = await call('save_template_block', dayType, payload, isNew ? -1 : index);
    if (r.ok) { STATE.templates = r.templates; renderTemplates(); toast(r.message); }
    else {
      await modal({ title: '保存失败', body: r.message });
      blockDialog(dayType, { ...payload, start: payload.start }, index);
    }
  });
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
async function wakeDialog() {
  const t = STATE.templates || await loadTemplates();
  const type = t.types.find((x) => x.key === STATE.activeType);
  if (!type || !type.wake) {
    await modal({ title: '改不了', body: '这套模板里没有上午的睡眠段，所以没有「起床时间」这个概念。' });
    return;
  }

  const form = el('div', 'field-row');
  form.appendChild(el('label', null, '新的起床时间'));
  const input = document.createElement('input');
  input.type = 'time';
  input.className = 'input';
  input.value = type.wake;
  form.appendChild(input);

  const note = el('p', 'hint');
  note.textContent =
    '会同时改动「睡觉」的结束时刻和后面紧跟着的那一格（起床、洗漱），' +
    '两格之间的时长保持不变。比一格一格改更省事，也不会撞上重叠检查。';
  form.appendChild(note);

  const v = await modal({
    title: `改「${type.label}」的起床时间`,
    body: form,
    buttons: [
      { label: '取消', value: null },
      { label: '保存', value: 'save', primary: true },
    ],
  });
  if (v !== 'save') return;

  const r = await call('shift_wake_time', STATE.activeType, input.value);
  if (r.ok) {
    STATE.templates = r.templates;
    renderTemplates();
    toast(r.message);
  } else {
    await modal({ title: '改不了', body: r.message });
  }
}

// ==========================================================================
//  导入导出
// ==========================================================================

// includeScheduleConfig = true 时带上**完整的作息配置**：
// 展开的六套模板 + 启用的日型策略。两者必须一起带走 ——
// 只带模板不带日型的话，别人导入后拿到的日型和你的不一样
// （同一份模板，你只启用四种，他却六种全开）。
async function exportFile(includeScheduleConfig) {
  const r = await call('export_to_file', includeScheduleConfig);
  if (r.cancelled) return;
  toast(r.message);
}

async function doImport() {
  const r = await call('import_from_file');
  if (r.cancelled) return;
  if (!r.ok) { await modal({ title: '导入失败', body: r.message }); return; }

  const p = r.preview;
  const lines = [];

  lines.push(p.hasTerm
    ? `学期：${p.termName}\n起始：${p.termStart}（周一）\n总周数：${p.totalWeeks}`
    : '学期：文件里没写，保持不变');

  lines.push(p.coursesUnchanged
    ? '课程：文件里没写，保持不变'
    : `课程：${p.courseCount} 门`);

  if (p.templatesUnchanged) {
    lines.push('作息模板：文件里没写，保持不变');
  } else {
    const t = p.templateLines
      .map((x) => `· ${x.key}${x.wake ? '　' + x.wake + ' 起床' : ''}`)
      .join('\n');
    lines.push(`作息模板：将替换 ${p.templateCount} 种日型\n${t}`);
  }

  // 启用了哪些日型（v3）。带上起床时间 —— 这是用户最想核对的那件事：
  // 「我说好 7 点起，到底给我排的几点」。
  if (p.dayTypesUnchanged) {
    lines.push('启用日型：文件里没写，保持不变');
  } else {
    const d = p.dayTypeLines
      .map((x) => `· ${x.key}${x.wake ? '　' + x.wake + ' 起床' : ''}`)
      .join('\n');
    lines.push(
      `启用日型：${p.dayTypeEnabled.length} 种\n${d}\n` +
      `没启用的日子会回落到 ${p.dayTypeFallback}`
    );
  }

  if (p.warnings.length) {
    lines.push(`\n⚠️ ${p.warnings.length} 条提醒：\n` +
      p.warnings.slice(0, 5).map((w) => '· ' + w).join('\n'));
  }

  const buttons = [{ label: '取消', value: null }];
  if (!p.coursesUnchanged) {
    buttons.push({ label: '合并', value: 'merge' });
    buttons.push({ label: '替换现有课表', value: 'replace', primary: true });
  } else {
    // 文件里没带课程时，「替换 / 合并」没有意义 ——
    // 给两个长得一样的按钮只会让人以为自己在做选择
    buttons.push({ label: '应用', value: 'merge', primary: true });
  }

  const choice = await modal({ title: '确认导入', body: lines.join('\n\n'), buttons });
  if (!choice) return;

  const res = await call('confirm_import', choice);
  toast(res.message);
  if (res.ok) {
    await refreshAll();
  }
}

async function showPrompt(kind, title) {
  const r = await call('get_ai_prompt', kind);
  if (!r.ok) { toast('取提示词失败'); return; }
  await promptDialog(title, r.text);
}

// ==========================================================================
//  设置
// ==========================================================================

async function renderSettings() {
  const t = await call('bootstrap');
  STATE.term = t.term;
  STATE.settings = t.settings;

  $('swBall').checked = t.settings.ballEnabled;
  $('leadValue').textContent = t.settings.remindLead === 0 ? '关闭' : t.settings.remindLead + ' 分钟';

  $('termName').value = t.term.name;
  $('termStart').value = t.term.start;
  $('termWeeks').value = t.term.totalWeeks;
  $('termHint').textContent =
    `今天是第 ${t.term.todayWeek} 周。起始日不是周一也没关系，保存时会自动对齐到那一周的周一。`;

  await renderDayTypes();

  updateStatusPill(t.settings);
}

/**
 * 日型勾选列表。
 *
 * 每个选项旁边显示**这一型的起床时间** —— 用户勾选时才看得出代价：
 * 「把工作日全勾成 A 型」意味着没早八的日子也 06:55 起床，
 * 而那正是这个程序最该帮人避免的事。
 */
async function renderDayTypes() {
  const d = await call('get_day_types');
  if (!d || !d.types) return;

  const box = $('dayTypeList');
  box.innerHTML = '';

  d.types.forEach((t) => {
    const row = el('label', 'daytype-item');

    const cb = el('input');
    cb.type = 'checkbox';
    cb.dataset.key = t.key;
    cb.checked = t.enabled;
    // 勾选变化时重建下拉框：下拉里只该出现**已勾选**的日型
    cb.onchange = () => {
      if (cb.checked) {
        const opt = el('option', null, `${t.label}${t.wake ? '　' + t.wake + ' 起' : ''}`);
        opt.value = t.key;
        $('dayTypeFallback').appendChild(opt);
      } else {
        const opt = [...$('dayTypeFallback').options].find((o) => o.value === t.key);
        if (opt) opt.remove();
      }
      updateDayTypeHint();
    };
    row.appendChild(cb);

    const text = el('div', 'daytype-text');
    text.appendChild(el('div', 'daytype-name', t.label));
    if (t.wake) text.appendChild(el('div', 'daytype-wake', t.wake + ' 起床'));
    row.appendChild(text);

    if (t.isFallback) row.appendChild(el('span', 'daytype-badge', '当前回落'));
    box.appendChild(row);
  });

  // 下拉框只列**已勾选**的日型 —— 让用户选一个没勾的，
  // 等于给了一个自相矛盾的控件（后端也会拦下来）
  const sel = $('dayTypeFallback');
  sel.innerHTML = '';
  d.types.filter((t) => t.enabled).forEach((t) => {
    const opt = el('option', null, `${t.label}${t.wake ? '　' + t.wake + ' 起' : ''}`);
    opt.value = t.key;
    if (t.key === d.fallback) opt.selected = true;
    sel.appendChild(opt);
  });

  updateDayTypeHint();
}

function updateDayTypeHint() {
  const n = document.querySelectorAll('#dayTypeList input:checked').length;
  const hint = $('dayTypeHint');
  if (n === 0) {
    hint.textContent = '至少要勾一种 —— 全都不勾的话，每一天都会落到「回落到」那一种上。';
    hint.classList.add('warn');
  } else {
    hint.textContent = `已勾选 ${n} 种。没勾的日型会自动落到「回落到」选的那一套。`;
    hint.classList.remove('warn');
  }
}

function updateStatusPill(s) {
  const pill = $('statusPill');
  if (!s.enabled && !s.ballEnabled) {
    pill.textContent = '已关闭';
    pill.className = 'pill';
  } else if (s.ballEnabled) {
    pill.textContent = '悬浮窗运行中';
    pill.className = 'pill on';
  } else {
    pill.textContent = '提醒已开启';
    pill.className = 'pill on';
  }
}

// ==========================================================================
//  事件绑定
// ==========================================================================

function bindEvents() {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.onclick = () => switchPage(b.dataset.page);
  });

  $('btnRefreshToday').onclick = renderToday;

  // 课表翻周
  $('prevWeek').onclick = () => { STATE.weekOffset--; renderWeek(); };
  $('nextWeek').onclick = () => { STATE.weekOffset++; renderWeek(); };
  $('curWeek').onclick = () => { STATE.weekOffset = 0; renderWeek(); };

  // 课程
  $('addCourse').onclick = () => courseDialog(null);
  $('clearCourses').onclick = async () => {
    if (!await confirmBox('清空所有课程',
      '会删掉全部课程，课表变空。\n\n建议先「导出课表」备份 —— 清空之后只能靠导入恢复，内置课表可以用「恢复内置」找回来。',
      '确认清空', true)) return;
    const r = await call('clear_courses');
    if (r.ok) { STATE.courses = []; renderCourses(); toast(r.message); }
  };
  $('resetCourses').onclick = async () => {
    if (!await confirmBox('恢复内置课表', '会用内置的示例课表替换当前课表（示例大学 2026 级·大一上，19 门课）。', '恢复')) return;
    const r = await call('reset_courses');
    if (r.ok) { STATE.courses = r.courses; renderCourses(); toast(r.message); }
  };

  // 作息
  $('addBlock').onclick = () => blockDialog(STATE.activeType, null, -1);
  $('changeWake').onclick = () => wakeDialog();
  $('resetTemplates').onclick = async () => {
    if (!await confirmBox('恢复内置作息模板',
      '会丢弃你改过的作息，重新使用内置的六套模板。\n\n课表不受影响。', '恢复')) return;
    const r = await call('reset_templates');
    if (r.ok) { STATE.templates = r.templates; renderTemplates(); toast(r.message); }
  };

  // 数据
  $('exportSimple').onclick = () => exportFile(false);
  $('exportFull').onclick = () => exportFile(true);
  $('copyJson').onclick = async () => {
    // ⚠️ 用 hasCustomScheduleConfig，**不能用 hasCustomTemplates**。
    // 后者在「只改了日型、没改模板」时是 false，复制出来的 JSON 不含 dayTypes ——
    // 用户那份日型设置就静默消失了（手机版踩过这个坑）。
    const r = await call('copy_json',
      STATE.settings && STATE.settings.hasCustomScheduleConfig);
    if (r.ok) {
      navigator.clipboard.writeText(r.text).then(
        () => toast(r.message),
        () => promptDialog('复制失败，请手动复制', r.text)
      );
    }
  };
  $('importFile').onclick = doImport;
  $('openFolder').onclick = async () => { const r = await call('open_data_folder'); if (r.ok) toast('已打开'); };
  $('copyPromptCourse').onclick = () => showPrompt('course', '课表提示词');
  $('copyPromptTemplate').onclick = () => showPrompt('template', '作息模板提示词');
  $('copyPromptFix').onclick = () => showPrompt('fix', '报错修复提示词');

  // 设置
  $('swBall').onchange = async (e) => {
    const r = await call('set_ball_enabled', e.target.checked);
    STATE.settings = r;
    updateStatusPill(r);
    toast(e.target.checked ? '悬浮窗已开启' : '悬浮窗已关闭');
  };

  document.querySelectorAll('[data-lead]').forEach((btn) => {
    btn.onclick = async () => {
      const steps = [0, 5, 10, 15, 20, 30];
      const cur = STATE.settings ? STATE.settings.remindLead : 0;
      let idx = steps.indexOf(cur);
      if (idx < 0) idx = 0;
      idx = Math.max(0, Math.min(steps.length - 1, idx + Number(btn.dataset.lead)));
      const r = await call('set_remind_lead', steps[idx]);
      STATE.settings = r;
      $('leadValue').textContent = r.remindLead === 0 ? '关闭' : r.remindLead + ' 分钟';
      updateStatusPill(r);
    };
  });

  $('saveTerm').onclick = async () => {
    const r = await call('set_term',
      $('termName').value, $('termStart').value, Number($('termWeeks').value));
    toast(r.message);
    if (r.ok) {
      STATE.term = r.term;
      // 改了学期，课表页的周次全变了，位置也要重置
      STATE.weekOffset = 0;
      await refreshAll();
    }
  };

  // 日型策略
  $('saveDayTypes').onclick = async () => {
    const enabled = [...document.querySelectorAll('#dayTypeList input:checked')]
      .map((cb) => cb.dataset.key);
    const r = await call('save_day_types', enabled, $('dayTypeFallback').value);
    if (!r.ok) {
      // 校验失败（比如一种都没勾）用弹窗而不是 toast ——
      // 报错里写了「该怎么办」，一闪而过的提示读不完
      await modal({ title: '保存失败', body: r.message, buttons: [{ label: '知道了', value: 1, primary: true }] });
      return;
    }
    toast(r.message);
    await renderDayTypes();
    await refreshAll();
  };

  $('resetDayTypes').onclick = async () => {
    if (!await confirmBox('恢复默认日型',
      '会改成默认那四种：有早八的 A 型、没早八的 B 型、周六、周日。\n\n' +
      '训练日（周二力量A、周四力量B）的细分会被关掉 —— '
      + '它们来自某一份具体规划表，不是通用规律。\n\n课表和模板都不受影响。',
      '恢复')) return;
    const r = await call('reset_day_types');
    if (r.ok) {
      if (r.settings) { STATE.settings = r.settings; updateStatusPill(r.settings); }
      await renderDayTypes();
      await refreshAll();
      toast(r.message);
    }
  };
}

// ==========================================================================
//  启动
// ==========================================================================

async function refreshAll() {
  await renderToday();
  const t = await call('bootstrap');
  STATE.term = t.term;
  STATE.settings = t.settings;
  updateStatusPill(t.settings);
}

let booted = false;

async function boot() {
  // 防止两条触发路径都跑一遍（下面用的是「两个都接」的写法）
  if (booted) return;
  booted = true;
  jslog('boot() 开始');

  const data = await call('bootstrap');
  jslog('bootstrap 返回：' + (data ? ('term=' + (data.term ? data.term.name : '无')) : '空'));
  if (!data || !data.term) return;

  STATE.term = data.term;
  STATE.settings = data.settings;
  STATE.templates = null;   // 让作息页第一次打开时去后端取

  $('brandSub').textContent = `第 ${data.term.todayWeek} 周`;
  updateStatusPill(data.settings);

  bindEvents();
  switchPage('today');
  jslog('界面初始化完成');

  // 首次启动：问一句用内置示例还是从空白开始。
  // 这个程序是要发给同学用的，别的学校的同学装上之后会看到一堆跟自己无关的课 ——
  // 给一个明确的开场选择，比让他自己去设置里找要友好得多。
  if (data.isFirstLaunch) {
    const blank = await modal({
      title: '第一次使用',
      body:
        '这个程序内置了一份示例课表（示例大学 2026 级 · 大一上），' +
        '包含 19 门课和大学作息模板。\n\n' +
        '它不是你的课表，只是一个能看清界面的样例 —— 建议选「从空白开始」，' +
        '然后到「导入导出」里导入自己的课表，或者用 AI 提示词生成一份。\n\n' +
        '选错了也没关系，随时可以清空或恢复。',
      buttons: [
        { label: '用内置示例', value: false },
        { label: '从空白开始', value: true, primary: true },
      ],
    });
    if (blank) {
      await call('clear_courses');
    }
    await call('mark_launched');
    await refreshAll();
  }

  // 每分钟刷新一次「此刻」——只在界面开着时才跑，关掉窗口就没了。
  // 这和手机版的思路一样：**不做 7×24 的后台轮询**。
  setInterval(() => {
    if ($('page-today').classList.contains('active')) renderToday();
  }, 60000);
}

/* --------------------------------------------------------------------------
   启动时机：两条路都要接上

   pywebview 注入 API 需要一点时间，所以通常要等 pywebviewready 事件。
   但这里有个**竞态**：如果页面加载得比 API 注入还快，事件会在
   addEventListener 注册之前就发完，于是 boot() 永远等不到 ——
   表现是「窗口开着，但界面一片空白、什么数据都没有」。

   打包成 exe 之后页面是从本地磁盘读的，比开发时更快，这个竞态更容易触发。

   所以两个都接：已经注入好了就直接启动，还没好就等事件。
   这是处理「一次性事件 + 可能已经错过」的标准写法。
   -------------------------------------------------------------------------- */
if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener('pywebviewready', boot);
}

/* --------------------------------------------------------------------------
   出错时留下痕迹

   前端的异常默认只出现在开发者工具里。用户看不到，我们也拿不到 ——
   于是「点了没反应」这种问题就完全无从查起。

   这里把 JS 的报错转发给 Python，写进日志文件。
   -------------------------------------------------------------------------- */
window.addEventListener('error', (e) => {
  try {
    pywebview.api.log_error(`${e.message} @ ${e.filename}:${e.lineno}`);
  } catch (_) { /* 连日志都发不出去就算了，不能再抛一个错 */ }
});

window.addEventListener('unhandledrejection', (e) => {
  try {
    const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
    pywebview.api.log_error(`未处理的 Promise 拒绝：${msg}`);
  } catch (_) { /* 同上 */ }
});
