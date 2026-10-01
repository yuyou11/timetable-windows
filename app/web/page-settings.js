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
 * 日型列表：勾选启用 + 改名（v4）。
 *
 * 每个选项旁边显示**这一型的起床时间** —— 用户勾选时才看得出代价：
 * 「把工作日全勾成 A 型」意味着没早八的日子也 06:55 起床，
 * 而那正是这个程序最该帮人避免的事。
 *
 * v4 的两点：
 * · 每行有「改名」按钮 —— 名字是用户数据，即时保存，不走下面的
 *   「保存日型设置」（那颗按钮只管启用/回落）；
 * · 休息日（id "7"）是引擎自动判定的一行：没有勾选框、标着「自动」，
 *   名字照样可以改。
 */
async function renderDayTypes() {
  const d = await call('get_day_types');
  if (!d || !d.types) return;

  const box = $('dayTypeList');
  box.innerHTML = '';

  d.types.forEach((t) => {
    const row = el('label', 'daytype-item');

    // 休息日没有勾选框：它永远生效，「启用/回落」跟它无关
    if (!t.locked) {
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
    }

    const text = el('div', 'daytype-text');
    text.appendChild(el('div', 'daytype-name', t.label));
    if (t.wake) text.appendChild(el('div', 'daytype-wake', t.wake + ' 起床'));
    row.appendChild(text);

    if (t.isFallback) row.appendChild(el('span', 'daytype-badge', '当前回落'));
    if (t.locked) row.appendChild(el('span', 'daytype-badge', '自动'));
    if (!t.isDefaultName) row.appendChild(el('span', 'daytype-badge', '已改名'));

    // type="button" + preventDefault：按钮在 <label> 里，
    // 不拦的话点「改名」会顺带切换勾选框
    const rename = el('button', 'btn ghost mini', '改名');
    rename.type = 'button';
    rename.onclick = async (e) => {
      e.preventDefault();
      await renameDialog(d, t);
    };
    row.appendChild(rename);

    box.appendChild(row);
  });

  // 下拉框只列**已勾选**的日型 —— 让用户选一个没勾的，
  // 等于给了一个自相矛盾的控件（后端也会拦下来）
  const sel = $('dayTypeFallback');
  sel.innerHTML = '';
  d.types.filter((t) => t.enabled && !t.locked).forEach((t) => {
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

/**
 * 改名弹窗（v4）。
 *
 * 后端 save_day_type_names 的语义是「整体替换稀疏表」，所以这里先把
 * 当前所有「已改名」的项收拢成 desired，再套用本次改动后整体提交 ——
 * 只传一个键的话，其他键会被当成「恢复默认」而静默丢失。
 *
 * 名字改动即时生效（改名的语义就是「改这个标签」，没有攒着一起
 * 提交的必要）；「保存日型设置」那颗按钮只管启用/回落。
 */
async function renameDialog(d, t) {
  // 当前的稀疏表：把「已改名」的行收拢（isDefaultName=false 的那些）
  const desired = {};
  d.types.forEach((x) => { if (!x.isDefaultName) desired[x.key] = x.label; });

  const input = el('input', 'input');
  input.type = 'text';
  input.maxLength = 20;
  input.value = t.label;
  const form = el('div');
  form.appendChild(input);
  form.appendChild(el('p', 'hint',
    `id ${t.key} · 名字显示在今天页、悬浮窗和导出文件里（最多 20 字）。` +
    `清空并保存 = 恢复默认名「${t.defaultName}」。`));
  const ok = await modal({
    title: '给日型改名',
    body: form,
    buttons: [
      { label: '取消', value: null },
      { label: '保存', value: 1, primary: true },
    ],
  });
  if (!ok) return;

  const name = input.value.trim();
  if (name === desired[t.key]) return;   // 没变化就不打扰后端
  if (name) desired[t.key] = name;
  else delete desired[t.key];            // 空串 = 恢复默认名

  const r = await call('save_day_type_names', desired);
  if (!r.ok) {
    await modal({ title: '改名失败', body: r.message, buttons: [{ label: '知道了', value: 1, primary: true }] });
    return;
  }
  toast(r.message);
  await renderDayTypes();
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
