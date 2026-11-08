"""Mock extraction provider for local development and tests.

This is NOT an LLM. It is a deterministic keyword/regex parser (English + Indonesian) labelled
`provider="mock"` everywhere it is shown, so no result is ever passed off as AI output.
Every value it returns carries the exact source substring it came from; anything it cannot
find is returned as `missing` rather than guessed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.domain.requirement import REQUIRED_FIELDS, FieldValue
from app.llm.base import ExtractionOutput

I = re.IGNORECASE

# ------------------------------------------------------------------ helpers


def _sentence(text: str, start: int, end: int) -> str:
    """Exact substring of the sentence/line containing [start, end)."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n")
    right_candidates = [i for i in (text.find(c, end) for c in "!?\n") if i != -1]
    # a '.' only ends the sentence when followed by whitespace/end (keeps "1.500" and "2.5k" intact)
    dot = re.compile(r"\.(?=\s|$)").search(text, end)
    if dot:
        right_candidates.append(dot.start())
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].strip()


def _parse_number(raw: str, suffix: str | None = None) -> Decimal | None:
    s = raw.strip().rstrip(".,")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # Indonesian thousands: 1.500.000
        s = s.replace(".", "")
    else:
        s = s.replace(",", "")
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None
    mult = {
        "k": 1_000, "rb": 1_000, "ribu": 1_000,
        "m": 1_000_000, "million": 1_000_000, "jt": 1_000_000, "juta": 1_000_000,
        "miliar": 1_000_000_000,
    }
    if suffix:
        n *= mult.get(suffix.lower(), 1)
    return n


# ------------------------------------------------------------------ sector / objective
# (pattern, value, is_direct_word, regex flags)

_SECTORS = [
    (r"\btechnology\b|\bteknologi\b", "technology", True, I),
    (r"\bSaaS\b|\bsoftware\b|\bcloud\b|\bcybersecurity\b|\btech\b", "technology", False, I),
    (r"\bfinancial services\b|\bjasa keuangan\b", "financial_services", True, I),
    (r"\bfintech\b|\bbank(?:ing)?\b|\binsurance\b|\basuransi\b|\bperbankan\b",
     "financial_services", False, I),
    (r"\bhealthcare\b|\bkesehatan\b", "healthcare", True, I),
    (r"\bhospitals?\b|\bpharma\w*\b|\brumah sakit\b|\bmedtech\b", "healthcare", False, I),
    (r"\bmanufacturing\b|\bmanufaktur\b", "manufacturing", True, I),
    (r"\bfactor(?:y|ies)\b|\bpabrik\b|\bindustrial\b", "manufacturing", False, I),
    (r"\bretail\b|\britel\b", "retail", True, I),
    (r"\be-?commerce\b|\bCPG\b", "retail", False, I),
    (r"\btelecom\b|\btelekomunikasi\b", "telecom", True, I),
    (r"\btelco\b|\boperator seluler\b", "telecom", False, I),
    (r"\beducation\b|\bpendidikan\b", "education", True, I),
    (r"\bedtech\b|\buniversit(?:y|ies|as)\b", "education", False, I),
]

_OBJECTIVES = [
    (r"\bdemand gen(?:eration)?\b", "demand_generation", True, I),
    (r"\blead gen(?:eration)?\b", "lead_generation", True, I),
    (r"\bpipeline accelerat\w*|\baccelerate (?:the |our )?pipeline\b",
     "pipeline_acceleration", True, I),
    (r"\bbrand awareness\b|\bawareness\b|\bkesadaran merek\b", "brand_awareness", True, I),
    (r"\bwebinar\b|\bevent registration\b|\bregistrations?\b|\bpendaftar\w*",
     "event_registration", False, I),
    (r"\bMQLs?\b|\bleads\b|\bprospek\b", "lead_generation", False, I),
]


def _first_keyword(text: str, table) -> FieldValue:
    best = None
    for pattern, value, direct, flags in table:
        m = re.search(pattern, text, flags)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, value, direct)
    if not best:
        return FieldValue()
    m, value, direct = best
    return FieldValue(
        value=value,
        status="found" if direct else "inferred",
        source_quote=_sentence(text, m.start(), m.end()),
        note=None if direct else f"'{m.group(0)}' mapped to {value}",
    )


# ------------------------------------------------------------------ audience

_AUDIENCE = re.compile(
    r"\b(?:target audience|audience|ICP|targeting|sasaran|target(?:kan)?)\s*(?:is|are|:|-|=)?\s*"
    r"([^.\n;]{3,160})",
    I,
)


