"""
内置数据 —— 由 tools/gen_builtin.py 自动生成，**不要手工编辑**。

来源：手机版导出的 example-full.json（程序真实输出，不是手抄）。
要改内容请改手机版的内置数据后重新导出，再跑一次生成脚本。
"""

from __future__ import annotations

import json
from datetime import date

from .models import Block, Course, DayType, Kind

TERM_NAME = '示例大学 2026 级 · 大一上'
TERM_START = date.fromisoformat('2026-09-07')
TOTAL_WEEKS = 19

#: 内置课表。每项是一个 dict，字段与 JSON 格式一致
COURSES_RAW = [
    {'name': '大学英语', 'dayOfWeek': 1, 'nodes': [1, 2], 'weeks': '2-4,6-17', 'place': '教一-101'},
    {'name': '高等数学', 'dayOfWeek': 1, 'nodes': [3, 4], 'weeks': '2-4,6-17', 'place': '教一-203'},
    {'name': '中国近现代史纲要', 'dayOfWeek': 1, 'nodes': [5, 6], 'weeks': '15-17', 'place': '教二-105'},
    {'name': '军事理论', 'dayOfWeek': 1, 'nodes': [7, 8], 'weeks': '2-4,6-14', 'place': '教二-311'},
    {'name': '程序设计基础', 'dayOfWeek': 2, 'nodes': [3, 4], 'weeks': '2-7,9-17', 'place': '实验楼-201'},
    {'name': '大学物理实验', 'dayOfWeek': 2, 'nodes': [5, 6], 'weeks': '4', 'place': '实验楼-305'},
    {'name': '思想道德与法治', 'dayOfWeek': 2, 'nodes': [7, 8], 'weeks': '2-7,9-17', 'place': '教一-108'},
    {'name': '高等数学', 'dayOfWeek': 3, 'nodes': [1, 2], 'weeks': '2-7,9-17', 'place': '教一-203'},
    {'name': '形势与政策', 'dayOfWeek': 3, 'nodes': [3, 4], 'weeks': '5-7,9', 'place': '教二-220'},
    {'name': '中国近现代史纲要', 'dayOfWeek': 3, 'nodes': [7, 8], 'weeks': '13-17', 'place': '教二-105'},
    {'name': '人工智能导论', 'dayOfWeek': 3, 'nodes': [9, 10], 'weeks': '3-7,9-11', 'place': '教三-401'},
    {'name': '大学物理', 'dayOfWeek': 4, 'nodes': [3, 4], 'weeks': '2-3,5-17', 'place': '教二-108'},
    {'name': '大学生职业生涯规划', 'dayOfWeek': 4, 'nodes': [5, 6], 'weeks': '3-9/2', 'place': '教一-215'},
    {'name': '数据结构', 'dayOfWeek': 4, 'nodes': [7, 8], 'weeks': '3-17/2', 'place': '实验楼-201'},
    {'name': '程序设计基础', 'dayOfWeek': 4, 'nodes': [7, 8], 'weeks': '2', 'place': '实验楼-201'},
    {'name': '高等数学', 'dayOfWeek': 5, 'nodes': [3, 4], 'weeks': '2,5-16', 'place': '教一-203'},
    {'name': '思想道德与法治', 'dayOfWeek': 5, 'nodes': [5, 6], 'weeks': '2,5-12', 'place': '教一-108'},
    {'name': '大学体育', 'dayOfWeek': 5, 'nodes': [7, 8], 'weeks': '2,5-16', 'place': '操场 / 体育馆'},
    {'name': '音乐鉴赏', 'dayOfWeek': 5, 'nodes': [9, 10], 'weeks': '5-12', 'place': '教三-102'},
]

