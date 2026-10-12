/*
「作息编辑」页 —— 时间轴 + 列表双视图，编辑会话式保存（新一代交互）。

交互模型（v4.1 重设计，旧版是「胶囊标签 + 弹窗逐格编辑」）：

  · 左侧 24 小时竖向时间轴：格子按分钟比例着色，空档看得见，
    上下边缘拖拽调时间、空档处拖出新格子、起床线可拖；
  · 右侧列表：点行展开行内编辑器，不再逐格弹窗；
  · 日型标签右侧的小点是删除入口：悬停变红 ×，点击弹确认卡片（休息日除外）；
  · 编辑会话：所有改动先落在本地工作副本，点「保存」整套提交
    （save_template_blocks），可撤销（快照栈）、可放弃；
    dirty 时切日型/离开页面会拦截确认。

时间单位约定：会话内部一律用**分钟**（0–1440，1440 = 24:00），
渲染和提交时才转 "HH:mm"。后端给的 blocks 自带 startMin/endMin，
正好直接用。

   ========================================================================== */

'use strict';

const TL_PX_PER_MIN = 1008 / 1440;   // 时间轴总高 1008px（24h × 42px/h）
const TL_MIN_DURATION = 5;          // 一格最短 5 分钟（也是拖拽/新建的吸附粒度）

// ==========================================================================
//  编辑会话
// ==========================================================================

let edit = null;
// {
//   key: '1',            // 正在编辑的日型 id
//   blocks: [...],       // 工作副本，始终按 start 排序，分钟单位
//   undo: [ [blocks快照], ... ],
//   dirty: false,
//   selected: null,      // 选中的 block 对象引用
// }

function deepBlocks(blocks) {
  return blocks.map((b) => ({ ...b, nodes: b.nodes ? [b.nodes[0], b.nodes[1]] : null }));
}

function fmtHHMM(m) {
  const h = Math.floor(m / 60), mm = m % 60;
  return (h === 24 ? '24' : String(h).padStart(2, '0')) + ':' + String(mm).padStart(2, '0');
}

/** 当前日型的数据（来自后端的那份，不是工作副本） */
function typeData(key) {
  const t = STATE.templates;
  return t && t.types ? t.types.find((x) => x.key === key) : null;
}

/** 建立会话（切日型 / 后端数据整体刷新后调用）。已有同 key 会话则复用。 */
function ensureSession(key) {
  if (edit && edit.key === key) return edit;
  const data = typeData(key);
  if (!data) return null;
  edit = {
    key,
    blocks: data.blocks.map((b) => ({
      start: b.startMin, end: b.endMin, title: b.title,
      note: b.note || '', kind: b.kind, nodes: b.nodes ? [b.nodes[0], b.nodes[1]] : null,
    })).sort((a, b) => a.start - b.start),
    undo: [],
    dirty: false,
    selected: null,
  };
  return edit;
}

function tplInvalidateSession() {
  edit = null;
}

function snapshot() {
  edit.undo.push(deepBlocks(edit.blocks));
  if (edit.undo.length > 50) edit.undo.shift();
  edit.dirty = true;
}

/** 所有改动的唯一入口：先拍快照，再改，再刷新界面 */
function mutate(fn) {
  snapshot();
  fn(edit.blocks);
  edit.blocks.sort((a, b) => a.start - b.start);
  renderEditor();
}

function undoEdit() {
  if (!edit || !edit.undo.length) return;
  edit.blocks = edit.undo.pop();
  edit.selected = null;
  if (!edit.undo.length) edit.dirty = false;
  renderEditor();
}

async function discardEdit() {
  if (!edit || !edit.dirty) return;
  const ok = await confirmBox('放弃改动',
    '「' + currentTypeLabel() + '」有未保存的改动，放弃后恢复到上次保存的样子。', '放弃');
  if (!ok) return;
  edit = null;
  ensureSession(STATE.activeType);
  renderEditor();
}

// ==========================================================================
//  冲突 / 空档
// ==========================================================================

