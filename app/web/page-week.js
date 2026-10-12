/*
「课表」页 —— 一周的课程网格 + 点卡片展开的「选周」浮层。

从 app.js 拆出来（tools/split_app_js.py），后来翻周按钮换成了浮层。
   ========================================================================== */

'use strict';

// ==========================================================================
//  课表网格
// ==========================================================================

async function renderWeek() {
  const w = await call('get_week', STATE.weekOffset);
  if (!w || !w.rows) return;

  // 网格正在显示第几周 —— 浮层打开时草稿周从它起步，「确定」时也拿它
  // 判断要不要重画网格。weekShown 是钳位后的真实周号（1..总周数）。
  STATE.weekShown = w.week;

  $('weekTitle').textContent = w.isCurrent ? `第 ${w.week} 教学周 · 本周` : `第 ${w.week} 教学周`;
  $('weekRange').textContent = w.rangeText;

  const hints = $('dayHints');
  hints.innerHTML = '';
  w.days.forEach((d) => {
    hints.appendChild(el('span', d.isToday ? 'today' : null, `${d.label}·${d.shortType}`));
  });

  const table = $('weekGrid');
  table.innerHTML = '';

  // 「当天」高亮的落点：表头 + 整列格子 + 上面的日型提示行。
  // isToday 由后端给（真实日期比对），翻到别的周时七天全是 false
  const todayDow = (w.days.find((d) => d.isToday) || {}).dow;

  const thead = el('thead');
  const hrow = el('tr');
  hrow.appendChild(el('th', 'col-time', '节次'));
  w.days.forEach((d) => {
    const th = el('th', d.isToday ? 'today' : null, d.label);
    if (d.isToday) th.title = '今天';
    hrow.appendChild(th);
  });
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
      const td = el('td', 'cell' + (c.dow === todayDow ? ' today' : ''));
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

// ==========================================================================
//  选周浮层：点页头卡片（标题+日期那张）展开，下方是各周的点阵预览
//
//  流程：点页头卡片 → 它从原位放大到几乎占满窗口（几何用行内样式逐帧过渡，
//  见 style.css 的 .week-card.animating）→ 浮层内容淡入。
//  浮层里选中的只是「草稿」，网格不变；点「确定」才收下并重画网格，
//  点遮罩 / ESC / 「回到本周」外的取消路径都不动网格。
// ==========================================================================

const WEEK_PICKER = {
  open: false,
  animating: false,
  draft: 1,       // 浮层里正在看的周（确定之前只是草稿）
  current: 1,     // 真正的「本周」（钳到 1..总周数），「回到本周」按它判禁用
  todayWeek: 0,   // 未钳位的今天周次 —— 换算 weekOffset 用（get_week 的 offset 相对它）
  weeks: [],      // get_week_overview 回来的各周信息（形状、有没有课、日期范围）
  cells: {},      // week -> 预览图 DOM。建一次反复改样式，切周才有连续动画
  restoreRect: null, // 展开前的卡片矩形（视觉 px）—— 占位元素量不到时的兜底
  placeholder: null, // 卡片在流里的占位 —— 收回去的落点按它现量，窗口改过大小也算数
  hopToken: 0,    // 大跨度快滑的批次号 —— 中途改点别处/关浮层时作废排队的步子
  hopTarget: null, // 快滑的目的地（还没滑到）：这期间点「确定」要按它落周次
};

// 展开动画时长（ms）—— 必须和 style.css 里 .week-card.animating 的 .44s 一致，
// 两处只改一处的话，浮层内容会在卡片还没到位时就出现（或反之）。
const WP_MS = 440;

// 点阵预览的几何：草稿周居中，左右按远近排开，**间距也随远近收窄** ——
// 中心一对最宽，往外每环收一点，边上的预览图被轻轻往中间带，
// 展开成一个扇形（等间距的话边上两张会显得太散）。
// 每张的位置/缩放全写在 transform 上，靠同一条 CSS 过渡滑到位；
// 锚点是容器的 50%，窗口缩放自动跟随，不需要监听 resize。
// WP_OFFSETS[d] = 第 d 环预览图中心到草稿中心的距离（px，本地坐标）。
const WP_VISIBLE_RADIUS = 3;                       // 两侧各 3 张 → 一屏最多 7 张
const WP_OFFSETS = [0, 120, 226, 320, 402, 472, 530];
const WP_SCALES = [1, .8, .64, .52, .44, .4, .38]; // 远近缩放（末档给藏起来的过渡用）
const WP_FADES = [1, .78, .6, .44, .34, .26, .2];

/** 根 zoom（设置页的字体大小缩放）。getBoundingClientRect 返回的是视觉
 *  像素，而给 position:fixed 元素写的 left/top 会被根 zoom 再乘一遍 ——
 *  所以视觉坐标必须除回 zoom 才不会在字体放大的机器上飞掉。 */
function wpZoom() {
  const z = parseFloat(document.documentElement.style.zoom);
  return (z >= 0.5 && z <= 2) ? z : 1;
}

async function weekOpenPicker() {
  if (WEEK_PICKER.open || WEEK_PICKER.animating) return;

  const r = await call('get_week_overview');
  if (!r || !r.weeks || !r.weeks.length) return;
  WEEK_PICKER.weeks = r.weeks;
  WEEK_PICKER.current = r.currentWeek;
  WEEK_PICKER.todayWeek = r.todayWeek;
  // 草稿从网格正在显示的那一周起步（还没渲染过就退回「本周」）
  WEEK_PICKER.draft = STATE.weekShown || r.currentWeek;

  const card = $('weekCard');
  const Z = wpZoom();
  const first = card.getBoundingClientRect();
  WEEK_PICKER.restoreRect = {
    left: first.left, top: first.top, width: first.width, height: first.height,
  };

  WEEK_PICKER.open = true;
  $('wpBackdrop').classList.add('show');
  // 展开后卡片不再是「按钮」，tooltip 一起摘掉，收回去再还原
  WEEK_PICKER.cardTitle = card.title;
  card.title = '';
  // 先钉在原位（视觉上和原来一模一样），下一帧再过渡到放大目标 ——
  // 不钉这一帧的话，static → fixed 的瞬间没有插值起点，会直接跳过去
  card.classList.add('picking');
  card.style.left = (first.left / Z) + 'px';
  card.style.top = (first.top / Z) + 'px';
  card.style.width = (first.width / Z) + 'px';
  card.style.height = (first.height / Z) + 'px';

  weekBuildCells();
  weekUpdateTitle(false);
  weekUpdateBackBtn();

  // 卡片被固定定位抽走了，流里留一个等高的占位 —— 收回去的落点按它现量，
  // 浮层开着时窗口怎么改大小都算数（restoreRect 只是量不到时的兜底）。
  const ph = document.createElement('div');
  ph.style.height = (first.height / Z) + 'px';   // 视觉 px 除回根 zoom = 本地 px
  ph.style.marginBottom = '14px';                // 和 .page-head 的下外边距一致
  ph.style.visibility = 'hidden';
  card.parentElement.insertBefore(ph, card);
  WEEK_PICKER.placeholder = ph;
  window.addEventListener('resize', weekOnResize);
  // 滚动锁走事件层而不是 overflow:hidden —— 滚动条留在原地，布局零位移
  document.addEventListener('wheel', weekScrollLock, { passive: false, capture: true });
  document.addEventListener('keydown', weekScrollKeyLock, { capture: true });

  // 强制回流，确保「钉在原位」这一帧真的画出来了
  card.getBoundingClientRect();
  card.classList.add('animating');
  weekApplyTargetGeometry();

  // 网格先淡出；动画尾段换上浮层内容淡入（比等完全停稳再换更顺）
  $('weekCardNormal').classList.add('fade-out');
  setTimeout(() => {
    $('weekCardNormal').style.display = 'none';
    const p = $('weekPicker');
    p.style.display = 'block';
    weekLayoutCells();
    requestAnimationFrame(() => p.classList.add('show'));
  }, WP_MS - 80);
}

/** 展开态的目标几何：几乎占满窗口（四周各留 4%）。开启和 resize 共用 ——
 *  不共用的话，浮层开着时把窗口拉大，卡片还停在开启瞬间的尺寸。 */
function weekApplyTargetGeometry() {
  const Z = wpZoom();
  const m = 0.04;   // 四周各留 4% —— 「几乎占满窗口」
  const vw = window.innerWidth, vh = window.innerHeight;
  const card = $('weekCard');
  card.style.left = (vw * m / Z) + 'px';
  card.style.top = (vh * m / Z) + 'px';
  card.style.width = (vw * (1 - 2 * m) / Z) + 'px';
  card.style.height = (vh * (1 - 2 * m) / Z) + 'px';
}

let weekResizeTimer = null;

/** 浮层开着时窗口变了：卡片几何跟着走。拖拽连发 resize 事件，逐帧过
 *  .44s 过渡会像橡皮筋 —— 期间摘掉过渡跟手，停手 120ms 后恢复（收回
 *  动画还要用它）。 */
function weekOnResize() {
  if (!WEEK_PICKER.open || WEEK_PICKER.animating) return;
  const card = $('weekCard');
  card.style.transition = 'none';
  weekApplyTargetGeometry();
  clearTimeout(weekResizeTimer);
  weekResizeTimer = setTimeout(() => { card.style.transition = ''; }, 120);
}

/** 滚动锁：浮层开着时不许背景滚。滚轮一律拦下（卡片虽是 fixed，
 *  仍是 .content 的 DOM 子节点，滚轮会透过它滚到背后的列表）。 */
function weekScrollLock(e) { e.preventDefault(); }

/** 键盘滚动的几条路也封掉（方向键/翻页键会滚最近的可滚祖先）。
 *  空格不拦 —— 它是按钮的「按下」。 */
function weekScrollKeyLock(e) {
  if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) {
    e.preventDefault();
  }
}

