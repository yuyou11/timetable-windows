/* ==========================================================================
   时间规划表 · 电脑版 前端逻辑
   ==========================================================================

   和后端的分工：

     后端（Python）  算时间轴、读写文件、校验数据
     前端（这里）    只负责画界面、收集用户输入

   **所有判断「对不对」的逻辑都在后端**，前端不重复实现一遍。
   否则两边规则一旦不一致，就会出现「前端说能存、后端说不行」的怪现象。

   调用后端： pywebview.api.方法名(参数)  →  返回 Promise
   ========================================================================== */

'use strict';

jslog('app.js 已加载');

let STATE = {
  term: null,
  settings: null,
  courses: [],
  templates: null,
  activeType: 'A',
  weekOffset: 0,
};
/* ⚠️ 这张表 ball.js 里还有一份，**两边的值必须一模一样**。
 *
 * 为什么是复制而不是共享：前端没有模块系统（两个页面各自独立加载脚本），
 * 拿不到同一个常量。那就只能复制，然后**用一条测试盯着它** ——
 * 否则迟早漂移，而且漂移了看不出来。
 *
 * 实测漂移过一次：这边 CLASS 是 #3b82f6、TRANSIT 是 #cbd5e1，
 * 悬浮窗那边是 #2563eb 和 #94a3b8 —— 同一门课在两个窗口显示两种颜色。
 * 现在统一成**较深的那一套**。
 *
 * 为什么取深的那一套，而不是「主界面这一套」：悬浮窗上同一个色值既要当
 * 7px 小圆点的背景、又要当剩余时间的**文字颜色**，而文字在白色卡片上用
 * #cbd5e1 几乎看不见。深色在两个场景都能用，浅色只有主窗口能用 ——
 * **消除漂移时要选那个两边都能工作的值**，而不是简单选"主要的那个"。
 *
 * 核对它的测试：tests/test_ball_ui.py 里那条 KIND_COLOR 一致性用例。 */
const KIND_COLOR = {
  SLEEP: '#78909c', MEAL: '#f59e0b', CLASS: '#2563eb', STUDY: '#8b5cf6',
  TRAIN: '#ef4444', FREE: '#22c55e', CHORE: '#14b8a6', TRANSIT: '#94a3b8',
};

const KIND_LABEL = {
  SLEEP: '睡觉', MEAL: '吃饭', CLASS: '上课', STUDY: '自习',
  TRAIN: '运动', FREE: '自由', CHORE: '杂事', TRANSIT: '路上',
};
// ==========================================================================
//  页面切换
// ==========================================================================

function switchPage(name) {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.classList.toggle('active', b.dataset.page === name);
  });
  document.querySelectorAll('.page').forEach((p) => {
    p.classList.toggle('active', p.id === 'page-' + name);
  });

  if (name === 'today') renderToday();
  if (name === 'week') renderWeek();
  if (name === 'courses') renderCourses();
  if (name === 'templates') renderTemplates();
  if (name === 'settings') renderSettings();
}
// ==========================================================================
//  事件绑定
// ==========================================================================

