/*
「导入导出」页 —— 和手机版交换数据、AI 提示词。

从 app.js 拆出来（tools/split_app_js.py），内容一字未改。
   ========================================================================== */

'use strict';

// ==========================================================================
//  导入导出
// ==========================================================================

// includeScheduleConfig = true 时带上**完整的作息配置**：
// 展开的六套模板 + 启用的日型策略。两者必须一起带走 ——
// 只带模板不带日型的话，别人导入后拿到的日型和你的不一样
// （同一份模板，你只启用四种，他却六种全开）。
async function exportFile(includeScheduleConfig) {
  const r = await call('export_to_file', includeScheduleConfig);
  if (r.cancelled) return;
  toast(r.message);
}

async function doImport() {
  const r = await call('import_from_file');
  if (r.cancelled) return;
  if (!r.ok) { await modal({ title: '导入失败', body: r.message }); return; }

  const p = r.preview;
  const lines = [];

  lines.push(p.hasTerm
    ? `学期：${p.termName}\n起始：${p.termStart}（周一）\n总周数：${p.totalWeeks}`
    : '学期：文件里没写，保持不变');

  lines.push(p.coursesUnchanged
    ? '课程：文件里没写，保持不变'
    : `课程：${p.courseCount} 门`);

  if (p.templatesUnchanged) {
    lines.push('作息模板：文件里没写，保持不变');
  } else {
    const t = p.templateLines
      .map((x) => `· ${x.key}${x.wake ? '　' + x.wake + ' 起床' : ''}`)
      .join('\n');
    lines.push(`作息模板：将替换 ${p.templateCount} 种日型\n${t}`);
  }

  // 启用了哪些日型（v3）。带上起床时间 —— 这是用户最想核对的那件事：
  // 「我说好 7 点起，到底给我排的几点」。
  if (p.dayTypesUnchanged) {
    lines.push('启用日型：文件里没写，保持不变');
  } else {
    const d = p.dayTypeLines
      .map((x) => `· ${x.key}${x.wake ? '　' + x.wake + ' 起床' : ''}`)
      .join('\n');
    lines.push(
      `启用日型：${p.dayTypeEnabled.length} 种\n${d}\n` +
      `没启用的日子会回落到 ${p.dayTypeFallback}`
    );
  }

  if (p.warnings.length) {
    lines.push(`\n⚠️ ${p.warnings.length} 条提醒：\n` +
      p.warnings.slice(0, 5).map((w) => '· ' + w).join('\n'));
  }

  const buttons = [{ label: '取消', value: null }];
  if (!p.coursesUnchanged) {
    buttons.push({ label: '合并', value: 'merge' });
    buttons.push({ label: '替换现有课表', value: 'replace', primary: true });
  } else {
    // 文件里没带课程时，「替换 / 合并」没有意义 ——
    // 给两个长得一样的按钮只会让人以为自己在做选择
    buttons.push({ label: '应用', value: 'merge', primary: true });
  }

  const choice = await modal({ title: '确认导入', body: lines.join('\n\n'), buttons });
  if (!choice) return;

  const res = await call('confirm_import', choice);
  toast(res.message);
  if (res.ok) {
    await refreshAll();
  }
}

async function showPrompt(kind, title) {
  const r = await call('get_ai_prompt', kind);
  if (!r.ok) { toast('取提示词失败'); return; }
  await promptDialog(title, r.text);
}
