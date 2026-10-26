#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★多音字自动校正（2026-10-13）—— 配音前按文案含义校正 F5/pypinyin 的错读
─────────────────────────────────────────────────────────────────
背景：F5 的发音链路 = jieba 分词 → pypinyin lazy_pinyin，pypinyin 对多音字
      按高频读音猜，实测翻车：「多**囤**几瓶」读成 dùn（炖音，2026-10-13 烟酰胺）。
机制：两级校正，均只作用于「喂给 TTS 的文本」，字幕/tokens 用原文，不受影响。
  ① 同音字替换（map 值为字符串）：把错读字换成读音唯一的同音字。
     例 "囤": "屯" —— 屯 只读 tún，pypinyin 必对。要求替换前后字数相等。
  ② 短语读音覆盖（map 值为拼音列表）：注册进 pypinyin phrases_dict，
     原文不动、只改读音。用于找不到合适同音字的场合。
     例 "重来": ["chong2","lai2"] —— pypinyin 默认 zhong4。
用法（f5_batch.py 内自动调用）：
    from polyphone_fix import apply_poly, register_phrase_overrides
    poly_map = J.get("poly_map") or {}          # 内置表 + 项目 poly.json 合并
    register_phrase_overrides(poly_map)         # ② 先注册短语读音
    text = apply_poly(item["text"], poly_map)   # ① 再做同音字替换
项目级扩展：文案同名 <文案>.poly.json（值格式同上），由 P0 多音字校审产出；
    内置表管常见错读，项目表按当条文案语境增补（品名、谐音梗、品牌特读）。
"""
import re

# ── 内置基础表（2026-10-13 实测 pypinyin TONE3 逐一验证，只收确认错读的）──
POLYPHONE_BASE = {
    # ① 同音字替换（值=读音唯一的同音字，字数必须相等）
    "囤": "屯",                                   # 囤货 tún，默认误读 dùn
    # ② 短语读音覆盖（值=逐字拼音 TONE3，原文不动）
    "重来": ["chong2", "lai2"],                   # 默认 zhong4
    "重拾": ["chong2", "shi2"],
    "重灾区": ["chong2", "zai1", "qu1"],
    "嚼碎": ["jiao2", "sui4"],                    # 默认 jué，口播口语 jiáo
    "嚼着": ["jiao2", "zhe"],
    "薄脆": ["bao2", "cui4"],                     # 默认 bó，食品语境 báo
}

_HAN = re.compile(r"[\u4e00-\u9fff]")


def apply_poly(text, poly_map):
    """①同音字替换：长键优先；替换前后字数必须相等（保 tokens 逐字对齐）"""
    subs = {k: v for k, v in (poly_map or {}).items() if isinstance(v, str)}
    for k in sorted(subs, key=len, reverse=True):
        if k in text:
            if len(k) != len(subs[k]):
                raise ValueError(f"poly 同音字替换长度不等：{k!r} → {subs[k]!r}"
                                 f"（会破坏逐字对齐，请改用短语读音覆盖格式）")
            text = text.replace(k, subs[k])
    return text


def register_phrase_overrides(poly_map):
    """②短语读音覆盖：注册进 pypinyin（f5_tts_mlx 内部同库，即时生效）"""
    phr = {k: v for k, v in (poly_map or {}).items() if isinstance(v, list)}
    if not phr:
        return
    from pypinyin import load_phrases_dict
    # 格式：{短语: [[逐字拼音], [..], ...]}
    load_phrases_dict({k: [[p] for p in v] for k, v in phr.items()})


def merged_map(extra=None):
    """内置基础表 + 项目表（extra 优先，可覆盖内置项）"""
    m = dict(POLYPHONE_BASE)
    if extra:
        m.update(extra)
    return m
