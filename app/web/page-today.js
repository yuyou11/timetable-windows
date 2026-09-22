/*
「今天」页 —— 此刻在做什么、还剩多久、明天预告、今日时间线。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

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
