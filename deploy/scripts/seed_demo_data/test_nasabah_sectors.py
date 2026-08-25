"""`sectors_for` -- equity holdings grouped by IDX sector.

The string this builds is written onto the Chatwoot contact as
`holdings_sectors` and read back into the AI prompt by
`agent/app/services/customer_context.py`. It has a SQL twin in
`bahana_bq_warehouse.profile_view_sql`, so the ordering rules below are a
contract, not a formatting preference: a contact written by the seeder and the
same contact refreshed by the BigQuery sync must carry a byte-identical value,
or the nightly sync silently rewrites every contact it touches.
"""

from __future__ import annotations

from nasabah import TICKER_SECTORS, sectors_for


def test_no_holdings_yields_the_shared_none_sentinel():
    # Both writers spell "none" this way, and `customer_context` recognises
    # exactly this string when deciding there are no alternatives to list.
    assert sectors_for([]) == "Tidak ada"


def test_single_holding_renders_its_sector():
    assert sectors_for(["TLKM"]) == "Infrastruktur (TLKM)"


def test_groups_tickers_sharing_a_sector():
    assert sectors_for(["BBCA", "BBRI"]) == "Keuangan (BBCA, BBRI)"


def test_biggest_sector_leads():
    # Concentration is the point of the field: the sector the nasabah is most
    # exposed to has to be the first thing the model reads.
    out = sectors_for(["BBCA", "BBRI", "TLKM"])
    assert out == "Keuangan (BBCA, BBRI), Infrastruktur (TLKM)"


def test_equal_sized_sectors_are_ordered_by_name():
    out = sectors_for(["ANTM", "BBRI", "ICBP", "PGAS"])
    assert out == (
        "Barang Baku (ANTM), Barang Konsumen (ICBP), "
        "Energi (PGAS), Keuangan (BBRI)"
    )


def test_tickers_are_sorted_within_a_group():
    assert sectors_for(["BBRI", "BBCA"]) == "Keuangan (BBCA, BBRI)"


def test_input_order_does_not_change_the_output():
    # The generator draws holdings with `rnd.sample`, so input order is
    # arbitrary; the sync job reads them back sorted. Both must agree.
    assert sectors_for(["TLKM", "BBRI", "BBCA"]) == sectors_for(["BBCA", "BBRI", "TLKM"])


def test_unknown_ticker_is_grouped_rather_than_dropped():
    # `holdings` renders directly above this field in the prompt. Dropping a
    # ticker here would make the two disagree in front of the model.
    out = sectors_for(["XXXX"])
    assert out == "Lainnya (XXXX)"


def test_every_generated_ticker_has_a_sector():
    # The generator draws from `_TICKERS`; anything it can draw must map, or
    # real nasabah start showing up under "Lainnya".
    from nasabah import _TICKERS

    assert set(_TICKERS) <= set(TICKER_SECTORS)
