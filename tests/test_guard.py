from decimal import Decimal

from app.guard import money_amounts, unsupported_amounts, unsupported_user_claims

SOURCE = """Program Type | Duration | Total Cost (USD)
Out-Patient Program | 3 Weeks | $18,995
In-Patient Program | 3 Weeks | $30,000
Specific Killer Cell Antibody: $3,950 - $4,250 USD
Colorectal Cancer | 18995 USD"""


def test_reads_common_money_formats():
    values = [v for _, v in money_amounts("$18,995, USD 30,000, 4,250 USD, €1.500, 18 995 dólares")]
    assert values == [Decimal(18995), Decimal(30000), Decimal(4250), Decimal(1500), Decimal(18995)]


def test_reads_arabic_indic_digits():
    assert [v for _, v in money_amounts("التكلفة ١٨٬٩٩٥ دولار")] == [Decimal(18995)]


def test_amounts_from_the_sources_pass_in_any_format():
    answer = "The out-patient program costs $18,995 (18.995 USD in some formats) and in-patient is US$30,000.00."
    assert unsupported_amounts(answer, SOURCE) == []


def test_invented_or_rounded_amount_is_caught():
    assert unsupported_amounts("It costs about $19,000.", SOURCE) == ["$19,000"]
    assert unsupported_amounts("Ozone therapy is $450.", SOURCE) == ["$450"]


def test_user_figure_may_be_quoted_back_when_correcting():
    answer = "I can't confirm $50; the page lists $18,995."
    assert unsupported_amounts(answer, SOURCE, user_text="I heard it costs $50") == []


def test_user_claims_not_in_sources_are_reported():
    assert unsupported_user_claims("I heard it costs $50. Confirm that.", SOURCE) == ["$50"]
    assert unsupported_user_claims("Is it really $18,995?", SOURCE) == []


def test_non_money_numbers_are_ignored():
    assert unsupported_amounts("The program lasts 3 weeks with a follow-up after 3 months.", SOURCE) == []
