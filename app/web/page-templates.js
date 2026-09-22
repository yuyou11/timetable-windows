/*
「作息编辑」页 —— 改六套作息模板、一键改起床时间。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
（`toMinutes` 搬去了 core.js，它是通用工具。）
   ========================================================================== */

'use strict';

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
