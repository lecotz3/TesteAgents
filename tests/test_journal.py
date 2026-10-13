from desk.journal import Journal, format_entry, format_exit, format_note
from desk.models import Fill
from tests.factories import NOW, make_analysis, make_position


def test_entry_block_has_every_field_the_guide_requires():
    pos, a = make_position(), make_analysis()
    text = format_entry(
        pos=pos, analysis=a, fill=Fill("111", "BUY", 9.67, 0.62, 0.01, 0.62), edge_pp=12.0, mode="paper", now=NOW
    )
    for needle in ["ENTRADA [PAPER]", pos.question, "Yes", "74.0%", "0.620", "9.67", "12.0pp",
                   a.official_resolution_source, a.exit_condition, a.reasoning, a.evidence[0].url]:
        assert needle in text


def test_exit_block_shows_pnl():
    pos = make_position(shares=10, avg_price=0.5, fee_paid=0.0)
    text = format_exit(pos=pos, price=0.6, proceeds=6.0, reason="mispricing_closed", mode="paper", now=NOW)
    assert "SAÍDA [PAPER]" in text and "mispricing_closed" in text and "+1.00" in text


def test_journal_appends_utf8(tmp_path):
    journal = Journal(tmp_path / "sub" / "trades.txt")
    journal.append(format_note("NOTA", "ação", NOW))
    journal.append("x\n")
    content = (tmp_path / "sub" / "trades.txt").read_text(encoding="utf-8")
    assert "ação" in content and content.endswith("x\n")
