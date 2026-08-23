#!/usr/bin/env python3
"""Build the Bahana demo warehouse in BigQuery as a normalised star, not a dump.

Why normalised
--------------
A single flat table of synthetic nasabah proves nothing to a securities firm --
it is a spreadsheet with extra steps. Split into dimensions and facts, the same
data demonstrates the two things that actually matter to that audience:

1. **Suitability is a lookup, not a judgement.** `dim_offer_eligibility` is the
   risk-profile-to-SKU catalogue as an actual table you can SELECT from. When
   someone asks "how do you stop the AI recommending an equity fund to a
   conservative investor", the answer is a join, not a paragraph of prompt.

2. **Holding drift is visible.** `dim_product.risk_rank` vs the customer's own
   rank surfaces nasabah holding products riskier than their stated profile --
   a real advisory finding, and the kind of thing a warehouse earns its keep on.

The generator in `seed_demo_data/nasabah.py` is the single source of truth for
the population, so the warehouse and the Chatwoot contacts are the same people:
same names, same phones, same offers. `v_nasabah_profile` re-flattens the star
back into exactly the nine attributes the CRM carries, which is what makes
"this row IS that contact IS this conversation" literally true on stage rather
than a hand-wave.

Two vocabularies are deliberately kept apart, because they are genuinely
different things and conflating them is how you end up unable to answer a
question:

- **instruments** (`dim_instrument`) are IDX tickers -- what a nasabah holds
  directly.
- **products** (`dim_product`) are the sellable SKUs -- reksa dana, obligasi,
  and "Saham" as the umbrella for holding equities at all.

Usage
-----
    python3 bahana_bq_warehouse.py --project lv-playground-genai \\
        --dataset bahana_demo --location asia-southeast2

Idempotent: every load uses WRITE_TRUNCATE, so re-running rebuilds cleanly.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent / "seed_demo_data"))

from nasabah import (  # noqa: E402
    _OFFERS_BY_RISK,
    _PRODUCTS,
    generate_nasabah,
)

BATCH_ID = "demo1"
PINNED_PHONE = "+6281112117038"
PINNED_NAME = "Budi Santoso"

# The pinned demo contact was hand-corrected in Chatwoot for internal coherence
# (its offer rationale claims a concentrated portfolio, so it must actually hold
# something). Mirror that here or the warehouse contradicts the CRM on the one
# record that will be on screen.
PINNED_OVERRIDE = {
    "aum_band": "Rp 100-500 juta",
    "holdings": ["BBCA", "BBRI", "TLKM"],
}

RISK_RANK = {"Konservatif": 1, "Moderat": 2, "Agresif": 3}

# SKU catalogue. `risk_rank` is the risk of HOLDING the product, used for drift
# analysis. Which products may be OFFERED to whom is a separate, stricter thing
# -- see dim_offer_eligibility, built from the generator's own catalogue.
PRODUCTS = [
    ("RDPU-001", "Reksa Dana Pasar Uang", "Reksa Dana", 1, 100_000),
    ("ORI-001", "Obligasi Ritel (ORI)", "Obligasi", 1, 1_000_000),
    ("RDC-001", "Reksa Dana Campuran", "Reksa Dana", 2, 100_000),
    ("OBK-001", "Obligasi Korporasi", "Obligasi", 2, 5_000_000),
    ("RDS-001", "Reksa Dana Saham", "Reksa Dana", 3, 100_000),
    ("IPO-001", "IPO Subscription", "Saham", 3, 1_000_000),
    ("SAHAM-001", "Saham", "Saham", 2, 100_000),
]
PRODUCT_BY_NAME = {name: sku for sku, name, _, _, _ in PRODUCTS}

INSTRUMENTS = [
    ("BBCA", "Bank Central Asia", "Keuangan"),
    ("BBRI", "Bank Rakyat Indonesia", "Keuangan"),
    ("BMRI", "Bank Mandiri", "Keuangan"),
    ("TLKM", "Telkom Indonesia", "Infrastruktur"),
    ("ASII", "Astra International", "Aneka Industri"),
    ("UNVR", "Unilever Indonesia", "Barang Konsumen"),
    ("ICBP", "Indofood CBP", "Barang Konsumen"),
    ("ANTM", "Aneka Tambang", "Barang Baku"),
    ("PGAS", "Perusahaan Gas Negara", "Energi"),
    ("KLBF", "Kalbe Farma", "Kesehatan"),
]


def customer_id(index: int) -> str:
    """Stable synthetic CIF. Derived from position, so it is reproducible."""
    return f"CIF{index + 1:05d}"


def build_rows() -> dict[str, list[dict]]:
    people = generate_nasabah(
        25, batch_id=BATCH_ID, pinned_phone=PINNED_PHONE, pinned_name=PINNED_NAME
    )

    dim_customer, fact_holding, fact_ownership, fact_offer = [], [], [], []

    for i, p in enumerate(people):
        cif = customer_id(i)
        holdings = list(p.holdings)
        aum_band = p.aum_band
        if i == 0:
            holdings = list(PINNED_OVERRIDE["holdings"])
            aum_band = PINNED_OVERRIDE["aum_band"]

        dim_customer.append(
            {
                "customer_id": cif,
                "batch_id": BATCH_ID,
                "name": p.name,
                "phone": p.phone,
                "email": p.email,
                "risk_profile": p.risk_profile,
                "risk_rank": RISK_RANK[p.risk_profile],
                "aum_band": aum_band,
                "rdn_balance_idr": p.rdn_balance,
                "days_since_last_transaction": p.days_since_last_transaction,
            }
        )

        for ticker in holdings:
            fact_holding.append({"customer_id": cif, "ticker": ticker})

        # Ownership is derived as (products eligible for this risk profile)
        # MINUS (their gaps) -- NOT as (whole universe) minus gaps.
        #
        # `product_gaps` is already risk-filtered upstream, so "not in gaps"
        # conflates two very different things: products the nasabah owns, and
        # products that were never eligible for them in the first place. Taking
        # the universe made every Konservatif customer appear to own the equity
        # fund they are specifically not allowed to be sold, which is the exact
        # confusion this warehouse exists to make impossible.
        #
        # The consequence is a deliberate modelling limit worth stating: we can
        # only represent ownership of products inside a nasabah's own catalogue,
        # plus direct equities. The generator does not expose holdings outside
        # that set, so inventing them here would be fabrication.
        eligible = [offer for offer, _ in _OFFERS_BY_RISK.get(p.risk_profile, [])]
        held = [prod for prod in eligible if prod not in p.product_gaps]
        if holdings:
            held.append("Saham")
        for prod in held:
            sku = PRODUCT_BY_NAME.get(prod)
            if sku:
                fact_ownership.append({"customer_id": cif, "product_sku": sku})

        fact_offer.append(
            {
                "customer_id": cif,
                "product_sku": PRODUCT_BY_NAME[p.next_best_offer],
                "rationale": p.offer_rationale,
                "batch_id": BATCH_ID,
            }
        )

    dim_product = [
        {
            "product_sku": sku,
            "product_name": name,
            "category": cat,
            "risk_rank": rank,
            "min_investment_idr": minimum,
        }
        for sku, name, cat, rank, minimum in PRODUCTS
    ]

    dim_instrument = [
        {"ticker": t, "instrument_name": n, "sector": s} for t, n, s in INSTRUMENTS
    ]

    # The suitability rule itself, straight from the generator's catalogue --
    # exact per profile, NOT cumulative. A Moderat nasabah is not offered
    # conservative products just because they could tolerate them.
    dim_offer_eligibility = [
        {
            "risk_profile": profile,
            "product_sku": PRODUCT_BY_NAME[offer],
            "product_name": offer,
        }
        for profile, offers in _OFFERS_BY_RISK.items()
        for offer, _ in offers
    ]

    return {
        "dim_customer": dim_customer,
        "dim_product": dim_product,
        "dim_instrument": dim_instrument,
        "dim_offer_eligibility": dim_offer_eligibility,
        "fact_holding": fact_holding,
        "fact_product_ownership": fact_ownership,
        "fact_next_best_offer": fact_offer,
    }


SCHEMAS: dict[str, list[dict]] = {
    "dim_customer": [
        {"name": "customer_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "batch_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "phone", "type": "STRING", "mode": "REQUIRED"},
        {"name": "email", "type": "STRING", "mode": "NULLABLE"},
        {"name": "risk_profile", "type": "STRING", "mode": "REQUIRED"},
        {"name": "risk_rank", "type": "INT64", "mode": "REQUIRED"},
        {"name": "aum_band", "type": "STRING", "mode": "NULLABLE"},
        {"name": "rdn_balance_idr", "type": "INT64", "mode": "NULLABLE"},
        {"name": "days_since_last_transaction", "type": "INT64", "mode": "NULLABLE"},
    ],
    "dim_product": [
        {"name": "product_sku", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "category", "type": "STRING", "mode": "REQUIRED"},
        {"name": "risk_rank", "type": "INT64", "mode": "REQUIRED"},
        {"name": "min_investment_idr", "type": "INT64", "mode": "NULLABLE"},
    ],
    "dim_instrument": [
        {"name": "ticker", "type": "STRING", "mode": "REQUIRED"},
        {"name": "instrument_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "sector", "type": "STRING", "mode": "NULLABLE"},
    ],
    "dim_offer_eligibility": [
        {"name": "risk_profile", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_sku", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_name", "type": "STRING", "mode": "REQUIRED"},
    ],
    "fact_holding": [
        {"name": "customer_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "ticker", "type": "STRING", "mode": "REQUIRED"},
    ],
    "fact_product_ownership": [
        {"name": "customer_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_sku", "type": "STRING", "mode": "REQUIRED"},
    ],
    "fact_next_best_offer": [
        {"name": "customer_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_sku", "type": "STRING", "mode": "REQUIRED"},
        {"name": "rationale", "type": "STRING", "mode": "NULLABLE"},
        {"name": "batch_id", "type": "STRING", "mode": "REQUIRED"},
    ],
}


def profile_view_sql(project: str, dataset: str) -> str:
    """The star flattened back into the nine attributes the CRM carries.

    `product_gaps` is computed here as *eligible-and-not-owned*, joining
    dim_offer_eligibility rather than the whole product universe. That is the
    same rule `nasabah.py::_gaps_for` enforces in Python, expressed in SQL --
    which is the point: the constraint lives in data, so it is inspectable by
    someone who does not read Python.
    """
    fq = f"`{project}.{dataset}`"
    return f"""
