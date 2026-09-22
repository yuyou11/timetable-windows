/*
「课表」页 —— 一周的课程网格，可以翻周。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

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
