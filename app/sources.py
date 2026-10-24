"""The only pages the bot may learn from (from the assessment brief). Nothing else is crawled."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    page_type: str
    url: str


SOURCES: tuple[Source, ...] = (
    Source(
        "itc-clinic-profile",
        "Clinic profile",
        "https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    ),
    Source(
        "adenocarcinoma-package",
        "Treatment package",
        "https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    ),
    Source(
        "adrenal-package",
        "Treatment package",
        "https://www.placidway.com/package/7902/Alternative-Adrenal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    ),
    Source(
        "anal-package",
        "Treatment package",
        "https://www.placidway.com/package/7901/Alternative-Anal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    ),
    Source(
        "itc-videos",
        "Clinic videos",
        "https://www.placidway.com/profile-videos/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    ),
    Source(
        "itc-price-list",
        "Price list",
        "https://www.placidway.com/price-list/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    ),
    Source(
        "alternative-medicine-prices",
        "Price comparison",
        "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1",
    ),
)
