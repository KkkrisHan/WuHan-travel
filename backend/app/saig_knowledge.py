"""Small, source-linked knowledge set shared with the landing page."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "saigKnowledge.json"


@lru_cache(maxsize=1)
def load_knowledge() -> dict:
    return json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))


def relevant_knowledge(question: str) -> dict | None:
    spu_match = re.search(r"\bSPU\b|数命科技|数命.{0,4}社区|超级人类单元", question, re.I)
    saig_match = re.search(r"\bSAIG\b|线上赛|现场赛|现场比赛|现场竞赛|线下赛|赛制|奖池|赛事|竞赛|大赛|参赛|选手", question, re.I)
    if not spu_match and not saig_match:
        return None

    data = load_knowledge()
    selected = {"updatedAt": data["updatedAt"], "sources": data["sources"]}
    if spu_match:
        selected["spu"] = data["spu"]
    if saig_match:
        selected["saig"] = data["saig"]
    return selected


def fallback_answer(knowledge: dict) -> str:
    """Answer core questions from the verified facts when no model key is set."""
    lines: list[str] = []
    spu = knowledge.get("spu")
    saig = knowledge.get("saig")
    if spu:
        lines.append(
            f"SPU 是 {spu['fullName']}（{spu['translation']}）：{spu['definition']} "
            f"{spu['company']} 数命 SPU 社区围绕真实问题学习和使用 AI，承接任务、完成交付并展示成果。"
        )
    if saig:
        lines.append(
            f"{saig['name']} 主题为 {saig['theme']}。三日活动在 {saig['eventDates']} 于{saig['location']}举行；"
            f"现场比赛为 {saig['competitionDates']}，{saig['competitionHours']}。"
            f"{saig['challenge']} 奖池为{saig['prizePool']}"
        )
        lines.append(
            f"线上题目：{saig['onlineChallenge']} {saig['submission']} {saig['selection']}"
        )
        lines.append(
            f"比赛结束后进行市场测试，成绩为现场表现 40%、市场测试 60%。{saig['participation']} "
            f"{saig['ticketStatus']}"
        )
    sources = knowledge["sources"]
    citations = [f"[SAIG 官方网站]({sources['saig']})"]
    if spu:
        citations.append(f"[数命科技 / SAIG 官方首页]({sources['home']})")
    return "\n\n".join(lines) + "\n\n来源：" + " · ".join(citations)
