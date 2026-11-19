"""Human-reviewed public notices for the first batch, with exact source URLs.

The source references are intentionally explicit so a future maintainer can
replace or expire them when an operator publishes a new rule.
"""
from __future__ import annotations

from .store import add_session, connection

REVIEWED_AT = "2026-11-19T00:00:00+00:00"


def _official_fact(poi_id: str, field: str, value: str, evidence: str, url: str):
    with connection() as db:
        db.execute("""INSERT INTO poi_sources(poi_id,url,kind,checked_at) VALUES(?,?,?,?)
            ON CONFLICT(poi_id,url) DO NOTHING""", (poi_id, url, "official_venue", REVIEWED_AT))
        source_id = db.execute("SELECT id FROM poi_sources WHERE poi_id=? AND url=?",
                               (poi_id, url)).fetchone()[0]
        db.execute("""INSERT INTO poi_facts(poi_id,field,value,source_id,evidence,checked_at,status)
            VALUES(?,?,?,?,?,?,'verified') ON CONFLICT(poi_id,field,source_id) DO UPDATE SET
            value=excluded.value,evidence=excluded.evidence,checked_at=excluded.checked_at,status='verified'""",
            (poi_id, field, value, source_id, evidence, REVIEWED_AT))


def apply_official_seed():
    with connection() as db:
        names = {r["name"]: r["poi_id"] for r in db.execute("SELECT name,poi_id FROM poi_places")}
    museum = names.get("湖北省博物馆")
    if museum:
        url = "https://www.hbww.org.cn/xwdt/p/13081.html"
        service = "https://www.hbww.org.cn/fuwu/index.html"
        _official_fact(museum, "reservation", "普通参观需实名预约；部分优待人群免预约",
                       "本馆实行实名预约参观。", service)
        _official_fact(museum, "adultPrice", "普通参观门票免费",
                       "门票免费", service)
        add_session(museum, kind="day", start_minute=540, end_minute=1050,
                    last_entry_minute=990, weekdays=[1, 2, 3, 4], start_date="2026-10-01",
                    source_url=url, checked_at=REVIEWED_AT, note="2026-09-29官方公告：周二至周五9:00-17:30，16:30停止检票")
        add_session(museum, kind="day", start_minute=540, end_minute=1230,
                    last_entry_minute=1170, weekdays=[5, 6], start_date="2026-10-01",
                    source_url=url, checked_at=REVIEWED_AT, note="2026-09-29官方公告：周六、周日9:00-20:30，19:30停止检票；预约夜场票额")
        add_session(museum, kind="closure", start_minute=None, end_minute=None,
                    weekdays=[0], is_closed=True, start_date="2026-10-01",
                    source_url=service,
                    checked_at=REVIEWED_AT,
                    note="官方服务页：周一闭馆，法定节假日除外；节假日须单独核实")
    tower = names.get("黄鹤楼")
    if tower:
        # Conservative overlap: district tourism bureau lists Nov-Mar 08:00-17:00;
        # Aug 2026 practical ticket guide lists day admission 08:30-17:20 and
        # park clearance 18:00. Only their shared 08:30-17:00 is asserted.
        for url, source_kind in (
            ("https://www.wuchang.gov.cn/zwgk_37/fdzdgknr/shgysy/ly/202209/t20220930_2050896.html", "official_government"),
            ("https://m.wh.bendibao.com/tour/199727.shtm", "reputable_reference"),
        ):
            add_session(tower, kind="day", start_minute=510, end_minute=1020,
                        month_start="11-01", month_end="03-31", source_url=url,
                        source_kind=source_kind,
                        checked_at=REVIEWED_AT,
                        note="仅采用两来源共同支持的日间时段；夜上黄鹤楼另售票且具体日期场次未核实")
    return {"museum": bool(museum), "tower": bool(tower)}


if __name__ == "__main__":
    print(apply_official_seed())