function computeIssues(blocks) {
  const conflicts = new Set();
  const gaps = [];
  let cursor = 0;
  blocks.forEach((b, i) => {
    if (b.start < cursor) { conflicts.add(i); conflicts.add(i - 1); }
    if (b.start > cursor) gaps.push({ start: cursor, end: b.start });
    cursor = Math.max(cursor, b.end);
  });
  if (cursor < 1440) gaps.push({ start: cursor, end: 1440 });
  return { conflicts, gaps };
}

// ==========================================================================
//  渲染
// ==========================================================================

async function loadTemplates() {
  STATE.templates = await call('get_templates');
  return STATE.templates;
}

// ==========================================================================
//  提示栏的关闭状态（关一次就一直不显示）
// ==========================================================================

let infoBarClosed = null;   // null = 还没从 localStorage 读过

/**
 * localStorage 在 file:// 源下可用；万一被隐私模式/策略禁掉，
 * try/catch 接住 —— 退回「本次会话关了就关了」，不算坏。
 */
function infoBarDismissed() {
  if (infoBarClosed === null) {
    try { infoBarClosed = localStorage.getItem('tplInfoBarClosed') === '1'; }
    catch (_) { infoBarClosed = false; }
  }
  return infoBarClosed;
}

function closeInfoBar() {
  infoBarClosed = true;
  try { localStorage.setItem('tplInfoBarClosed', '1'); } catch (_) { /* 同上 */ }
  $('typeInfo').classList.add('closed');
}

async function renderTemplates() {
  // 永远从后端取新 —— STATE.templates 会被别处（创建/删除日型后）改旧，
  // 用缓存渲染会把「刚建好的日型没有标签」这类陈旧状态画出来。
  const t = await loadTemplates();
  if (!t || !t.types) return;

  // ---- 日型标签 ----
  const tabs = $('typeTabs');
  tabs.innerHTML = '';
  t.types.forEach((type) => {
    const btn = el('button', 'type-tab' + (type.key === STATE.activeType ? ' active' : ''), type.label);
    btn.appendChild(typeDot(type));
    btn.onclick = () => switchType(type.key);
    tabs.appendChild(btn);
  });

  // 日型可以自己建：标签末尾的「＋ 新建」打开日型卡片（和设置页同一个）。
  // 满 7 种（id 空间用完）时置灰并说明 —— 先删一个才能再建。
  const addTab = el('button', 'type-tab add',
    t.types.length >= 7 ? '＋ 新建（已满）' : '＋ 新建');
  if (t.types.length >= 7) {
    addTab.disabled = true;
    addTab.title = '日型已满 7 种 —— 到「设置」里删掉一个不用的，再建新的';
  } else {
    addTab.onclick = () => newDayTypeDialog();
  }
  tabs.appendChild(addTab);

  const active = t.types.find((x) => x.key === STATE.activeType) || t.types[0];
  STATE.activeType = active.key;
  ensureSession(active.key);

  $('typeInfoText').textContent = active.wake
    ? `这一天 ${active.wake} 起床${active.isCustom ? '（已自定义）' : ''} —— 左边时间轴上虚线就是起床线，可以拖`
    : '这一天没有上午的睡眠段，界面上不会显示起床时间';
  $('typeInfo').classList.toggle('closed', infoBarDismissed());
  $('typeInfoX').onclick = closeInfoBar;

  renderEditor();
}

function currentTypeLabel() {
  const d = typeData(edit ? edit.key : STATE.activeType);
  return d ? d.label : '';
}

// ==========================================================================
//  日型标签上的删除点（红 ×）
// ==========================================================================

/**
 * 标签右侧的小点 = 删除入口：平时是小圆点（实心 = 自定义过，空心 = 内置），
 * 悬停整枚标签变成红 ×。休息日是引擎自动判定层（工作日全天没课），
 * 删不掉 —— 它的点是纯装饰（.no-del 不变形、不响应点击）。
 */
