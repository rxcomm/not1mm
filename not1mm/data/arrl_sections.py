"""
The 85 W/VE sections used in ARRL contests (e.g. ARRL Sweepstakes),
grouped by US call area (0-9) and VE/VO (Canada), as published by the
ARRL: https://contests.arrl.org/contestmultipliers.php?a=wve

Used by the statistics window to show a clean-sweep tracker: which
sections have been worked, and which remain, grouped the way an
operator naturally thinks about them.
"""

# Order within each call area matches the ARRL's own listing.
ARRL_SECTIONS_BY_CALL_AREA: dict[str, list[str]] = {
    "0": ["CO", "IA", "KS", "MN", "MO", "ND", "NE", "SD"],
    "1": ["CT", "EMA", "ME", "NH", "RI", "VT", "WMA"],
    "2": ["ENY", "NLI", "NNJ", "NNY", "SNJ", "WNY"],
    "3": ["DE", "EPA", "MDC", "WPA"],
    "4": ["AL", "GA", "KY", "NC", "NFL", "PR", "SC", "SFL", "TN", "VA", "VI", "WCF"],
    "5": ["AR", "LA", "MS", "NM", "NTX", "OK", "STX", "WTX"],
    "6": ["EB", "LAX", "ORG", "PAC", "SB", "SCV", "SDG", "SF", "SJV", "SV"],
    "7": ["AK", "AZ", "EWA", "ID", "MT", "NV", "OR", "UT", "WWA", "WY"],
    "8": ["MI", "OH", "WV"],
    "9": ["IL", "IN", "WI"],
    "VE/VO": [
        "AB",
        "BC",
        "GH",
        "MB",
        "NB",
        "NL",
        "NS",
        "ONE",
        "ONN",
        "ONS",
        "PE",
        "QC",
        "SK",
        "TER",
    ],
}

# Flat set of all 85 valid section abbreviations, for membership checks
# and total counts.
ALL_ARRL_SECTIONS: frozenset[str] = frozenset(
    section
    for sections in ARRL_SECTIONS_BY_CALL_AREA.values()
    for section in sections
)

TOTAL_ARRL_SECTIONS: int = len(ALL_ARRL_SECTIONS)
