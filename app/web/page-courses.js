/*
「课程编辑」页 —— 图形化增删改课程。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

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