function bindEvents() {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.onclick = () => switchPage(b.dataset.page);
  });

  $('btnRefreshToday').onclick = renderToday;

  // 课表翻周
  $('prevWeek').onclick = () => { STATE.weekOffset--; renderWeek(); };
  $('nextWeek').onclick = () => { STATE.weekOffset++; renderWeek(); };
  $('curWeek').onclick = () => { STATE.weekOffset = 0; renderWeek(); };

  // 课程
  $('addCourse').onclick = () => courseDialog(null);
  $('clearCourses').onclick = async () => {
    if (!await confirmBox('清空所有课程',
      '会删掉全部课程，课表变空。\n\n建议先「导出课表」备份 —— 清空之后只能靠导入恢复，内置课表可以用「恢复内置」找回来。',
      '确认清空', true)) return;
    const r = await call('clear_courses');
    if (r.ok) { STATE.courses = []; renderCourses(); toast(r.message); }
  };
  $('resetCourses').onclick = async () => {
    if (!await confirmBox('恢复内置课表', '会用内置的示例课表替换当前课表（示例大学 2026 级·大一上，19 门课）。', '恢复')) return;
    const r = await call('reset_courses');
    if (r.ok) { STATE.courses = r.courses; renderCourses(); toast(r.message); }
  };

  // 作息
  $('addBlock').onclick = () => blockDialog(STATE.activeType, null, -1);
  $('changeWake').onclick = () => wakeDialog();
  $('resetTemplates').onclick = async () => {
    if (!await confirmBox('恢复内置作息模板',
      '会丢弃你改过的作息，重新使用内置的六套模板。\n\n课表不受影响。', '恢复')) return;
    const r = await call('reset_templates');
    if (r.ok) { STATE.templates = r.templates; renderTemplates(); toast(r.message); }
  };

  // 数据
  $('exportSimple').onclick = () => exportFile(false);
  $('exportFull').onclick = () => exportFile(true);
  $('copyJson').onclick = async () => {
    // ⚠️ 用 hasCustomScheduleConfig，**不能用 hasCustomTemplates**。
    // 后者在「只改了日型、没改模板」时是 false，复制出来的 JSON 不含 dayTypes ——
    // 用户那份日型设置就静默消失了（手机版踩过这个坑）。
    const r = await call('copy_json',
      STATE.settings && STATE.settings.hasCustomScheduleConfig);
    if (r.ok) {
      navigator.clipboard.writeText(r.text).then(
        () => toast(r.message),
        () => promptDialog('复制失败，请手动复制', r.text)
      );
    }
  };
  $('importFile').onclick = doImport;
  $('openFolder').onclick = async () => { const r = await call('open_data_folder'); if (r.ok) toast('已打开'); };
  $('copyPromptCourse').onclick = () => showPrompt('course', '课表提示词');
  $('copyPromptTemplate').onclick = () => showPrompt('template', '作息模板提示词');
  $('copyPromptFix').onclick = () => showPrompt('fix', '报错修复提示词');

  // 设置
  $('swBall').onchange = async (e) => {
    const r = await call('set_ball_enabled', e.target.checked);
    STATE.settings = r;
    updateStatusPill(r);
    toast(e.target.checked ? '悬浮窗已开启' : '悬浮窗已关闭');
  };

  document.querySelectorAll('[data-lead]').forEach((btn) => {
    btn.onclick = async () => {
      const steps = [0, 5, 10, 15, 20, 30];
      const cur = STATE.settings ? STATE.settings.remindLead : 0;
      let idx = steps.indexOf(cur);
      if (idx < 0) idx = 0;
      idx = Math.max(0, Math.min(steps.length - 1, idx + Number(btn.dataset.lead)));
      const r = await call('set_remind_lead', steps[idx]);
      STATE.settings = r;
      $('leadValue').textContent = r.remindLead === 0 ? '关闭' : r.remindLead + ' 分钟';
      updateStatusPill(r);
    };
  });

  $('saveTerm').onclick = async () => {
    const r = await call('set_term',
      $('termName').value, $('termStart').value, Number($('termWeeks').value));
    toast(r.message);
    if (r.ok) {
      STATE.term = r.term;
      // 改了学期，课表页的周次全变了，位置也要重置
      STATE.weekOffset = 0;
      await refreshAll();
    }
  };

  // 日型策略
  $('saveDayTypes').onclick = async () => {
    const enabled = [...document.querySelectorAll('#dayTypeList input:checked')]
      .map((cb) => cb.dataset.key);
    const r = await call('save_day_types', enabled, $('dayTypeFallback').value);
    if (!r.ok) {
      // 校验失败（比如一种都没勾）用弹窗而不是 toast ——
      // 报错里写了「该怎么办」，一闪而过的提示读不完
      await modal({ title: '保存失败', body: r.message, buttons: [{ label: '知道了', value: 1, primary: true }] });
      return;
    }
    toast(r.message);
    await renderDayTypes();
    await refreshAll();
  };

  $('resetDayTypes').onclick = async () => {
    if (!await confirmBox('恢复默认日型',
      '会改成默认那四种：有早八的 A 型、没早八的 B 型、周六、周日。\n\n' +
      '训练日（周二力量A、周四力量B）的细分会被关掉 —— '
      + '它们来自某一份具体规划表，不是通用规律。\n\n课表和模板都不受影响。',
      '恢复')) return;
    const r = await call('reset_day_types');
    if (r.ok) {
      if (r.settings) { STATE.settings = r.settings; updateStatusPill(r.settings); }
      await renderDayTypes();
      await refreshAll();
      toast(r.message);
    }
  };
}