/** 收回去。[apply] 为真时把草稿周落成网格（网格在浮层还开着时就开始重画，
 *  和收回动画并行，缩回原位时新课表已经就位）。 */
function weekClosePicker(apply) {
  if (!WEEK_PICKER.open || WEEK_PICKER.animating) return;
  WEEK_PICKER.animating = true;
  WEEK_PICKER.hopToken++;        // 排队中的快滑步子全部作废
  WEEK_PICKER.hopTarget = null;

  const card = $('weekCard');
  $('weekPicker').classList.remove('show');
  setTimeout(() => {
    $('weekPicker').style.display = 'none';
    const normal = $('weekCardNormal');
    normal.style.display = '';
    normal.classList.remove('fade-out');
    // 落点按占位元素现量 —— 浮层开着时窗口可能改过大小，开启瞬间记的
    // restoreRect 早过时了；占位在正常流里，它的矩形就是页头此刻的位置
    const ph = WEEK_PICKER.placeholder;
    const r = ph ? ph.getBoundingClientRect() : WEEK_PICKER.restoreRect;
    const Z = wpZoom();
    card.style.left = (r.left / Z) + 'px';
    card.style.top = (r.top / Z) + 'px';
    card.style.width = (r.width / Z) + 'px';
    card.style.height = (r.height / Z) + 'px';
  }, 150);

  setTimeout(() => {
    card.classList.remove('picking', 'animating');
    card.style.left = card.style.top = card.style.width = card.style.height = '';
    card.title = WEEK_PICKER.cardTitle || '';
    $('wpBackdrop').classList.remove('show');
    window.removeEventListener('resize', weekOnResize);
    document.removeEventListener('wheel', weekScrollLock, { capture: true });
    document.removeEventListener('keydown', weekScrollKeyLock, { capture: true });
    clearTimeout(weekResizeTimer);
    if (WEEK_PICKER.placeholder) {
      WEEK_PICKER.placeholder.remove();
      WEEK_PICKER.placeholder = null;
    }
    WEEK_PICKER.open = false;
    WEEK_PICKER.animating = false;
  }, 150 + WP_MS);
}

