"""Retrieval recall on realistic questions, searched with the raw question text (no query rewriting,
i.e. the worst case). Each case names a fact that must appear in the top-8 passages."""

import pytest

CASES = [
    ("How much is the in-patient adrenal cancer program?", "adrenal-package", "$30,000"),
    ("What does the out-patient adenocarcinoma program include?", "adenocarcinoma-package", "Two health-focused meals"),
    ("What is not included in the anal cancer package?", "anal-package", "Specialist Consultations"),
    ("How much does LAK cell therapy cost?", None, "$2,600"),
    ("What does a blood transfusion cost at ITC?", None, "$462 per pint"),
    ("Who is the medical director of ITC?", None, "Carlos Bautista"),
    ("What is the clinic's address?", None, "Padre Kino 10133"),
    ("How far is the clinic from San Diego airport?", "itc-clinic-profile", "25 minutes"),
    ("Is the staff at the clinic bilingual?", None, "bilingual"),
    ("What happens in week 2 of the adrenal program?", "adrenal-package", "Week 2"),
    ("How much is breast cancer treatment at ITC?", "itc-price-list", "Procedure: Breast Cancer | Price in USD: $18995"),
    ("Are there patient videos about lung cancer?", "itc-videos", "Lung"),
    ("What is the average cost of alternative medicine abroad?", "alternative-medicine-prices", "$15032"),
    ("How much does acupuncture cost abroad?", "alternative-medicine-prices", "Procedure: Acupuncture Therapy Package | Low: $200 | High: $800"),
    ("Does health insurance cover alternative medicine abroad?", "alternative-medicine-prices", "insurance"),
    ("Which clinic in Turkey offers ozone therapy?", "alternative-medicine-prices", "Fizikon"),
    ("What is often not included in alternative medicine packages?", "alternative-medicine-prices", "International flights"),
    ("What awards has ITC won?", "itc-clinic-profile", "Top Cancer Center in Latin America"),
    ("What risks should patients consider?", "itc-clinic-profile", "second opinion"),
    ("Can ITC treat adenocarcinoma that has spread?", "adenocarcinoma-package", "metastasized"),
    ("How much does ethanol tumor ablation cost?", None, "Ethanol Tumor Ablation"),
    ("Is a follow-up consultation included after treatment?", None, "after 3 months"),
    ("Which cancer types does ITC treat?", "itc-clinic-profile", "Pancreatic Cancer"),
    ("How much is the EBOO therapy package in India?", "alternative-medicine-prices", "$10,000"),
    ("How much does an ambulance transfer cost?", None, "$950"),
]


@pytest.mark.parametrize("question,page,fact", CASES, ids=[c[0][:40] for c in CASES])
def test_fact_is_retrieved(kb, question, page, fact):
    hits = kb.search(question, 8)
    found = [h for h in hits if fact in h.chunk.text and (page is None or h.chunk.page_id == page)]
    assert found, f"{fact!r} not in top 8: {[h.chunk.id for h in hits]}"