function typeDot(type) {
  const dot = el('span', 'dot'
    + (type.isCustom ? ' custom' : '')
    + (type.locked ? ' no-del' : ''));
  if (type.locked) {
    dot.title = '休息日由引擎自动判定，不能删除';
    return dot;
  }
  dot.title = '删除这个日型';
  dot.onclick = (e) => {
    e.stopPropagation();   // 点的是删除，不是「切到这个日型」
    tplDeleteType(type);
  };
  return dot;
}

/** 红 × 的删除流程：警告卡片 → 确认 → 后端守卫放行才真删 */
async function tplDeleteType(type) {
  const dirty = edit && edit.key === type.key && edit.dirty;
  const okDel = await confirmBox('删除日型',
    `删除「${type.label}」后它管的天要改派给别人才能用，删除无法撤销。`
    + `整套作息 ${type.blocks.length} 格一起删。`
    + (dirty ? ' 有未保存的改动会一并丢弃。' : ''),
    '删除', true);
  if (!okDel) return;

  const r = await call('delete_day_type', type.key);
  if (!r.ok) {
    // 删不了的原因和「下一步怎么办」都在后端的 message 里（还管着星期 /
    // 是早八日型 / 休息日），照实转达，不在前端另写一套规矩
    await modal({ title: '删不了', body: r.message,
      buttons: [{ label: '知道了', value: 1, primary: true }] });
    return;
  }
  toast(r.message);
  STATE.templates = r.templates;
  STATE.settings = r.settings;
  // 删的不是正在编辑的那套时，工作副本（含未保存改动）原样留着
  if (edit && edit.key === type.key) tplInvalidateSession();
  await renderTemplates();
  await refreshAll();
}

/** 编辑区全量重绘：时间轴 + 列表 + 状态条 + 工具栏（保持选中） */
function renderEditor() {
  if (!edit) return;
  renderTimeline();
  renderBlockList();
  renderStatus();
  updateToolbar();
}

function updateToolbar() {
  const { conflicts } = computeIssues(edit.blocks);
  $('saveTemplate').disabled = !edit.dirty || conflicts.size > 0;
  $('discardEdit').disabled = !edit.dirty;
  $('undoEdit').disabled = edit.undo.length === 0;
}

function renderStatus() {
  const el_ = $('tplStatus');
  const { conflicts, gaps } = computeIssues(edit.blocks);
  const bits = [];
  if (conflicts.size > 0) {
    bits.push(`<b class="danger-text">⚠ ${conflicts.size} 处时间重叠 —— 保存已禁用</b>`);
  }
  if (gaps.length > 0) {
    const first = gaps[0];
    bits.push(`空档 ${gaps.length} 段（如 ${fmtHHMM(first.start)}–${fmtHHMM(first.end)}），`
      + `运行时会自动补「空档 · 机动」；直接在时间轴空档处拖动就能填上`);
  }
  if (edit.dirty && conflicts.size === 0) bits.push('有未保存的改动');
  if (!edit.dirty && conflicts.size === 0 && gaps.length === 0) bits.push('与上次保存一致');
  el_.innerHTML = bits.join('　·　');
}

// ==========================================================================
//  时间轴
// ==========================================================================