/** 「确定」：草稿周变成真周次。offset 是相对「今天那周」的偏移
 *  （get_week 的口径），用未钳位的 todayWeek 换算 —— 开学前周次是 0，
 *  用钳位后的 currentWeek 会差一周。快滑还没落定时按 hopTarget 落。 */
function weekConfirm() {
  if (!WEEK_PICKER.open) return;
  const chosen = WEEK_PICKER.hopTarget ?? WEEK_PICKER.draft;
  if (chosen !== STATE.weekShown) {
    STATE.weekOffset = chosen - WEEK_PICKER.todayWeek;
    renderWeek();   // 不等它 —— 让数据加载和收回动画并行跑
  }
  weekClosePicker(true);
}

/** 点了某周的预览图：草稿切过去，各张滑到新的位置，标题跟着换 */
function weekPickWeek(week) {
  if (!WEEK_PICKER.open || week === WEEK_PICKER.draft) return;
  weekHopTo(week);
  // 标题直接写目标周 —— 快滑时 draft 要等步子跑才到位，读它拿到的是旧值
  weekUpdateTitle(true, week);
  weekUpdateBackBtn(week);
}

function weekBackToCurrent() {
  weekPickWeek(WEEK_PICKER.current);
}

function weekUpdateBackBtn(week) {
  $('wpBack').disabled = (week ?? WEEK_PICKER.draft) === WEEK_PICKER.current;
}

