/*
「设置」页 —— 学期信息、课前提醒、悬浮窗开关、日型勾选。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

// ==========================================================================
//  设置
// ==========================================================================

async function renderSettings() {
  const t = await call('bootstrap');
  STATE.term = t.term;
  STATE.settings = t.settings;

  $('swBall').checked = t.settings.ballEnabled;
  $('leadValue').textContent = t.settings.remindLead === 0 ? '关闭' : t.settings.remindLead + ' 分钟';
  // 主题三档。值就是存的那三个字面量（system / light / dark），
  // 和 <select> 的 option.value 对上 —— 别在这里做映射，多一层就多一处会漂移。
  $('themePick').value = t.settings.theme;

  // 字体大小：存档值在 setter 里吸附过档位，一定对得上某个 option
  const fontPick = $('fontPick');
  fontPick.value = String(t.settings.fontScale);
  if (fontPick.selectedIndex === -1) fontPick.value = '1';   // 兜底：手改出离档值

  renderAccent(t.settings.accent || '');
  $('cardOpacity').value = t.settings.cardOpacity;
  $('cardOpacityValue').textContent = t.settings.cardOpacity + '%';
  $('sidebarOpacity').value = t.settings.sidebarOpacity;
  $('sidebarOpacityValue').textContent = t.settings.sidebarOpacity + '%';
  $('bgDim').value = t.settings.bgDim;
  $('bgDimValue').textContent = t.settings.bgDim + '%';
  // 没有背景图时暗化没意义，禁用掉比让用户白调一把好
  $('bgDim').disabled = !t.settings.bgImage;
  // 文字颜色同理：只在有背景图时生效（深浅配对是跟着图走的），没图就禁用。
  // 之前存过的值保留着，重新选图会自动回来。
  renderTextColor(t.settings.textColor || '', t.settings.bgImage);

  $('termName').value = t.term.name;
  $('termStart').value = t.term.start;
  $('termWeeks').value = t.term.totalWeeks;
  $('termHint').textContent =
    `今天是第 ${t.term.todayWeek} 周。起始日不是周一也没关系，保存时会自动对齐到那一周的周一。`;

  await renderDayTypes();

  updateStatusPill(t.settings);
}

// ==========================================================================
//  自定义外观
// ==========================================================================

/** 主色调快捷色板。挑顺手的几个色相，自定义走旁边的取色器。 */
const ACCENT_PRESETS = ['#2563eb', '#0d9488', '#16a34a', '#7c3aed', '#db2777', '#ea580c'];

/**
 * 画一排色板圆点，标出当前选中的那个。active 传 '' 表示用默认 ——
 * 此时六颗都不描边，旁边取色器恢复成默认蓝。
 */
function renderAccent(active) {
  const row = $('accentRow');
  // 只重建圆点，取色器和「默认」按钮是写在 HTML 里的，别动它们
  row.querySelectorAll('.swatch').forEach((n) => n.remove());
  ACCENT_PRESETS.forEach((c) => {
    const dot = el('button', 'swatch' + (c === active ? ' active' : ''));
    dot.type = 'button';
    dot.style.background = c;
    dot.title = c;
    dot.onclick = async () => {
      const r = await call('set_accent', c);
      if (!r || r.ok === false) { toast((r && r.message) || '没换成'); return; }
      STATE.settings = r;
      renderAccent(c);
      $('accentPick').value = c;
    };
    row.insertBefore(dot, $('accentPick'));
  });
  $('accentPick').value = active || '#2563eb';
}

/** 文字颜色快捷色板。深色图配白字是最常见的诉求，顺手给个暖黄和深色。 */
const TEXT_PRESETS = ['#ffffff', '#fde68a', '#1a1d23'];

/**
 * 画文字颜色那一排：色板圆点 + 取色器 + 「默认」。enabled 传 false 时
 * 全部禁用 —— 没有背景图时这个设置不生效，可点但没反应比禁用更糟。
 */
function renderTextColor(active, enabled) {
  const row = $('textColorRow');
  const on = enabled !== false;
  row.querySelectorAll('.swatch').forEach((n) => n.remove());
  TEXT_PRESETS.forEach((c) => {
    const dot = el('button', 'swatch' + (c === active ? ' active' : ''));
    dot.type = 'button';
    dot.style.background = c;
    dot.title = c;
    dot.disabled = !on;
    dot.onclick = async () => {
      const r = await call('set_text_color', c);
      if (!r || r.ok === false) { toast((r && r.message) || '没换成'); return; }
      STATE.settings = r;
      renderTextColor(c, true);
    };
    row.insertBefore(dot, $('textPick'));
  });
  $('textPick').value = active || '#ffffff';
  $('textPick').disabled = !on;
  $('textReset').disabled = !on;
}