CREATE OR REPLACE VIEW {fq}.v_nasabah_profile AS
WITH holdings AS (
  SELECT customer_id, STRING_AGG(ticker, ', ' ORDER BY ticker) AS holdings
  FROM {fq}.fact_holding GROUP BY customer_id
),
gaps AS (
  SELECT e.risk_profile, c.customer_id,
         STRING_AGG(e.product_name, ', ' ORDER BY e.product_name) AS product_gaps
  FROM {fq}.dim_customer c
  JOIN {fq}.dim_offer_eligibility e USING (risk_profile)
  LEFT JOIN {fq}.fact_product_ownership o
         ON o.customer_id = c.customer_id AND o.product_sku = e.product_sku
  WHERE o.customer_id IS NULL
  GROUP BY e.risk_profile, c.customer_id
)
SELECT
  c.customer_id,
  c.name,
  c.phone,
  c.risk_profile,
  c.aum_band,
  FORMAT('Rp %s', FORMAT('%\\'d', c.rdn_balance_idr))      AS rdn_balance,
  IFNULL(h.holdings, 'Tidak ada')                          AS holdings,
  CAST(c.days_since_last_transaction AS STRING)            AS days_since_last_transaction,
  IFNULL(g.product_gaps, 'Tidak ada')                      AS product_gaps,
  p.product_name                                           AS next_best_offer,
  f.rationale                                              AS offer_rationale,
  c.batch_id                                               AS demo_seed
FROM {fq}.dim_customer c
LEFT JOIN holdings h USING (customer_id)
LEFT JOIN gaps g     USING (customer_id)
LEFT JOIN {fq}.fact_next_best_offer f USING (customer_id)
LEFT JOIN {fq}.dim_product p ON p.product_sku = f.product_sku
"""


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd[:4])}...\n{r.stderr[:800]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="lv-playground-genai")
    ap.add_argument("--dataset", default="bahana_demo")
    ap.add_argument("--location", default="asia-southeast2")
    args = ap.parse_args()

    tables = build_rows()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bahana_wh_"))

    for table, rows in tables.items():
        data = tmp / f"{table}.ndjson"
        schema = tmp / f"{table}.schema.json"
        data.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )
        schema.write_text(json.dumps(SCHEMAS[table], indent=1))
        run(
            [
                "bq", f"--project_id={args.project}", f"--location={args.location}",
                "load", "--source_format=NEWLINE_DELIMITED_JSON", "--replace",
                f"{args.dataset}.{table}", str(data), str(schema),
            ]
        )
        print(f"  loaded {table:24} {len(rows):4d} rows")

    run(
        [
            "bq", f"--project_id={args.project}", f"--location={args.location}",
            "query", "--use_legacy_sql=false",
            profile_view_sql(args.project, args.dataset),
        ]
    )
    print("  created view v_nasabah_profile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
