import unittest

from app.saig_knowledge import fallback_answer, relevant_knowledge


class SaigKnowledgeTests(unittest.TestCase):
  def test_saig_and_spu_acceptance_questions_are_covered(self):
    questions = [
        ("SPU 的英文全称是什么？", "spu"),
        ("数命 SPU 社区主要做什么？", "spu"),
        ("数命科技、SPU 社区和 SAIG 有什么关系？", "spu"),
        ("SAIG 在哪里举办，日期是什么？", "saig"),
        ("SAIG 线上赛要求制作什么？", "saig"),
        ("线上赛需要提交哪些材料？", "saig"),
        ("现场比赛的核心形式是什么？", "saig"),
        ("普通非 AI 从业者能否参赛？", "saig"),
        ("SAIG 的奖池是多少，包含哪些组成？", "saig"),
        ("现场竞赛和三日展览是否是同一回事？", "saig"),
    ]

    for question, topic in questions:
        knowledge = relevant_knowledge(question)
        self.assertIsNotNone(knowledge, question)
        self.assertIn(topic, knowledge, question)


  def test_official_facts_and_sources_are_present(self):
    spu = relevant_knowledge("SPU 社区和数命科技是什么关系？")
    saig = relevant_knowledge("SAIG 现场赛日期、奖池和选拔赛制是什么？")

    self.assertEqual(spu["spu"]["fullName"], "Super Person Unit")
    self.assertIn("真实问题", spu["spu"]["community"])
    self.assertEqual(saig["saig"]["competitionDates"], "2026 年 12 月 18–19 日")
    self.assertEqual(saig["saig"]["eventDates"], "2026 年 12 月 18–20 日")
    self.assertIn("100 万美元", saig["saig"]["prizePool"])
    self.assertIn("演示视频链接", saig["saig"]["submission"])
    self.assertEqual(saig["sources"]["saig"], "https://www.dlife.love/saig/")
    answer = fallback_answer(saig)
    self.assertIn("50 万美元现金 + 50 万美元 AI 模型调用额度", answer)
    self.assertIn("12 月 18–20 日", answer)
    self.assertIn("12 月 18–19 日", answer)


  def test_unrelated_travel_question_does_not_receive_event_knowledge(self):
    self.assertIsNone(relevant_knowledge("黄鹤楼怎么去？"))
