"""Deterministic HTML fixture generator.

Reads `golden_dataset.json` and produces every version of the demo storefront
we use in tests, benchmarks and demo. Every fixture ships in the repo so
the demo does not depend on a live website (per guide §10).

Fixture versions (11 total):

    v1_healthy.html         Baseline that the initial extraction strategy targets.
    v2_rename_class.html    `.price` renamed to `.cost` — the classic breakage.
    v3_dataattr.html        Class identifiers replaced with `data-testid` attributes.
    v4_change_nesting.html  Extra wrapper divs around every field.
    v5_move_price.html      Price moved into a sibling summary block.
    v6_label_change.html    Visible labels change (e.g. "Reviews:" -> "Ratings:").
    v7_decoy.html           Crossed-out "was" price introduces a decoy element.
    v8_pagination.html      Pagination markup changes (next-link rewritten).
    v9_combined.html        Multiple changes at once (v2 + v4 + v7).
    v10_partial.html        Only availability field breaks; the rest stays stable.
    v11_semantic.html       Same DOM, but availability values change semantics.

Run:
    python tests/fixtures/generate_fixtures.py
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN_PATH = HERE / "golden_dataset.json"

# We pick a page size so pagination can be exercised.
PAGE_SIZE = 10


def load_records() -> list[dict]:
    with GOLDEN_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["records"]


def page_slice(records: list[dict], page: int) -> list[dict]:
    start = (page - 1) * PAGE_SIZE
    return records[start : start + PAGE_SIZE]


# --------------------------------------------------------------------------- #
# HTML skeleton
# --------------------------------------------------------------------------- #

HEAD = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="fixture-version" content="{version}">
</head>
<body>
  <header><h1>MetroKart</h1><nav><a href="/">Home</a> · <a href="/deals">Deals</a></nav></header>
  <main>
    <h2>Electronics catalog — page {page}</h2>
    <section class="product-grid">
"""

FOOT = """    </section>
    {pagination}
  </main>
  <footer><p>© MetroKart demo (synthetic data).</p></footer>
</body>
</html>
"""


def render_pagination_default(page: int, total_pages: int) -> str:
    parts: list[str] = ['<nav class="pagination">']
    if page > 1:
        parts.append(f'<a class="prev" href="?page={page-1}">Prev</a>')
    for i in range(1, total_pages + 1):
        cls = "current" if i == page else "page"
        parts.append(f'<a class="{cls}" href="?page={i}">{i}</a>')
    if page < total_pages:
        parts.append(f'<a class="next" href="?page={page+1}">Next</a>')
    parts.append("</nav>")
    return "\n    ".join(parts)


def render_pagination_altered(page: int, total_pages: int) -> str:
    # `.next` link becomes `.load-more` inside a wrapper.
    parts: list[str] = ['<div class="pager">']
    for i in range(1, total_pages + 1):
        cls = "on" if i == page else "off"
        parts.append(f'<a class="{cls}" data-p="{i}" href="?p={i}">{i}</a>')
    if page < total_pages:
        parts.append(f'<button class="load-more" data-next="{page+1}">Show more</button>')
    parts.append("</div>")
    return "\n    ".join(parts)


# --------------------------------------------------------------------------- #
# Card renderers, one per fixture version
# --------------------------------------------------------------------------- #


def _price_str(p: dict) -> str:
    return f"₹{p['price']:,.2f}"


def card_v1(p: dict) -> str:
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <div class="price-block">
          <span class="price">{escape(_price_str(p))}</span>
          <span class="currency">{escape(p['currency'])}</span>
        </div>
        <div class="rating-block">
          <span class="rating">{p['rating']:.1f}</span> stars ·
          <span class="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div class="availability">{escape(p['availability'])}</div>
      </article>
"""


def card_v2_rename(p: dict) -> str:
    # `.price` → `.cost` (baseline strategy will miss price entirely)
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <div class="price-block">
          <span class="cost">{escape(_price_str(p))}</span>
          <span class="currency">{escape(p['currency'])}</span>
        </div>
        <div class="rating-block">
          <span class="rating">{p['rating']:.1f}</span> stars ·
          <span class="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div class="availability">{escape(p['availability'])}</div>
      </article>
"""


def card_v3_dataattr(p: dict) -> str:
    # data-testid attributes replace class-based identifiers on price/rating.
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 data-testid="title">{escape(p['product_name'])}</h3>
        </a>
        <div>
          <span data-testid="price">{escape(_price_str(p))}</span>
          <span data-testid="currency">{escape(p['currency'])}</span>
        </div>
        <div>
          <span data-testid="rating">{p['rating']:.1f}</span> stars ·
          <span data-testid="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div data-testid="availability">{escape(p['availability'])}</div>
      </article>
"""


def card_v4_nesting(p: dict) -> str:
    # Every field wrapped in an extra div — descendant selectors still work,
    # but strict child selectors (`>`) will not.
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <div class="card-inner">
          <a class="product-link" href="{escape(p['product_url'])}">
            <div class="media"><img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}"></div>
            <div class="title-wrap"><h3 class="product-name">{escape(p['product_name'])}</h3></div>
          </a>
          <div class="body">
            <div class="price-block"><div class="inner"><span class="price">{escape(_price_str(p))}</span><span class="currency">{escape(p['currency'])}</span></div></div>
            <div class="rating-block"><div class="inner"><span class="rating">{p['rating']:.1f}</span> stars · <span class="review-count">{p['review_count']:,}</span> reviews</div></div>
            <div class="availability"><span class="inner">{escape(p['availability'])}</span></div>
          </div>
        </div>
      </article>
"""


