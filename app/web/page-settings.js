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