function renderTimeline() {
  const canvas = $('tlCanvas');
  canvas.innerHTML = '';
  if (!canvas.dataset.gapHook) {
    attachGapCreate(canvas);
    canvas.dataset.gapHook = '1';
  }

  // 小时刻度（每 2 小时一个 label）
  for (let h = 0; h <= 24; h += 2) {
    const lab = el('span', 'tl-hour', String(h).padStart(2, '0'));
    lab.style.top = (h * 60 * TL_PX_PER_MIN) + 'px';
    // 刻度默认垂直居中在自己的时刻上，但首尾两个正好贴着画布边缘，
    // 会被 overflow:hidden 裁掉半个字 —— 各自往里收（00 对齐上边、
    // 24 对齐下边），中间的照旧居中
    if (h === 0) lab.style.transform = 'translateY(0)';
    else if (h === 24) lab.style.transform = 'translateY(-100%)';
    canvas.appendChild(lab);
  }

  const issues = computeIssues(edit.blocks);
  const conflicts = issues.conflicts;
  const sorted = edit.blocks;
  const conflictObjs = new Set(sorted.filter((_, i) => conflicts.has(i)));

  // 空档（虚纹）
  issues.gaps.forEach((g) => {
    if (g.end - g.start < TL_MIN_DURATION) return;
    const d = el('div', 'tl-gap');
    tlPosition(d, g.start, g.end);
    d.title = `空档 ${fmtHHMM(g.start)}–${fmtHHMM(g.end)}`;
    canvas.appendChild(d);
  });

  // 格子
  sorted.forEach((b) => {
    const d = el('div', 'tl-block' + (b === edit.selected ? ' sel' : '')
      + (conflictObjs.has(b) ? ' conflict' : ''));
    tlPosition(d, b.start, b.end);
    d.style.background = KIND_COLOR[b.kind] || 'var(--border)';
    if ((b.end - b.start) * TL_PX_PER_MIN >= 18) {
      d.appendChild(el('div', 'tl-t', b.title || '（未命名）'));
    }
    d.title = `${fmtHHMM(b.start)}–${fmtHHMM(b.end)}　${b.title}`;
    d.onclick = () => { edit.selected = b; renderEditor(); };

    // 上下边缘拖拽手柄
    const hTop = el('div', 'tl-h top');
    const hBot = el('div', 'tl-h bottom');
    attachResize(hTop, b, 'start');
    attachResize(hBot, b, 'end');
    d.append(hTop, hBot);
    canvas.appendChild(d);
  });

  // 起床线（午前最后一个睡觉段的结束时刻）
  const wake = wakeMinuteOf(edit.blocks);
  if (wake !== null) {
    const w = el('div', 'tl-wake' + (edit.dirty ? ' locked' : ''));
    w.style.top = (wake * TL_PX_PER_MIN) + 'px';
    w.appendChild(el('span', 'tl-wlabel', '起床 ' + fmtHHMM(wake)));
    if (!edit.dirty) attachWakeDrag(w);
    else w.title = '先保存或放弃当前改动，再拖起床线';
    canvas.appendChild(w);
  }
}

function tlPosition(d, start, end) {
  d.style.top = (start * TL_PX_PER_MIN) + 'px';
  d.style.height = Math.max((end - start) * TL_PX_PER_MIN, 2) + 'px';
}

function wakeMinuteOf(blocks) {
  const morning = blocks.filter((b) => b.kind === 'SLEEP' && b.start < 12 * 60);
  if (!morning.length) return null;
  return Math.max(...morning.map((b) => b.end));
}

function snap5(min) {
  return Math.round(min / TL_MIN_DURATION) * TL_MIN_DURATION;
}

/** 分钟 → 时间轴 y 坐标换算（拖拽共用的鼠标取值）。
 *  取值和换算都用 getBoundingClientRect 的**可视坐标** —— 设置页的
 *  字体大小是根元素上的 CSS zoom，clientTop/clientHeight 在缩放下
 *  与可视坐标不同源，混用会在非 100% 档位错位。边框带来的 1px 偏差
 *  由 5 分钟吸附吃掉。 */
function pointerMinute(canvas, ev) {
  const rect = canvas.getBoundingClientRect();
  return Math.max(0, Math.min(1440, (ev.clientY - rect.top) / rect.height * 1440));
}