def _audience(text: str) -> FieldValue:
    for m in _AUDIENCE.finditer(text):
        captured = m.group(1).strip()
        if re.match(r"[\d$€]|Rp|USD|IDR", captured, I):  # "target 500 MQLs" is a volume
            continue
        return FieldValue(value=captured, status="found", source_quote=m.group(0).strip())
    return FieldValue()


# ------------------------------------------------------------------ geo
# Short codes are case-sensitive so the English word "us" is never read as the United States.

_GEO = [
    (r"\bNorth America\b|\bAmerika Utara\b", "NA", True, I),
    (r"\bNA\b", "NA", True, 0),
    (r"\bUnited States\b|\bCanada\b", "NA", False, I),
    (r"\bUSA?\b", "NA", False, 0),
    (r"\bEMEA\b", "EMEA", True, 0),
    (r"\bEurope\b|\bEropa\b|\bGermany\b|\bMiddle East\b", "EMEA", False, I),
    (r"\bUK\b", "EMEA", False, 0),
    (r"\bAPAC\b", "APAC", True, 0),
    (r"\bAsia[- ]Pacific\b|\bAsia Pasifik\b", "APAC", True, I),
    (
        (
            r"\bSoutheast Asia\b|\bAsia Tenggara\b|\bIndonesia\b|\bSingapore\b"
            r"|\bAustralia\b|\bJapan\b|\bIndia\b"
        ),
        "APAC", False, I,
    ),
    (r"\bSEA\b", "APAC", False, 0),
    (r"\bLATAM\b", "LATAM", True, 0),
    (r"\bLatin America\b|\bAmerika Latin\b", "LATAM", True, I),
    (r"\bBrazil\b|\bMexico\b", "LATAM", False, I),
]


def _geo(text: str) -> FieldValue:
    hits = []
    for pattern, region, direct, flags in _GEO:
        for m in re.finditer(pattern, text, flags):
            hits.append((m.start(), m, region, direct))
    if not hits:
        return FieldValue()
    hits.sort(key=lambda h: h[0])
    regions: list[str] = []
    mapped: list[str] = []
    for _, m, region, direct in hits:
        if region not in regions:
            regions.append(region)
        if not direct:
            mapped.append(f"{m.group(0)}→{region}")
    first = hits[0][1]
    return FieldValue(
        value=regions,
        status="inferred" if mapped else "found",
        source_quote=_sentence(text, first.start(), first.end()),
        note=("mapped " + ", ".join(dict.fromkeys(mapped))) if mapped else None,
    )


# ------------------------------------------------------------------ deliverables

_NUM = r"(\d[\d.,]*)\s*(k|rb|ribu)?\s*(?:x\s*)?"
_DELIVERABLES = [
    ("content_syndication_lead", r"content[- ]syndication(?: leads?)?|CS leads?"),
    ("intent_qualified_lead", r"intent[- ](?:qualified |based )?leads?|IQLs?"),
    ("mql", r"MQLs?|marketing[- ]qualified leads?"),
    ("appointment_setting", r"(?:booked |sales )?(?:appointments?|meetings?)|janji temu"),
    ("event_registration",
     r"(?:event |webinar )?registrations?|registran|pendaftar(?:an)?(?: webinar)?"),
    # Not on the rate card: extracted honestly so pricing can flag them for feasibility review.
    ("video_production", r"(?:explainer )?videos?"),
    ("podcast_episode", r"podcast(?: episodes?)?"),
    ("whitepaper", r"white ?papers?"),
]


def _deliverables(text: str) -> FieldValue:
    found: list[tuple[int, int, str, int]] = []
    taken: list[tuple[int, int]] = []
    for dtype, alt in _DELIVERABLES:
        for m in re.finditer(rf"(?<![\w$€.,]){_NUM}(?:{alt})\b", text, I):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            n = _parse_number(m.group(1), m.group(2))
            if n is None or n != n.to_integral_value() or n <= 0:
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), dtype, int(n)))
    if not found:
        return FieldValue()
    found.sort()
    items = [{"type": d, "volume": v} for _, _, d, v in found]
    quotes = [text[s:e] for s, e, _, _ in found]
    return FieldValue(
        value=items,
        status="found",
        source_quote=_sentence(text, found[0][0], found[0][1]),
        note="matched: " + "; ".join(quotes),
    )


# ------------------------------------------------------------------ budget

_MONEY = [
    (re.compile(r"(?:US\$|USD\s?|\$)\s?(\d[\d.,]*)\s*(k|m|million)?\b", I), "USD"),
    (re.compile(r"\b(\d[\d.,]*)\s*(k|m)?\s*USD\b", I), "USD"),
    (re.compile(r"(?:\bRp\.?|\bIDR)\s?(\d[\d.,]*)\s*(juta|jt|miliar|rb|ribu)?\b", I), "IDR"),
    (re.compile(r"(?:€|\bEUR\s?)(\d[\d.,]*)\s*(k|m)?\b", I), "EUR"),
]
_BUDGET_WORD = re.compile(r"budget|anggaran|\bcap\b|spend|dana", I)