// ==========================================================================
//  启动
// ==========================================================================

async function refreshAll() {
  await renderToday();
  const t = await call('bootstrap');
  STATE.term = t.term;
  STATE.settings = t.settings;
  updateStatusPill(t.settings);
}

let booted = false;

async function boot() {
  // 防止两条触发路径都跑一遍（下面用的是「两个都接」的写法）
  if (booted) return;
  booted = true;
  jslog('boot() 开始');

  const data = await call('bootstrap');
  jslog('bootstrap 返回：' + (data ? ('term=' + (data.term ? data.term.name : '无')) : '空'));
  if (!data || !data.term) return;

  STATE.term = data.term;
  STATE.settings = data.settings;
  STATE.templates = null;   // 让作息页第一次打开时去后端取

  $('brandSub').textContent = `第 ${data.term.todayWeek} 周`;
  updateStatusPill(data.settings);

  bindEvents();
  switchPage('today');
  jslog('界面初始化完成');

  // 首次启动：问一句用内置示例还是从空白开始。
  // 这个程序是要发给同学用的，别的学校的同学装上之后会看到一堆跟自己无关的课 ——
  // 给一个明确的开场选择，比让他自己去设置里找要友好得多。
  if (data.isFirstLaunch) {
    const blank = await modal({
      title: '第一次使用',
      body:
        '这个程序内置了一份示例课表（示例大学 2026 级 · 大一上），' +
        '包含 19 门课和大学作息模板。\n\n' +
        '它不是你的课表，只是一个能看清界面的样例 —— 建议选「从空白开始」，' +
        '然后到「导入导出」里导入自己的课表，或者用 AI 提示词生成一份。\n\n' +
        '选错了也没关系，随时可以清空或恢复。',
      buttons: [
        { label: '用内置示例', value: false },
        { label: '从空白开始', value: true, primary: true },
      ],
    });
    if (blank) {
      await call('clear_courses');
    }
    await call('mark_launched');
    await refreshAll();
  }

  // 每分钟刷新一次「此刻」——只在界面开着时才跑，关掉窗口就没了。
  // 这和手机版的思路一样：**不做 7×24 的后台轮询**。
  setInterval(() => {
    if ($('page-today').classList.contains('active')) renderToday();
  }, 60000);
}

/* --------------------------------------------------------------------------
   启动时机：两条路都要接上

   pywebview 注入 API 需要一点时间，所以通常要等 pywebviewready 事件。
   但这里有个**竞态**：如果页面加载得比 API 注入还快，事件会在
   addEventListener 注册之前就发完，于是 boot() 永远等不到 ——
   表现是「窗口开着，但界面一片空白、什么数据都没有」。

   打包成 exe 之后页面是从本地磁盘读的，比开发时更快，这个竞态更容易触发。

   所以两个都接：已经注入好了就直接启动，还没好就等事件。
   这是处理「一次性事件 + 可能已经错过」的标准写法。
   -------------------------------------------------------------------------- */
if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener('pywebviewready', boot);
}

/* --------------------------------------------------------------------------
   出错时留下痕迹

   前端的异常默认只出现在开发者工具里。用户看不到，我们也拿不到 ——
   于是「点了没反应」这种问题就完全无从查起。

   这里把 JS 的报错转发给 Python，写进日志文件。
   -------------------------------------------------------------------------- */
window.addEventListener('error', (e) => {
  try {
    pywebview.api.log_error(`${e.message} @ ${e.filename}:${e.lineno}`);
  } catch (_) { /* 连日志都发不出去就算了，不能再抛一个错 */ }
});

window.addEventListener('unhandledrejection', (e) => {
  try {
    const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
    pywebview.api.log_error(`未处理的 Promise 拒绝：${msg}`);
  } catch (_) { /* 同上 */ }
});