/** 拖拽边缘调 start / end。钳制到相邻块之间 —— 重叠在源头就不可能发生 */
function attachResize(handle, b, which) {
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    try { handle.setPointerCapture(e.pointerId); } catch (_) { /* 合成事件/指针已释放 */ }
    const canvas = $('tlCanvas');
    const i = edit.blocks.indexOf(b);
    const prev = edit.blocks[i - 1];
    const next = edit.blocks[i + 1];
    const orig = deepBlocks(edit.blocks);

    const lo = which === 'start'
      ? (prev ? prev.end : 0)
      : b.start + TL_MIN_DURATION;
    const hi = which === 'start'
      ? b.end - TL_MIN_DURATION
      : (next ? next.start : 1440);

    const tip = el('div', 'tl-tip');
    const move = (ev2) => {
      const m = Math.max(lo, Math.min(hi, snap5(pointerMinute(canvas, ev2))));
      b[which] = m;
      tlPosition(handle.parentElement, b.start, b.end);
      tip.textContent = fmtHHMM(m);
      tip.style.top = (m * TL_PX_PER_MIN) + 'px';
      canvas.appendChild(tip);
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      tip.remove();
      const changed = orig.some((x, j) =>
        x.start !== edit.blocks[j].start || x.end !== edit.blocks[j].end);
      if (changed) {
        edit.undo.push(orig);
        edit.dirty = true;
      }
      renderEditor();
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
}

/** 在空档处按下拖动 → 新格子 */
function attachGapCreate(canvas) {
  canvas.addEventListener('pointerdown', (e) => {
    // 只在「空白处」起拖：点在块/手柄上的不归这里管（target 不是 canvas/gap）
    if (e.target !== canvas && !e.target.classList.contains('tl-gap')) return;
    e.preventDefault();
    try { canvas.setPointerCapture(e.pointerId); } catch (_) { /* 同上 */ }

    const m0 = snap5(pointerMinute(canvas, e));
    let m1 = m0;
    const preview = el('div', 'tl-block creating');
    preview.style.background = 'var(--accent-soft)';
    canvas.appendChild(preview);

    const move = (ev2) => {
      m1 = snap5(pointerMinute(canvas, ev2));
      tlPosition(preview, Math.min(m0, m1), Math.max(m0, m1) + TL_MIN_DURATION);
    };
    const up = () => {
      canvas.removeEventListener('pointermove', move);
      canvas.removeEventListener('pointerup', up);
      preview.remove();
      let s = Math.min(m0, m1), en = Math.max(m0, m1) + TL_MIN_DURATION;
      en = Math.min(en, 1440);
      if (en - s < TL_MIN_DURATION) return;
      tplAddBlockAt(s, en);
    };
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', up);
  });
}

/** 拖起床线：只在会话干净时可用（shift_wake_time 改的是已保存的数据） */
function attachWakeDrag(line) {
  line.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    try { line.setPointerCapture(e.pointerId); } catch (_) { /* 同上 */ }
    const canvas = $('tlCanvas');
    const orig = parseInt(line.style.top, 10) / TL_PX_PER_MIN;

    const tip = el('div', 'tl-tip');
    const move = (ev2) => {
      const m = Math.max(4 * 60, Math.min(12 * 60, snap5(pointerMinute(canvas, ev2))));
      line.style.top = (m * TL_PX_PER_MIN) + 'px';
      const lab = line.querySelector('.tl-wlabel');
      if (lab) lab.textContent = '起床 ' + fmtHHMM(m);
      tip.textContent = fmtHHMM(m);
      tip.style.top = (m * TL_PX_PER_MIN) + 'px';
      canvas.appendChild(tip);
    };
    const up = async (ev2) => {
      line.removeEventListener('pointermove', move);
      line.removeEventListener('pointerup', up);
      tip.remove();
      const m = Math.max(4 * 60, Math.min(12 * 60, snap5(pointerMinute(canvas, ev2))));
      if (m === orig) { renderTimeline(); return; }
      const r = await call('shift_wake_time', edit.key, fmtHHMM(m));
      if (r.ok) {
        STATE.templates = r.templates;
        tplInvalidateSession();
        await renderTemplates();
        toast(r.message);
      } else {
        await modal({ title: '改不了', body: r.message });
        renderTimeline();
      }
    };
    line.addEventListener('pointermove', move);
    line.addEventListener('pointerup', up);
  });
}

// ==========================================================================
//  列表 + 行内编辑器
// ==========================================================================

