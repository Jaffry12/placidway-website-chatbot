from app.ingest.chunk import chunk_page
from app.ingest.extract import extract

PAGE = """
<html><head><title>Tab title</title><script>var x = 1;</script></head><body>
<header>Site menu</header>
<div class="package-dt-page-main">
  <ol class="breadcrumb"><li>Home</li><li>Packages</li></ol>
  <h1>Alternative Adrenal Cancer Treatment Package</h1>
  <div class="price-social-block-ctm"><div class="actual-price"><span> $18,995 </span><span>Price starting from</span></div></div>
  <button class="social-btn">Share</button>
  <h2>Cost of Adrenal Cancer Treatment</h2>
  <p>Prices for the <strong>three-week</strong> programs:</p>
  <table>
    <tr><th>Program Type</th><th>Total Cost (USD)</th><th>Actions</th></tr>
    <tr><td>Out-Patient Program</td><td>$18,995</td><td>Chat with Center</td></tr>
    <tr><td>In-Patient Program</td><td>$30,000</td><td>Chat with Center</td></tr>
  </table>
  <p>This range shows prices between =$priceRange?> for patients.</p>
  <h2>FAQs</h2>
  <h3>Is the staff bilingual?</h3><p>Yes, the staff speaks English and Spanish.</p>
  <h2>Unlock the Best Alternative Medicine Packages Just for You</h2>
  <div class="exp-card"><div><span>Price starting from</span><span>$24,000</span></div>
    <h3>Integrative Cancer Treatment</h3><p>Bangkok, Thailand</p></div>
  <div class="exp-card"><h3>10-Days Program in Murter, Croatia</h3><p>Murter, Croatia</p></div>
  <h2>Request your Free Quote</h2><p>Name, email, phone</p>
  <h2>Explore Cancer Treatment: Stories, Testimonials, and Procedures</h2>
  <p>Spinal Fusion Surgery in Saudi Arabia: $50,000</p>
</div>
<footer>Copyright</footer>
</body></html>
"""


def lines_of(page):
    return {section.heading: section.lines for section in page.sections}


def test_title_noise_and_template_artifacts():
    page = extract(PAGE)
    text = page.markdown()
    assert page.title == "Alternative Adrenal Cancer Treatment Package"
    for noise in ("Site menu", "Copyright", "Home", "var x", "Chat with Center", "Share", "priceRange"):
        assert noise not in text
    assert page.skipped_artifacts == 1


def test_price_box_survives_and_is_labelled():
    # The price box's class contains "social"; it must not be dropped as a share widget.
    assert "Price starting from: $18,995" in extract(PAGE).markdown()


def test_tables_become_rows_without_ui_columns():
    sections = lines_of(extract(PAGE))
    assert sections["Cost of Adrenal Cancer Treatment"] == [
        "Prices for the three-week programs:",
        "Program Type | Total Cost (USD)",
        "Program Type: Out-Patient Program | Total Cost (USD): $18,995",
        "Program Type: In-Patient Program | Total Cost (USD): $30,000",
    ]


def test_forms_and_cross_promotion_widgets_are_dropped():
    page = extract(PAGE)
    text = page.markdown()
    assert "$50,000" not in text and "Name, email, phone" not in text
    assert "Integrative Cancer Treatment" not in text  # another clinic's ad on an ITC page
    assert "Request your Free Quote" in page.dropped_headings


def test_cards_keep_their_own_price_on_the_comparison_page():
    # Cards print the price above the title. Read naively, Croatia would inherit Bangkok's
    # $24,000 or Bangkok would lose it. Each card must keep its own price.
    sections = lines_of(extract(PAGE, keep_promotions=True))
    bangkok = sections["Unlock the Best Alternative Medicine Packages Just for You > Integrative Cancer Treatment"]
    croatia = sections["Unlock the Best Alternative Medicine Packages Just for You > 10-Days Program in Murter, Croatia"]
    assert "Price starting from: $24,000" in bangkok
    assert not any("$" in line for line in croatia)


def test_repeated_card_labels_are_not_sections():
    cards = "".join(
        f"<div><h3>Video {i}</h3><p>Cancer Treatment</p><h4>ITC Clinic</h4><p>Tijuana</p></div>" for i in range(4)
    )
    page = extract(f"<div class='package-dt-page-main'><h1>Videos</h1><h2>All videos</h2>{cards}</div>")
    headings = [s.path[-1] for s in page.sections]
    assert "ITC Clinic" not in headings
    assert lines_of(page)["All videos > Video 0"] == ["Cancer Treatment", "ITC Clinic", "Tijuana"]


def test_chunks_follow_sections_and_repeat_table_headers():
    rows = "".join(f"<tr><td>Therapy number {i} with a longer name</td><td>${1000 + i}</td></tr>" for i in range(60))
    html = (
        "<div class='package-dt-page-main'><h1>Prices</h1><h2>Price list</h2>"
        f"<table><tr><th>Therapy</th><th>Price</th></tr>{rows}</table><h2>Other</h2><p>Short.</p></div>"
    )
    chunks = chunk_page("p", "https://example.test/p", "Price list", extract(html), max_words=120)
    price_chunks = [c for c in chunks if c.section == "Price list"]
    assert len(price_chunks) > 1
    assert all(c.text.startswith("Therapy | Price") for c in price_chunks)
    assert all(len(c.text.split()) <= 130 for c in price_chunks)
    assert chunks[-1].section == "Other" and chunks[-1].text == "Short."


def test_text_before_a_widget_heading_stays_in_its_own_section():
    # The price list puts the clinic's address box and the quote form in one container.
    html = (
        "<div class='package-dt-page-main'><h1>Prices</h1><h2>Location</h2>"
        "<div><ul><li>Location: Blvd. Padre Kino 10133, Tijuana</li><li>Price Range: $18995 - $18995</li></ul>"
        "<h2>Request your Free Quote</h2><p>Select a treatment</p></div></div>"
    )
    sections = lines_of(extract(html))
    assert sections["Location"] == ["- Location: Blvd. Padre Kino 10133, Tijuana", "- Price Range: $18995 - $18995"]
    assert "Select a treatment" not in extract(html).markdown()


def test_table_cells_carry_their_column_names():
    # Read alone, "Stem Health ... | $1000" looks like a stem cell price; it is an ozone therapy price.
    html = (
        "<div class='package-dt-page-main'><h1>Prices</h1><h2>Clinics</h2><table>"
        "<tr><th>Clinic / Surgeon</th><th>Standard</th><th>Procedure</th><th>Action</th></tr>"
        "<tr><td>Stem Health Guadalajara</td><td>$1000</td><td>Ozone Therapy</td><td>View Profile</td></tr>"
        "</table></div>"
    )
    rows = lines_of(extract(html))["Clinics"]
    assert rows[1] == "Clinic / Surgeon: Stem Health Guadalajara | Standard: $1000 | Procedure: Ozone Therapy"