/**
 * 把草稿切到 [target]。跨度 ≤ 可见半径就是一步正常滑动；跨度大
 * （典型：「回到本周」从很远跳回）拆成 ≤3 的小步**快速**滑过去 ——
 *
 * 一步跨太大的话，旧的一排整体淡出、新的一排凭空浮现，中间没有任何
 * 过渡内容，就是「一半消失、另一半浮现」的怪相。小步快滑每一步都有
 * 完整的排面（相邻两个窗口至少共享一张预览图），读起来是「快速翻页」；
 * 跨度越大步子越密（480ms 总预算摊给所有步子，不会越远越久）。
 *
 * 中途又点了别处 / 关了浮层：hopToken 变化，排队的步子全部作废，
 * 新的快滑从当前 draft 重新起算。这期间点「确定」按 hopTarget 落周次。
 */
function weekHopTo(target) {
  const from = WEEK_PICKER.draft;
  const span = target - from;
  const n = Math.max(1, Math.ceil(Math.abs(span) / WP_VISIBLE_RADIUS));
  WEEK_PICKER.hopToken++;
  const token = WEEK_PICKER.hopToken;
  WEEK_PICKER.hopTarget = n === 1 ? null : target;

  if (n === 1) { WEEK_PICKER.draft = target; weekLayoutCells(); return; }

  const stepMs = Math.max(90, Math.min(160, Math.round(480 / n)));
  for (let i = 1; i <= n; i++) {
    const last = i === n;
    const draft = last ? target : Math.round(from + (span * i) / n);
    setTimeout(() => {
      if (token !== WEEK_PICKER.hopToken) return;
      WEEK_PICKER.draft = draft;
      weekSetHopSpeed(!last);
      weekLayoutCells();
      if (last) WEEK_PICKER.hopTarget = null;
    }, stepMs * (i - 1));
  }
}

/** 小步快滑的节奏：transform/opacity 改走短过渡；最后一步恢复默认节奏
 *  （.44s），落点带一点减速的「刹车」感。 */