/**
 * 「星期怎么排」+ 日型管理（v5）。
 *
 * 三块内容：
 * · 早八日型下拉 —— 有早八（第 1-2 节有课）的工作日优先用它，
 *   可选「不区分早八」（那种日子也按星期映射走）；
 * · 星期映射 —— 周一~周日各用哪套模板（7 个下拉，改动即保存）；
 * · 日型列表 —— 每行「编辑」打开日型卡片（名称/管星期几/删除），
 *   列表末尾「＋ 新建日型」（满 7 种时置灰）。
 *
 * 休息日（id "7"）是引擎的自动判定层（工作日全天没课）：
 * 不参与映射、删不掉，名字可以改。
 *
 * 每行显示**起床时间** —— 配置时得看得出代价：
 * 「让周二用 A 型」意味着周二 06:55 起床，而这正是这个程序
 * 最该帮人避免的失误。
 */
async function renderDayTypes() {
  const d = await call('get_day_types');
  if (!d || !d.types) return;

  const dayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  const optText = (t) => `${t.label}${t.wake ? '　' + t.wake + ' 起' : ''}`;
  const selectable = d.types.filter((t) => !t.locked);

  // ---- 早八日型下拉 ----
  const early = $('earlyTypeSel');
  early.innerHTML = '';
  const off = el('option', null, '不区分早八（都按星期映射）');
  off.value = '';
  early.appendChild(off);
  selectable.forEach((t) => {
    const opt = el('option', null, optText(t));
    opt.value = t.key;
    if (t.key === d.earlyType) opt.selected = true;
    early.appendChild(opt);
  });
  early.onchange = async () => {
    const r = await call('save_early_type', early.value);
    if (!r.ok) {
      await modal({ title: '保存失败', body: r.message,
        buttons: [{ label: '知道了', value: 1, primary: true }] });
      await renderDayTypes();
      return;
    }
    toast(r.message);
    await renderDayTypes();
    await refreshAll();
  };

  // ---- 星期映射：七行下拉（改动即保存） ----
  const mapBox = $('weekdayMap');
  mapBox.innerHTML = '';
  for (let dow = 1; dow <= 7; dow++) {
    const row = el('div', 'field-row weekday-row');
    row.appendChild(el('label', null, dayNames[dow - 1]));
    const sel = el('select', 'input');
    const owner = d.types.find((x) => x.weekdays.includes(dow));
    selectable.forEach((t) => {
      const opt = el('option', null, optText(t));
      opt.value = t.key;
      if (owner && owner.key === t.key) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = () => saveWeekdayMap();
    row.appendChild(sel);
    mapBox.appendChild(row);
  }

  // ---- 日型列表 ----
  const box = $('dayTypeList');
  box.innerHTML = '';
  d.types.forEach((t) => {
    const row = el('div', 'daytype-item');
    const text = el('div', 'daytype-text');
    text.appendChild(el('div', 'daytype-name', t.label));
    const bits = [];
    if (t.wake) bits.push(t.wake + ' 起床');
    bits.push(t.weekdays.length
      ? '管 ' + t.weekdays.map((w) => dayNames[w - 1]).join('、')
      : '谁都不管');
    text.appendChild(el('div', 'daytype-wake', bits.join(' · ')));
    row.appendChild(text);

    if (t.isEarly) row.appendChild(el('span', 'daytype-badge', '早八'));
    if (t.locked) row.appendChild(el('span', 'daytype-badge', '自动'));
    if (!t.isDefaultName) row.appendChild(el('span', 'daytype-badge', '已改名'));

    const edit = el('button', 'btn ghost mini', '编辑');
    edit.type = 'button';
    edit.onclick = () => dayTypeDialog(d, t);
    row.appendChild(edit);
    box.appendChild(row);
  });

  // ---- 新建（满 7 种置灰） ----
  const add = $('addDayType');
  add.disabled = d.freeIds.length === 0;
  add.textContent = d.freeIds.length === 0 ? '＋ 新建（已满 7 种）' : '＋ 新建日型';

  const hint = $('dayTypeHint');
  const earlyLabel = (d.types.find((t) => t.key === d.earlyType) || {}).label;
  hint.textContent = d.earlyType
    ? `有早八的工作日优先用「${earlyLabel}」，其余按上面的星期映射走。`
    : '不区分早八：所有日子都按上面的星期映射走。';
  hint.classList.remove('warn');
}

/** 七个下拉的现状 → save_weekday_map（整体提交，一个选择只能有一套模板） */
async function saveWeekdayMap() {
  const vals = [...document.querySelectorAll('#weekdayMap select')].map((s) => s.value);
  const r = await call('save_weekday_map', vals);
  if (!r.ok) {
    await modal({ title: '保存失败', body: r.message,
      buttons: [{ label: '知道了', value: 1, primary: true }] });
    await renderDayTypes();
    return;
  }
  toast(r.message);
  await renderDayTypes();
  await refreshAll();
}

/**
 * 日型卡片：新建 / 编辑（名称 + 管星期几 + 删除）。
 *
 * 设置页和作息编辑页共用（模板页日型标签右侧的「＋ 新建」也调它）。
 * 新建 = create_day_type + 可选 update_day_type(weekdays)；
 * 编辑 = update_day_type（名称留空 = 不改名）；
 * 删除在卡片里 —— 后端三道守卫（休息日 / 管着星期 / 早八日型）
 * 会把不能删的原因写清楚。
 */
/** 「＋ 新建日型」入口（设置页按钮和模板页标签共用） */
async function newDayTypeDialog() {
  const d = await call('get_day_types');
  if (!d || !d.types) return;
  await dayTypeDialog(d, null);
}

async function dayTypeDialog(d, t) {
  const isNew = !t;
  const form = el('div');

  const nameInput = el('input', 'input');
  nameInput.type = 'text';
  nameInput.maxLength = 20;
  nameInput.value = isNew ? '' : t.label;
  nameInput.placeholder = isNew ? '给新日型起个名字，例如：考试日' : t.defaultName;
  form.appendChild(mk('日型名', nameInput, true));

  // 星期勾选：新建默认不勾（建好再安排也行）；休息日不参与映射
  const dayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  const checks = [];
  const dayWrap = el('div', 'weekday-checks');
  if (t && t.locked) {
    dayWrap.appendChild(el('p', 'hint',
      '休息日由引擎自动判定（工作日全天没课），不参与星期映射 —— 这里只改它的名字。'));
  } else {
    for (let dow = 1; dow <= 7; dow++) {
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = isNew ? false : t.weekdays.includes(dow);
      const lab = el('label', 'wd-check');
      lab.append(cb, document.createTextNode(dayNames[dow - 1]));
      dayWrap.appendChild(lab);
      checks.push(cb);
    }
  }
  form.appendChild(mk('管星期几（勾中的天用这一套）', dayWrap, true));
  form.appendChild(el('p', 'hint',
    '每个星期几只能由一种日型管 —— 勾上之后，原来管这天的自动让出。'));

  const buttons = [
    { label: '取消', value: null },
  ];
  if (!isNew && !t.locked) {
    buttons.push({ label: '删除此日型', value: 'delete', danger: true });
  }
  buttons.push({ label: isNew ? '创建' : '保存', value: 'save', primary: true });

  const v = await modal({
    title: isNew ? '新建日型' : `编辑「${t.label}」`,
    body: form,
    buttons,
  });
  if (v === null) return;

  if (v === 'delete') {
    const ok = await confirmBox('删除日型',
      `删除「${t.label}」后它管的天要改派给别人才能用。删除无法撤销。`, '删除');
    if (!ok) return;
    const r = await call('delete_day_type', t.key);
    if (!r.ok) {
      await modal({ title: '删不了', body: r.message,
        buttons: [{ label: '知道了', value: 1, primary: true }] });
      return;
    }
    toast(r.message);
    await renderDayTypes();
    await renderTemplates();
    await refreshAll();
    return;
  }

  const name = nameInput.value.trim();
  const days = checks.map((cb, i) => (cb.checked ? i + 1 : 0)).filter(Boolean);

  if (isNew) {
    const r = await call('create_day_type', name);
    if (!r.ok) {
      await modal({ title: '建不了', body: r.message,
        buttons: [{ label: '知道了', value: 1, primary: true }] });
      return;
    }
    if (days.length) {
      await call('update_day_type', r.newId, { weekdays: days });
    }
    toast(r.message);
  } else {
    const props = {};
    if (name && name !== t.label) props.name = name;
    if (!t.locked) props.weekdays = days;
    if (Object.keys(props).length) {
      const r = await call('update_day_type', t.key, props);
      if (!r.ok) {
        await modal({ title: '保存失败', body: r.message,
          buttons: [{ label: '知道了', value: 1, primary: true }] });
        return;
      }
      toast(r.message);
    }
  }
  await renderDayTypes();
  await renderTemplates();
  await refreshAll();
}

/**
 * 左下角的状态胶囊。它同时是悬浮窗的开关（点击切换，绑定见 app.js 的
 * bindEvents），所以文案只说悬浮窗这一件事：以前「关了悬浮窗、只开提醒」
 * 时会显示「提醒已开启」，但按下去变化的是悬浮窗 —— 文案和按钮做的事
 * 对不上，会让人觉得「我按了它，怎么开关的是悬浮窗」。
 */
function updateStatusPill(s) {
  const pill = $('statusPill');
  const on = !!s.ballEnabled;
  pill.textContent = on ? '悬浮窗运行中' : '悬浮窗已关闭';
  pill.className = on ? 'pill on' : 'pill';
  pill.title = on ? '点击关闭桌面悬浮窗' : '点击开启桌面悬浮窗';
}