#: 六套作息模板的原始 JSON（保持原样嵌入，避免手工转写出错）
TEMPLATES_JSON = r'''
{
 "A": [
  {
   "start": "00:00",
   "end": "06:55",
   "title": "睡觉",
   "kind": "SLEEP"
  },
  {
   "start": "06:55",
   "end": "07:10",
   "title": "起床、洗漱",
   "note": "10 月后 06:30 恢复供电，正好接上"
  },
  {
   "start": "07:10",
   "end": "07:35",
   "title": "早餐",
   "note": "按你的实际节奏留 20 分钟",
   "kind": "MEAL"
  },
  {
   "start": "07:35",
   "end": "08:15",
   "title": "早读",
   "note": "英语单词 + 当天课程预习，40 分钟",
   "kind": "STUDY"
  },
  {
   "start": "08:15",
   "end": "08:30",
   "title": "前往教室",
   "note": "15 分钟缓冲，够走到 EI 楼或 B 楼",
   "kind": "TRANSIT"
  },
  {
   "start": "08:30",
   "end": "10:05",
   "title": "第 1-2 节",
   "note": "自习 / 预习",
   "kind": "CLASS",
   "nodes": [
    1,
    2
   ]
  },
  {
   "start": "10:05",
   "end": "10:25",
   "title": "大课间",
   "note": "全天最长课间",
   "kind": "FREE"
  },
  {
   "start": "10:25",
   "end": "12:00",
   "title": "第 3-4 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    3,
    4
   ]
  },
  {
   "start": "12:00",
   "end": "12:40",
   "title": "午餐",
   "kind": "MEAL"
  },
  {
   "start": "12:40",
   "end": "13:30",
   "title": "午睡（硬性）",
   "note": "周三有晚课，这觉必须睡",
   "kind": "SLEEP"
  },
  {
   "start": "13:30",
   "end": "14:00",
   "title": "醒神 + 前往教室",
   "kind": "TRANSIT"
  },
  {
   "start": "14:00",
   "end": "15:35",
   "title": "第 5-6 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    5,
    6
   ]
  },
  {
   "start": "15:35",
   "end": "15:55",
   "title": "下午课间",
   "note": "第二个大课间",
   "kind": "FREE"
  },
  {
   "start": "15:55",
   "end": "17:30",
   "title": "第 7-8 节",
   "note": "自习 / 轻松跑",
   "kind": "CLASS",
   "nodes": [
    7,
    8
   ]
  },
  {
   "start": "17:30",
   "end": "18:30",
   "title": "晚餐 + 散步",
   "note": "热水 17:30 起，可先洗澡再吃饭",
   "kind": "MEAL"
  },
  {
   "start": "18:30",
   "end": "19:00",
   "title": "当日复盘 + 明日待办",
   "note": "10 分钟写清",
   "kind": "STUDY"
  },
  {
   "start": "19:00",
   "end": "20:35",
   "title": "第 9-10 节",
   "note": "晚自习",
   "kind": "CLASS",
   "nodes": [
    9,
    10
   ]
  },
  {
   "start": "20:35",
   "end": "21:40",
   "title": "晚自习",
   "note": "当天作业清零，65 分钟，做完就停",
   "kind": "STUDY"
  },
  {
   "start": "21:40",
   "end": "22:40",
   "title": "★ 自由时间",
   "note": "游戏 / 番剧 / 聊天，到点就玩，不设条件",
   "kind": "FREE"
  },
  {
   "start": "22:40",
   "end": "23:10",
   "title": "洗漱、收尾",
   "note": "热水供应至 23:30"
  },
  {
   "start": "23:10",
   "end": "24:00",
   "title": "睡觉",
   "note": "23:30 关楼门、熄灯",
   "kind": "SLEEP"
  }
 ],
 "B_TRAIN_A": [
  {
   "start": "00:00",
   "end": "07:25",
   "title": "睡觉",
   "kind": "SLEEP"
  },
  {
   "start": "07:25",
   "end": "07:40",
   "title": "起床、洗漱",
   "note": "比 A 型晚 30 分钟"
  },
  {
   "start": "07:40",
   "end": "08:00",
   "title": "早餐",
   "note": "20 分钟",
   "kind": "MEAL"
  },
  {
   "start": "08:00",
   "end": "10:05",
   "title": "★ 黄金自习块",
   "note": "图书馆或空教室，2 小时 5 分整块不打断",
   "kind": "STUDY"
  },
  {
   "start": "10:05",
   "end": "10:25",
   "title": "大课间：收拾换楼",
   "kind": "TRANSIT"
  },
  {
   "start": "10:25",
   "end": "12:00",
   "title": "第 3-4 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    3,
    4
   ]
  },
  {
   "start": "12:00",
   "end": "12:40",
   "title": "午餐",
   "kind": "MEAL"
  },
  {
   "start": "12:40",
   "end": "13:30",
   "title": "午睡（硬性）",
   "note": "下午连堂，必须睡",
   "kind": "SLEEP"
  },
  {
   "start": "13:30",
   "end": "14:00",
   "title": "醒神 + 前往教室",
   "kind": "TRANSIT"
  },
  {
   "start": "14:00",
   "end": "15:35",
   "title": "第 5-6 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    5,
    6
   ]
  },
  {
   "start": "15:35",
   "end": "15:55",
   "title": "下午课间",
   "kind": "FREE"
  },
  {
   "start": "15:55",
   "end": "17:30",
   "title": "第 7-8 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    7,
    8
   ]
  },
  {
   "start": "17:30",
   "end": "18:15",
   "title": "晚餐（七分饱）",
   "note": "训练前 1.5 小时吃完",
   "kind": "MEAL"
  },
  {
   "start": "18:15",
   "end": "18:45",
   "title": "消食 + 换装备"
  },
  {
   "start": "18:45",
   "end": "19:00",
   "title": "前往场地",
   "note": "路上算热身",
   "kind": "TRANSIT"
  },
  {
   "start": "19:00",
   "end": "20:20",
   "title": "★ 训练 80 分钟",
   "note": "力量 A：推 / 上肢（卧推、肩推、划船）热身10+力量60+拉伸10",
   "kind": "TRAIN"
  },
  {
   "start": "20:20",
   "end": "20:50",
   "title": "洗澡",
   "note": "博远有淋浴间，洗完再骑车回宿舍"
  },
  {
   "start": "20:50",
   "end": "21:40",
   "title": "晚自习 50 分钟",
   "note": "训练日只做当天作业",
   "kind": "STUDY"
  },
  {
   "start": "21:40",
   "end": "22:40",
   "title": "★ 自由时间",
   "note": "训练日也不取消",
   "kind": "FREE"
  },
  {
   "start": "22:40",
   "end": "23:10",
   "title": "洗漱、收尾"
  },
  {
   "start": "23:10",
   "end": "24:00",
   "title": "睡觉",
   "note": "此档睡眠 8 小时 15 分",
   "kind": "SLEEP"
  }
 ],
 "B_TRAIN_B": [
  {
   "start": "00:00",
   "end": "07:25",
   "title": "睡觉",
   "kind": "SLEEP"
  },
  {
   "start": "07:25",
   "end": "07:40",
   "title": "起床、洗漱",
   "note": "比 A 型晚 30 分钟"
  },
  {
   "start": "07:40",
   "end": "08:00",
   "title": "早餐",
   "note": "20 分钟",
   "kind": "MEAL"
  },
  {
   "start": "08:00",
   "end": "10:05",
   "title": "★ 黄金自习块",
   "note": "图书馆或空教室，2 小时 5 分整块不打断",
   "kind": "STUDY"
  },
  {
   "start": "10:05",
   "end": "10:25",
   "title": "大课间：收拾换楼",
   "kind": "TRANSIT"
  },
  {
   "start": "10:25",
   "end": "12:00",
   "title": "第 3-4 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    3,
    4
   ]
  },
  {
   "start": "12:00",
   "end": "12:40",
   "title": "午餐",
   "kind": "MEAL"
  },
  {
   "start": "12:40",
   "end": "13:30",
   "title": "午睡（硬性）",
   "note": "下午连堂，必须睡",
   "kind": "SLEEP"
  },
  {
   "start": "13:30",
   "end": "14:00",
   "title": "醒神 + 前往教室",
   "kind": "TRANSIT"
  },
  {
   "start": "14:00",
   "end": "15:35",
   "title": "第 5-6 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    5,
    6
   ]
  },
  {
   "start": "15:35",
   "end": "15:55",
   "title": "下午课间",
   "kind": "FREE"
  },
  {
   "start": "15:55",
   "end": "17:30",
   "title": "第 7-8 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    7,
    8
   ]
  },
  {
   "start": "17:30",
   "end": "18:15",
   "title": "晚餐（七分饱）",
   "note": "训练前 1.5 小时吃完",
   "kind": "MEAL"
  },
  {
   "start": "18:15",
   "end": "18:45",
   "title": "消食 + 换装备"
  },
  {
   "start": "18:45",
   "end": "19:00",
   "title": "前往场地",
   "note": "路上算热身",
   "kind": "TRANSIT"
  },
  {
   "start": "19:00",
   "end": "20:20",
   "title": "★ 训练 80 分钟",
   "note": "力量 B：拉 / 下肢（深蹲、硬拉、引体辅助）热身10+力量60+拉伸10",
   "kind": "TRAIN"
  },
  {
   "start": "20:20",
   "end": "20:50",
   "title": "洗澡",
   "note": "博远有淋浴间，洗完再骑车回宿舍"
  },
  {
   "start": "20:50",
   "end": "21:40",
   "title": "晚自习 50 分钟",
   "note": "训练日只做当天作业",
   "kind": "STUDY"
  },
  {
   "start": "21:40",
   "end": "22:40",
   "title": "★ 自由时间",
   "note": "训练日也不取消",
   "kind": "FREE"
  },
  {
   "start": "22:40",
   "end": "23:10",
   "title": "洗漱、收尾"
  },
  {
   "start": "23:10",
   "end": "24:00",
   "title": "睡觉",
   "note": "此档睡眠 8 小时 15 分",
   "kind": "SLEEP"
  }
 ],
 "B_NORMAL": [
  {
   "start": "00:00",
   "end": "07:25",
   "title": "睡觉",
   "kind": "SLEEP"
  },
  {
   "start": "07:25",
   "end": "07:40",
   "title": "起床、洗漱"
  },
  {
   "start": "07:40",
   "end": "08:00",
   "title": "早餐",
   "kind": "MEAL"
  },
  {
   "start": "08:00",
   "end": "10:05",
   "title": "★ 黄金自习块",
   "note": "2 小时 5 分整块",
   "kind": "STUDY"
  },
  {
   "start": "10:05",
   "end": "10:25",
   "title": "大课间：收拾换楼",
   "kind": "TRANSIT"
  },
  {
   "start": "10:25",
   "end": "12:00",
   "title": "第 3-4 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    3,
    4
   ]
  },
  {
   "start": "12:00",
   "end": "12:40",
   "title": "午餐",
   "kind": "MEAL"
  },
  {
   "start": "12:40",
   "end": "13:30",
   "title": "午睡（硬性）",
   "kind": "SLEEP"
  },
  {
   "start": "13:30",
   "end": "14:00",
   "title": "醒神 + 前往教室",
   "kind": "TRANSIT"
  },
  {
   "start": "14:00",
   "end": "15:35",
   "title": "第 5-6 节",
   "note": "自习",
   "kind": "CLASS",
   "nodes": [
    5,
    6
   ]
  },
  {
   "start": "15:35",
   "end": "15:55",
   "title": "下午课间",
   "kind": "FREE"
  },
  {
   "start": "15:55",
   "end": "17:30",
   "title": "第 7-8 节",
   "note": "自习 / 运动",
   "kind": "CLASS",
   "nodes": [
    7,
    8
   ]
  },
  {
   "start": "17:30",
   "end": "18:30",
   "title": "晚餐 + 散步",
   "kind": "MEAL"
  },
  {
   "start": "18:30",
   "end": "19:00",
   "title": "当日复盘 + 明日待办",
   "kind": "STUDY"
  },
  {
   "start": "19:00",
   "end": "20:35",
   "title": "第 9-10 节",
   "note": "晚自习",
   "kind": "CLASS",
   "nodes": [
    9,
    10
   ]
  },
  {
   "start": "20:35",
   "end": "21:40",
   "title": "晚自习",
   "note": "当天作业清零",
   "kind": "STUDY"
  },
  {
   "start": "21:40",
   "end": "22:40",
   "title": "★ 自由时间",
   "note": "本周最稳定的一块，不设条件",
   "kind": "FREE"
  },
  {
   "start": "22:40",
   "end": "23:10",
   "title": "洗漱、收尾"
  },
  {
   "start": "23:10",
   "end": "24:00",
   "title": "睡觉",
   "kind": "SLEEP"
  }
 ],
 "SATURDAY": [
  {
   "start": "00:00",
   "end": "09:00",
   "title": "睡觉",
   "note": "一周唯一可以睡到自然醒的一天",
   "kind": "SLEEP"
  },
  {
   "start": "09:00",
   "end": "09:30",
   "title": "自然醒（不设闹钟）"
  },
  {
   "start": "09:30",
   "end": "10:30",
   "title": "早餐 / 早午餐",
   "note": "下午要练，吃扎实一点，别凑合",
   "kind": "MEAL"
  },
  {
   "start": "10:30",
   "end": "12:30",
   "title": "★ 自由块 1",
   "note": "游戏 / 番剧，整块不打断",
   "kind": "FREE"
  },
  {
   "start": "12:30",
   "end": "13:30",
   "title": "午餐（七分饱）",
   "note": "离训练还有 1.5 小时",
   "kind": "MEAL"
  },
  {
   "start": "13:30",
   "end": "14:45",
   "title": "午休 + 消食",
   "note": "别吃完就去跑",
   "kind": "SLEEP"
  },
  {
   "start": "14:45",
   "end": "15:00",
   "title": "换装备、前往操场",
   "note": "跑鞋 + 水",
   "kind": "TRANSIT"
  },
  {
   "start": "15:00",
   "end": "16:40",
   "title": "★ 训练 100 分钟",
   "note": "热身10 + 跑步40~60 + 核心15 + 拉伸10",
   "kind": "TRAIN"
  },
  {
   "start": "16:40",
   "end": "17:30",
   "title": "洗澡",
   "note": "热水供应至 23:30"
  },
  {
   "start": "17:30",
   "end": "18:30",
   "title": "晚餐",
   "kind": "MEAL"
  },
  {
   "start": "18:30",
   "end": "22:30",
   "title": "★ 自由块 2",
   "note": "本周最大的一块，游戏 / 电影 / 开黑，随便",
   "kind": "FREE"
  },
  {
   "start": "22:30",
   "end": "23:30",
   "title": "洗漱、聊天、睡前刷手机"
  },
  {
   "start": "23:30",
   "end": "24:00",
   "title": "睡觉",
   "note": "次日想 08:30 起，就别超过 24:00",
   "kind": "SLEEP"
  }
 ],
 "SUNDAY": [
  {
   "start": "00:00",
   "end": "08:30",
   "title": "睡觉",
   "kind": "SLEEP"
  },
  {
   "start": "08:30",
   "end": "09:00",
   "title": "起床、洗漱"
  },
  {
   "start": "09:00",
   "end": "09:40",
   "title": "早餐",
   "kind": "MEAL"
  },
  {
   "start": "09:40",
   "end": "11:40",
   "title": "高数周预习（2 小时）",
   "note": "全周性价比最高的 2 小时",
   "kind": "STUDY"
  },
  {
   "start": "11:40",
   "end": "13:30",
   "title": "午餐 + 午休",
   "kind": "MEAL"
  },
  {
   "start": "13:30",
   "end": "17:00",
   "title": "★ 自由块",
   "note": "整块，不打断，别切成碎片",
   "kind": "FREE"
  },
  {
   "start": "17:00",
   "end": "18:30",
   "title": "晚餐 + 洗澡",
   "kind": "MEAL"
  },
  {
   "start": "18:30",
   "end": "19:00",
   "title": "周复盘 + 下周待办",
   "note": "顺便确认下周有没有调课",
   "kind": "STUDY"
  },
  {
   "start": "19:00",
   "end": "21:00",
   "title": "晚自习：补作业 + 周一英语单词",
   "note": "2 小时，为早八做准备",
   "kind": "STUDY"
  },
  {
   "start": "21:00",
   "end": "22:40",
   "title": "★ 自由块",
   "note": "游戏 / 放松，收个尾",
   "kind": "FREE"
  },
  {
   "start": "22:40",
   "end": "23:10",
   "title": "洗漱"
  },
  {
   "start": "23:10",
   "end": "24:00",
   "title": "睡觉",
   "note": "保证周一 06:55 起得来",
   "kind": "SLEEP"
  }
 ]
}
'''