function weekSetHopSpeed(fast) {
  const v = fast ? 'transform .16s cubic-bezier(.3, .6, .4, 1), opacity .12s ease' : '';
  Object.values(WEEK_PICKER.cells).forEach((c) => { c.style.transition = v; });
}

/** 建一次预览图，之后切周只改样式 —— 重建 DOM 的话就没有连续动画了。
 *  一张预览图 = 点阵矩阵（7 列 × 大节行，后端 pattern 给的形状）+ 下面的周数。 */
function weekBuildCells() {
  const wrap = $('wpDots');
  wrap.innerHTML = '';
  WEEK_PICKER.cells = {};
  WEEK_PICKER.weeks.forEach((w) => {
    const cell = el('button', 'wp-cell');
    cell.type = 'button';
    cell.dataset.week = w.week;
    cell.title = `第 ${w.week} 周`;

    const matrix = el('div', 'wp-matrix');
    const rows = w.pattern ? w.pattern[0].length : 0;
    // grid 逐行自动排位：外层扫大节行、内层扫星期列，取 pattern[日][行]
    for (let p = 0; p < rows; p++) {
      for (let d = 0; d < 7; d++) {
        matrix.appendChild(el('span', 'pd' + (w.pattern[d][p] === '1' ? ' lit' : '')));
      }
    }
    cell.appendChild(matrix);
    cell.appendChild(el('span', 'wp-num', w.week));
    cell.onclick = () => weekPickWeek(w.week);
    wrap.appendChild(cell);
    WEEK_PICKER.cells[w.week] = cell;
  });
}

/**
 * 摆点阵：草稿周居中，左右按 WP_OFFSETS 的收窄间距排开，每张按远近缩放、淡出。
 *
 * 位置、缩放全写在一张 transform 里（translate 锚定容器 50% + 环距，
 * scale 出近大远小），切周时每张靠同一条 CSS 过渡滑到自己的新位置 ——
 * 各张的位移方向一致（都是朝新中心一带），主体读起来仍是整排滑过去，
 * 边缘则多一分「收拢」的弹性。藏起来的也给外推档的缩放值：
 * 进场时从相邻档位过渡，不会从 1 突跳。
 */
function weekLayoutCells() {
  const strip = $('wpDots');
  if (!strip || !WEEK_PICKER.weeks.length) return;

  WEEK_PICKER.weeks.forEach((w) => {
    const c = WEEK_PICKER.cells[w.week];
    if (!c) return;
    const d = Math.abs(w.week - WEEK_PICKER.draft);
    const i = Math.min(d, WP_OFFSETS.length - 1);
    const sign = w.week < WEEK_PICKER.draft ? -1 : 1;
    const x = d === 0 ? 0 : sign * WP_OFFSETS[i];
    c.style.transform = `translate(calc(-50% + ${x}px), -50%) scale(${WP_SCALES[i]})`;
    c.style.opacity = d <= WP_VISIBLE_RADIUS ? WP_FADES[i] : '0';
    c.style.pointerEvents = d <= WP_VISIBLE_RADIUS ? '' : 'none';
    c.classList.toggle('sel', d === 0);
  });
}

/** 浮层正中的标题：第几周 + 日期范围。切周时淡出下滑换字再回来。
 *  [week] 不传就读当前 draft —— 大跨度快滑时必须显式传目标周。 */
function weekUpdateTitle(animate, week) {
  const t = $('wpTitle'), r = $('wpRange');
  const w = week ?? WEEK_PICKER.draft;
  const info = WEEK_PICKER.weeks[w - 1];
  const set = () => {
    t.textContent = `第 ${w} 教学周` + (w === WEEK_PICKER.current ? ' · 本周' : '');
    r.textContent = info ? info.rangeText : '';
  };
  if (!animate) { set(); return; }
  const box = t.parentElement;
  box.classList.add('switching');
  setTimeout(() => { set(); box.classList.remove('switching'); }, 170);
}