def _budget(text: str) -> FieldValue:
    matches = []
    seen_spans: list[tuple[int, int]] = []
    for rx, currency in _MONEY:
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in seen_spans):
                continue
            n = _parse_number(m.group(1), m.group(2))
            if n is not None and n > 0:
                seen_spans.append((m.start(), m.end()))
                matches.append((m, currency, n))
    if not matches:
        return FieldValue()
    matches.sort(key=lambda x: x[0].start())
    near = [x for x in matches if _BUDGET_WORD.search(text[max(0, x[0].start() - 40) : x[0].start()])]
    if near:
        m, currency, n = near[0]
        status, note = "found", None
    elif len(matches) == 1:
        m, currency, n = matches[0]
        status, note = "inferred", "only monetary amount in the brief; assumed to be the budget"
    else:
        return FieldValue(note="several amounts mentioned and none is labelled as budget")
    return FieldValue(
        value={"amount": str(n), "currency": currency},
        status=status,
        source_quote=_sentence(text, m.start(), m.end()),
        note=note,
    )


# ------------------------------------------------------------------ timeline

_MONTHS = {
    name.lower(): i
    for i in range(1, 13)
    for name in (calendar.month_name[i], calendar.month_abbr[i])
}
_MONTHS.update({"sept": 9, "januari": 1, "februari": 2, "maret": 3, "mei": 5, "juni": 6,
                "juli": 7, "agustus": 8, "oktober": 10, "desember": 12})
_MONTH_ALT = "|".join(sorted((re.escape(k) for k in _MONTHS if k), key=len, reverse=True))

_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MDY = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b", I)
_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\.?\s+(20\d{{2}})\b", I)
_QUARTER = re.compile(r"\b(?:by |end of |akhir )?Q([1-4])\s*(20\d{2})\b", I)
_RELATIVE = re.compile(r"\b(?:in|within|dalam)\s+(\d{1,2})\s+(weeks?|minggu|months?|bulan)\b", I)


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _timeline(text: str, received_on: date) -> FieldValue:
    dates: list[tuple[int, int, date]] = []
    for m in _ISO.finditer(text):
        if dt := _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            dates.append((m.start(), m.end(), dt))
    for m in _MDY.finditer(text):
        if dt := _safe_date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))):
            dates.append((m.start(), m.end(), dt))
    for m in _DMY.finditer(text):
        if dt := _safe_date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))):
            dates.append((m.start(), m.end(), dt))
    if dates:
        dates.sort()
        start = dates[0][2] if len(dates) > 1 else None
        end = dates[-1][2]
        return FieldValue(
            value={"start_date": start.isoformat() if start else None,
                   "end_date": end.isoformat()},
            status="found",
            source_quote=_sentence(text, dates[-1][0], dates[-1][1]),
        )
    if m := _QUARTER.search(text):
        q, y = int(m.group(1)), int(m.group(2))
        last_month = q * 3
        end = date(y, last_month, calendar.monthrange(y, last_month)[1])
        return FieldValue(
            value={"start_date": None, "end_date": end.isoformat()},
            status="inferred",
            source_quote=_sentence(text, m.start(), m.end()),
            note=f"'{m.group(0).strip()}' read as end of quarter",
        )
    if m := _RELATIVE.search(text):
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * 7 if unit.startswith(("week", "minggu")) else n * 30
        end = received_on + timedelta(days=days)
        return FieldValue(
            value={"start_date": None, "end_date": end.isoformat()},
            status="inferred",
            source_quote=_sentence(text, m.start(), m.end()),
            note=f"'{m.group(0)}' counted from received date {received_on.isoformat()}",
        )
    return FieldValue()


# ------------------------------------------------------------------ provider


class MockRulesExtractor:
    name = "mock"
    model_version = "mock-rules-v1"

    def extract(self, raw_text: str, *, received_on: date) -> ExtractionOutput:
        fields = {
            "client_sector": _first_keyword(raw_text, _SECTORS),
            "campaign_objective": _first_keyword(raw_text, _OBJECTIVES),
            "audience": _audience(raw_text),
            "geo": _geo(raw_text),
            "deliverables": _deliverables(raw_text),
            "budget": _budget(raw_text),
            "timeline": _timeline(raw_text, received_on),
        }
        assert set(fields) == set(REQUIRED_FIELDS)
        return ExtractionOutput(fields=fields, provider=self.name, model_version=self.model_version)