def _weeks_from_text(text: str) -> frozenset[int]:
    """把 '2-4,6-17' 这类写法展开成集合（复用正式解析器，避免两套逻辑）"""
    from .format_spec import parse_weeks
    return parse_weeks(text, TOTAL_WEEKS)


def courses() -> list[Course]:
    """构造内置课表。每次调用返回新的对象，避免被就地修改污染"""
    out: list[Course] = []
    for i, raw in enumerate(COURSES_RAW):
        start_node, end_node = raw['nodes']
        out.append(Course(
            name=raw['name'],
            day_of_week=raw['dayOfWeek'],
            start_node=start_node,
            end_node=end_node,
            weeks=_weeks_from_text(raw['weeks']),
            place=raw.get('place', ''),
            enabled=raw.get('enabled', True),
            id=f'c{i + 1:03d}',
        ))
    return out


def templates() -> dict[DayType, list[Block]]:
    """构造内置模板。延迟到调用时才解析，避免 import 时就依赖 format_spec"""
    from .format_spec import _parse_templates
    raw = json.loads(TEMPLATES_JSON)
    merged = _parse_templates(raw, [])
    merged[DayType.REST] = _REST_DAY_TEMPLATE
    return merged


# 「无课休息日」模板 —— **电脑版特有**，不是从手机版导出的。
#
# 引擎把「全天没课的工作日」判为 REST（假期、停课、课表没排到的日子），
# 那天按这一套过：没有课程格子、没有晚自修，全是自由块。
# REST 不在 DAY_TYPE_ORDER 里（不能出现在 JSON 文件中），所以这套模板
# 只能在代码里构造，走不了 TEMPLATES_JSON。
_REST_DAY_TEMPLATE = [
    Block(0, 9 * 60, '睡觉', kind=Kind.SLEEP),
    Block(9 * 60, 9 * 60 + 30, '起床、洗漱'),
    Block(9 * 60 + 30, 10 * 60 + 15, '早餐', kind=Kind.MEAL),
    Block(10 * 60 + 15, 12 * 60, '★ 自由块', note='没课的日子，做点自己想做的事', kind=Kind.FREE),
    Block(12 * 60, 12 * 60 + 40, '午餐', kind=Kind.MEAL),
    Block(12 * 60 + 40, 13 * 60 + 40, '午休', note='闭眼躺一会儿，不用睡着', kind=Kind.SLEEP),
    Block(13 * 60 + 40, 17 * 60 + 30, '★ 自由块', note='整块时间，别切成碎片', kind=Kind.FREE),
    Block(17 * 60 + 30, 18 * 60 + 30, '晚餐 + 散步', kind=Kind.MEAL),
    Block(18 * 60 + 30, 22 * 60 + 30, '★ 自由块', note='晚上没有晚自修，放松、收尾都行', kind=Kind.FREE),
    Block(22 * 60 + 30, 23 * 60 + 30, '洗漱、聊天、睡前刷手机'),
    Block(23 * 60 + 30, 24 * 60, '睡觉', note='没有闹钟的一天也别熬太晚', kind=Kind.SLEEP),
]