function renderBlockList() {
  const list = $('blockList');
  list.innerHTML = '';

  edit.blocks.forEach((b) => {
    const sel = b === edit.selected;
    const row = el('div', 'row' + (sel ? ' sel' : ''));

    const dot = el('div', 'kind-dot');
    dot.style.background = KIND_COLOR[b.kind] || 'var(--border)';

    const time = el('div', 'row-time');
    time.appendChild(el('div', null, fmtHHMM(b.start)));
    time.appendChild(el('div', null, fmtHHMM(b.end)));

    const main = el('div', 'row-main');
    main.appendChild(el('div', 'row-title', b.title || '（未命名）'));
    const bits = [KIND_LABEL[b.kind] || b.kind];
    if (b.nodes) bits.push(`第 ${b.nodes[0]}-${b.nodes[1]} 节课表格子`);
    if (b.note) bits.push(b.note);
    main.appendChild(el('div', 'row-sub', bits.join(' · ')));

    row.append(dot, time, main);
    row.onclick = () => {
      edit.selected = (sel ? null : b);
      renderEditor();
    };
    list.appendChild(row);

    if (sel) list.appendChild(buildRowEditor(b));
  });
}

function buildRowEditor(b) {
  const box = el('div', 'row-editor');

  const inTitle = document.createElement('input');
  inTitle.className = 'input';
  inTitle.value = b.title;
  inTitle.placeholder = '这一格做什么，例如：早餐';

  const inStart = document.createElement('input');
  inStart.className = 'input'; inStart.type = 'time';
  inStart.value = fmtHHMM(b.start);

  const inEnd = document.createElement('input');
  inEnd.className = 'input'; inEnd.type = 'time';
  inEnd.value = b.end === 1440 ? '23:59' : fmtHHMM(b.end);

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
  nodesWrap.append(inNodesOn, document.createTextNode('课表格子（有课时自动换成课程名和教室）'));

  const inNodeA = nodeSelect(b.nodes ? b.nodes[0] : 1, STATE.maxNode);
  const inNodeB = nodeSelect(b.nodes ? b.nodes[1] : 2, STATE.maxNode);

  const grid = el('div', 'field-grid');
  grid.append(
    mk('这一格做什么', inTitle, true),
    mk('开始', inStart),
    mk('结束', inEnd),
    mk('性质（决定颜色）', inKind),
    mk('备注', inNote, true),
    mk('', nodesWrap, true),
    mk('占位节次 起', inNodeA),
    mk('占位节次 止', inNodeB),
  );
  box.appendChild(grid);

  const actions = el('div', 'row-editor-actions');
  const apply = el('button', 'btn primary mini', '应用改动');
  const del = el('button', 'btn mini danger', '删除此格');
  actions.append(apply, del);
  box.appendChild(actions);

  apply.onclick = () => {
    let start = toMinutes(inStart.value);
    let end = toMinutes(inEnd.value);
    // <input type="time"> 不接受 24:00 —— 选 23:59 或早于开始都按「到当天结束」理解
    if (end <= start) end = 1440;
    mutate((blocks) => {
      Object.assign(b, {
        start, end,
        title: inTitle.value.trim() || '（未命名）',
        note: inNote.value.trim(),
        kind: inKind.value,
        nodes: inNodesOn.checked ? [Number(inNodeA.value), Number(inNodeB.value)] : null,
      });
    });
    toast('已应用（还未保存）');
  };

  del.onclick = async () => {
    if (edit.blocks.length <= 1) {
      await modal({ title: '删不了', body: '至少要留一格，否则这一天就没有作息了。' });
      return;
    }
    mutate((blocks) => {
      blocks.splice(blocks.indexOf(b), 1);
      edit.selected = null;
    });
  };

  return box;
}

// ==========================================================================
//  动作：添加 / 保存 / 复制 / 换日型 / 改起床
// ==========================================================================

function tplAddBlock() {
  if (!edit) return;
  const { gaps } = computeIssues(edit.blocks);
  const g = gaps[0];
  tplAddBlockAt(g ? g.start : 8 * 60, g ? Math.min(g.start + 60, g.end) : 9 * 60);
}

function tplAddBlockAt(start, end) {
  let nb;
  mutate((blocks) => {
    nb = {
      start, end,
      title: '新格子', note: '', kind: 'CHORE', nodes: null,
    };
    blocks.push(nb);
    blocks.sort((a, b2) => a.start - b2.start);
    edit.selected = nb;
  });
}