def card_v5_move_price(p: dict) -> str:
    # Price moved into a summary aside outside the previous parent.
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <aside class="summary">
          <span class="price">{escape(_price_str(p))}</span>
          <span class="currency">{escape(p['currency'])}</span>
        </aside>
        <div class="rating-block">
          <span class="rating">{p['rating']:.1f}</span> stars ·
          <span class="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div class="availability">{escape(p['availability'])}</div>
      </article>
"""


def card_v6_label(p: dict) -> str:
    # Visible labels change — the values are unchanged, but text anchors move.
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <div class="price-block">
          Buy for <span class="price">{escape(_price_str(p))}</span> <span class="currency">{escape(p['currency'])}</span>
        </div>
        <div class="rating-block">
          Rated <span class="rating">{p['rating']:.1f}</span>/5 ·
          <span class="review-count">{p['review_count']:,}</span> ratings
        </div>
        <div class="availability">Availability: {escape(p['availability'])}</div>
      </article>
"""


def card_v7_decoy(p: dict) -> str:
    # A struck-through "was" price sits above the real price — this is the
    # canonical trap for naive extractors that pick the first `.price`.
    was_price = f"₹{p['price']*1.25:,.2f}"
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <div class="price-block">
          <s class="price original">{escape(was_price)}</s>
          <span class="price current">{escape(_price_str(p))}</span>
          <span class="currency">{escape(p['currency'])}</span>
        </div>
        <div class="rating-block">
          <span class="rating">{p['rating']:.1f}</span> stars ·
          <span class="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div class="availability">{escape(p['availability'])}</div>
      </article>
"""


def card_v10_partial(p: dict) -> str:
    # Only the availability field breaks — the rest is v1.
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <a class="product-link" href="{escape(p['product_url'])}">
          <img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}">
          <h3 class="product-name">{escape(p['product_name'])}</h3>
        </a>
        <div class="price-block">
          <span class="price">{escape(_price_str(p))}</span>
          <span class="currency">{escape(p['currency'])}</span>
        </div>
        <div class="rating-block">
          <span class="rating">{p['rating']:.1f}</span> stars ·
          <span class="review-count">{p['review_count']:,}</span> reviews
        </div>
        <div class="stock-status">{escape(p['availability'])}</div>
      </article>
"""


def card_v11_semantic(p: dict) -> str:
    # Same DOM as v1, but availability wording changes.
    availability = "Available" if p["availability"] == "In Stock" else "Unavailable"
    return card_v1({**p, "availability": availability})


def _combined_card(p: dict) -> str:
    # v9 combines rename + nesting + decoy.
    was_price = f"₹{p['price']*1.25:,.2f}"
    return f"""      <article class="product-card" data-product-id="{escape(p['product_id'])}">
        <div class="card-inner">
          <a class="product-link" href="{escape(p['product_url'])}">
            <div class="media"><img class="product-image" src="{escape(p['image_url'])}" alt="{escape(p['product_name'])}"></div>
            <div class="title-wrap"><h3 class="product-name">{escape(p['product_name'])}</h3></div>
          </a>
          <div class="body">
            <div class="price-block"><div class="inner">
              <s class="cost original">{escape(was_price)}</s>
              <span class="cost current">{escape(_price_str(p))}</span>
              <span class="currency">{escape(p['currency'])}</span>
            </div></div>
            <div class="rating-block"><div class="inner">
              <span class="rating">{p['rating']:.1f}</span> stars ·
              <span class="review-count">{p['review_count']:,}</span> reviews
            </div></div>
            <div class="availability"><span class="inner">{escape(p['availability'])}</span></div>
          </div>
        </div>
      </article>
"""


CARDS = {
    "v1_healthy": card_v1,
    "v2_rename_class": card_v2_rename,
    "v3_dataattr": card_v3_dataattr,
    "v4_change_nesting": card_v4_nesting,
    "v5_move_price": card_v5_move_price,
    "v6_label_change": card_v6_label,
    "v7_decoy": card_v7_decoy,
    "v8_pagination": card_v1,  # DOM unchanged; only pagination differs
    "v9_combined": _combined_card,
    "v10_partial": card_v10_partial,
    "v11_semantic": card_v11_semantic,
}


PAGINATION_ALT = {"v8_pagination"}


def render_page(version: str, page: int, records: list[dict]) -> str:
    total_pages = max(1, -(-len(records) // PAGE_SIZE))
    card_fn = CARDS[version]
    body = "".join(card_fn(p) for p in page_slice(records, page))
    pagination = (
        render_pagination_altered(page, total_pages)
        if version in PAGINATION_ALT
        else render_pagination_default(page, total_pages)
    )
    return (
        HEAD.format(title=f"MetroKart — {version}", version=version, page=page)
        + body
        + FOOT.format(pagination=pagination)
    )


def main() -> None:
    records = load_records()
    total_pages = max(1, -(-len(records) // PAGE_SIZE))
    for version in CARDS:
        out_dir = HERE / "pages" / version
        out_dir.mkdir(parents=True, exist_ok=True)
        for page in range(1, total_pages + 1):
            (out_dir / f"page-{page}.html").write_text(
                render_page(version, page, records), encoding="utf-8"
            )
        print(f"[fixtures] wrote {version} ({total_pages} page(s))")


if __name__ == "__main__":
    main()
