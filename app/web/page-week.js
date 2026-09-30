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

  // 先给每个格子算好合并跨度。同一列里相邻的格子合并成一格，分两种：
  //  · 同一门课（名称、教室、周次都一致）—— 有的学校课不成对，一门课在
  //    数据里是按小节拆开的两条记录，显示时把它们拼回一整块；
  //  · 连续的空格 —— 节一多（"553" 一天 13 行）空格子就是满页灰盒子，
  //    按列并成一整块，页面只留下课和真正的空档。
  // 空 run 和课 run 互不吞并；单个空格自成一块，那本来就是「这一节没安排」。
  w.rows.forEach((r) => r.cells.forEach((c) => { c.rowspan = 1; c.skip = false; }));
  for (let ci = 0; ci < w.days.length; ci++) {
    let ri = 0;
    while (ri < w.rows.length) {
      const c = w.rows[ri].cells[ci];
      const empty = !c.name;
      let span = 1;
      while (ri + span < w.rows.length) {
        const nxt = w.rows[ri + span].cells[ci];
        if (!nxt.name !== empty) break;   // 空/课交界处断开
        if (!empty && (nxt.name !== c.name || nxt.place !== c.place || nxt.weeksText !== c.weeksText)) break;
        span++;
      }
      c.rowspan = span;
      for (let k = 1; k < span; k++) w.rows[ri + k].cells[ci].skip = true;
      ri += span;
    }
  }

  w.rows.forEach((r) => {
    const tr = el('tr');

    const tdTime = el('td', 'col-time');
    tdTime.appendChild(el('div', null, r.label));
    // 起止时间放同一行（中间空格），压低整表高度
    tdTime.appendChild(el('div', null, `${r.startTime} ${r.endTime}`));
    tr.appendChild(tdTime);

    r.cells.forEach((c) => {
      if (c.skip) return;   // 已经被上面的 rowspan 盖住了
      const td = el('td', 'cell');
      if (c.rowspan > 1) td.rowSpan = c.rowspan;
      const inner = el('div', 'cell-inner' + (c.name ? ' has' : ''));
      if (!c.name && c.rowspan > 1) {
        // 合并的大空格：按节数铺等分子行，行间一条淡虚线标出被合并的节边界
        // —— 对着左侧节次栏还能数出空档是第几节到第几节
        inner.classList.add('gap-run');
        for (let k = 0; k < c.rowspan; k++) inner.appendChild(el('div', 'gap-row'));
      }
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