async function tplSave() {
  if (!edit || !edit.dirty) return true;
  const { conflicts } = computeIssues(edit.blocks);
  if (conflicts.size > 0) {
    await modal({ title: '保存不了', body: '还有时间重叠的格子（时间轴上标红的那几处），先把它们改开。' });
    return false;
  }
  const payload = edit.blocks.map((b) => ({
    start: fmtHHMM(b.start),
    end: fmtHHMM(b.end),
    title: b.title,
    note: b.note,
    kind: b.kind,
    nodes: b.nodes,
  }));
  const r = await call('save_template_blocks', edit.key, payload);
  if (!r.ok) {
    await modal({ title: '保存失败', body: r.message });
    return false;
  }
  STATE.templates = r.templates;
  tplInvalidateSession();
  ensureSession(STATE.activeType);
  renderEditor();
  toast(r.message);
  return true;
}

async function tplCopyFrom() {
  if (!edit) return;
  const others = STATE.templates.types.filter((x) => x.key !== edit.key);
  const sel = document.createElement('select');
  sel.className = 'input';
  others.forEach((x) => {
    const o = document.createElement('option');
    o.value = x.key;
    o.textContent = `${x.label}（${x.blocks.length} 格）`;
    sel.appendChild(o);
  });

  const form = el('div');
  form.appendChild(mk('从哪一套复制', sel));
  const note = el('p', 'hint');
  note.textContent = `会覆盖「${currentTypeLabel()}」现有的全部 ${edit.blocks.length} 格，`
    + '复制完可以再微调。未保存的改动会先被丢弃。';
  form.appendChild(note);

  const v = await modal({
    title: '从另一套复制',
    body: form,
    buttons: [
      { label: '取消', value: null },
      { label: '复制', value: 'go', primary: true },
    ],
  });
  if (v !== 'go') return;

  const r = await call('copy_template', sel.value, edit.key);
  if (!r.ok) {
    await modal({ title: '复制失败', body: r.message });
    return;
  }
  STATE.templates = r.templates;
  tplInvalidateSession();
  ensureSession(STATE.activeType);
  renderTemplates();
  toast(r.message);
}

async function switchType(newKey) {
  if (newKey === STATE.activeType) return;
  if (edit && edit.dirty) {
    const v = await modal({
      title: '有未保存的改动',
      body: `「${currentTypeLabel()}」的作息改了还没保存。`,
      buttons: [
        { label: '保存并切换', value: 'save', primary: true },
        { label: '放弃并切换', value: 'discard' },
        { label: '取消', value: null },
      ],
    });
    if (v === null) return;
    if (v === 'save' && !(await tplSave())) return;
  }
  STATE.activeType = newKey;
  edit = null;
  await renderTemplates();
}

/** 离开作息页的守卫：app.js 的 switchPage 每次切页都会问一句 */
async function tplGuardLeave(target) {
  const cur = document.querySelector('.page.active');
  const onTemplates = cur && cur.id === 'page-templates';
  if (target === 'templates' || !onTemplates || !edit || !edit.dirty) return true;

  const v = await modal({
    title: '有未保存的改动',
    body: `「${currentTypeLabel()}」的作息改了还没保存。`,
    buttons: [
      { label: '保存并离开', value: 'save', primary: true },
      { label: '放弃改动', value: 'discard' },
      { label: '取消', value: null },
    ],
  });
  if (v === 'save') return await tplSave();
  if (v === 'discard') { edit = null; return true; }
  return false;
}

async function tplChangeWake() {
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
    '会同时改动「睡觉」的结束时刻和后面紧跟着的那一格，两格之间的时长保持不变。'
    + '时间轴上拖起床线是同一件事的快捷方式。';
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
    tplInvalidateSession();
    ensureSession(STATE.activeType);
    renderTemplates();
    toast(r.message);
  } else {
    await modal({ title: '改不了', body: r.message });
  }
}

// 空档拖拽建格监听挂在 canvas 本体上（renderTimeline 清空的是子元素，
// 不影响它），挂一次就够 —— dataset 标记防重复。
