"""Seeded payload corpus: the case matrix and its generation.

The corpus is the fairness mechanism of the whole suite. `barcode-bench gen`
runs exactly once per benchmark run, on the host, and materialises every
payload string into ``runs/<run_id>/corpus.json``; that file is bind-mounted
read-only into every encoder container. Every encoder therefore times the
*identical* byte-for-byte payloads - no per-encoder regeneration, no room for
drift.

Payloads are semi-random: realistic in shape (digit runs, alphanumeric codes,
URLs, prose, Latin-1/UTF-8/Shift-JIS text) but drawn from a seeded RNG so that
(a) each of the T trials per case gets *different* content, defeating any
content-keyed caching inside a library, and (b) the whole corpus is exactly
reproducible from its master seed. Each case draws from
``random.Random(f"{seed}:{case_id}")`` - keying the stream by case id means
adding or removing a case never reshuffles the payloads of the others.

Case ids are stable identifiers (``qr-alnum-eclM``); the matrix covers, per
symbology, the payload categories at increasing size up to near-capacity
(sized from :mod:`barcode_bench.capacity`), plus sweeps over the axes that
symbology actually has (QR/Aztec/PDF417 error-correction levels, PDF417
column counts, QR encodings incl. kanji).
"""

from __future__ import annotations

import base64
import json
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from barcode_bench.adapters.base import EncodeOptions
from barcode_bench.capacity import near_capacity_target

BENCH_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = BENCH_ROOT / "runs"

SCHEMA_VERSION = 1

# The symbologies the case matrix covers, in report order. Also the valid
# values for `gen --symbology` / `all --symbology`.
SYMBOLOGIES = ("qr", "datamatrix", "datamatrix_rect", "aztec", "pdf417")

QR_ALNUM_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

# Small embedded vocabulary for prose-like payloads. Realism here means
# "looks like text a real payload would carry", not linguistic fidelity.
_WORDS = (
    "order tracking parcel invoice customer account delivery status update "
    "warehouse shipment carrier label package express standard priority "
    "return reference number code batch serial product item quantity total "
    "amount value date time location address street city postcode country "
    "contact phone email service centre depot route manifest scan gate dock"
).split()

_LATIN1_WORDS = (
    "café Zürich naïve façade jalapeño señor über müller königsberg fähre "
    "château crème brûlée pêche déjà résumé garçon voilà £100 25°C"
).split()

# Latin-1 code points in the 0xA0..0xBF band - currency, maths and
# typographic symbols. Their single bytes collide with Shift-JIS half-width
# katakana (single-byte 0xA1..0xDF), so a byte-mode symbol carrying them with
# no ECI is charset-ambiguous: a reader that guesses or defaults to Shift-JIS
# decodes them as katakana. Contrast the accented *letters* (0xC0..0xFF) in
# `_LATIN1_WORDS`, which decoders reliably guess back as Latin-1. See
# `_latin1_ambiguous` for the ambiguity rationale and measured encoder results.
_LATIN1_SYMBOLS = "£¥¤§±°µ·¶½¼¾«»©®¡¿"

# Kept to characters that are both Shift-JIS encodable and inside QR kanji
# mode's double-byte ranges (JIS X 0208), so a conformant QR encoder can use
# kanji mode for the whole payload.
_KANJI_CHARS = "こんにちは世界日本語文字漢字試験符号化情報携帯電話東京大阪京都カタカナテスト番号"

_UTF8_EXTRAS = ("日本語", "中文", "한국어", "München", "🙂", "📦", "→", "€49.99")


@dataclass(frozen=True)
class Case:
    """One benchmark case: options plus T concrete trial payloads."""

    case_id: str
    symbology: str
    category: str
    options: EncodeOptions
    payload_spec: str
    trials: tuple[str, ...]
    quick: bool


# ---------------------------------------------------------------------------
# Payload generators. Each takes the case RNG and returns one payload string;
# generators must consume the RNG deterministically.


def _digits(rng: random.Random, lo: int, hi: int) -> str:
    return _digits_exact(rng, rng.randint(lo, hi))


def _qr_alnum(rng: random.Random, lo: int, hi: int) -> str:
    n = rng.randint(lo, hi)
    return "".join(rng.choice(QR_ALNUM_CHARSET) for _ in range(n))


def _alnum_tiny(rng: random.Random) -> str:
    n = rng.randint(4, 6)
    return "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(n))


def _digits_even(rng: random.Random) -> str:
    """Even digit count (24..48): DM ASCII mode packs digit *pairs*, so an
    even run is the best case - every pair costs one codeword."""
    return _digits_exact(rng, 2 * rng.randint(12, 24))


def _digits_odd(rng: random.Random) -> str:
    """Odd digit count (25..49): the dangling digit can't pair and costs a
    whole codeword - an encoder mishandling the tail wastes more."""
    return _digits_exact(rng, 2 * rng.randint(12, 24) + 1)


def _digits_exact(rng: random.Random, n: int) -> str:
    # No leading zero: a real numeric id rarely has one, and some libraries
    # treat leading zeros specially in numeric modes.
    return rng.choice("123456789") + "".join(rng.choice("0123456789") for _ in range(n - 1))


def _numeric_prefix(rng: random.Random) -> str:
    """Serial-number shape 'A123456789012': one letter then an even digit
    run - stresses the mode boundary going *into* digit-pair packing."""
    return rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") + _digits_exact(rng, 2 * rng.randint(6, 10))


def _numeric_suffix(rng: random.Random) -> str:
    """'123456789012A': even digit run then one letter - the boundary going
    *out* of digit-pair packing."""
    return _digits_exact(rng, 2 * rng.randint(6, 10)) + rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")


def _charset_run(rng: random.Random, charset: str, lo: int, hi: int) -> str:
    n = rng.randint(lo, hi)
    return "".join(rng.choice(charset) for _ in range(n))


def _upper_c40(rng: random.Random) -> str:
    """Pure uppercase+space+digits: the C40 basic set (DM), Alpha sub-mode
    (PDF417 text compaction), Upper mode (Aztec)."""
    return _charset_run(rng, "ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789", 42, 60)


def _lower_text(rng: random.Random) -> str:
    """Pure lowercase+space: DM Text mode / PDF417 Lower sub-mode / Aztec
    Lower mode. An encoder stuck in C40 pays double shifts here."""
    return _charset_run(rng, "abcdefghijklmnopqrstuvwxyz ", 42, 60)


def _edifact_charset(rng: random.Random) -> str:
    """EDIFACT-mode charset (0x40..0x5F heavy): @ [ \\ ] ^ _ plus uppercase.

    Purpose-built mode detector: EDIFACT packs 4 chars into 3 codewords,
    while C40 pays 2 units for every non-basic char - a measurable
    codeword gap for encoders lacking EDIFACT (pyStrich chose C40 for this
    charset when probed, spending ~2 extra codewords over EDIFACT).
    """
    return _charset_run(rng, "ABCDEFGHIJKLMNOPQRSTUVWXYZ@[]^_", 42, 60)


def _x12_charset(rng: random.Random) -> str:
    """X12 charset: uppercase, digits, space and the * > terminators."""
    return _charset_run(rng, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 *>", 42, 60)


def _mixed_blocks(rng: random.Random) -> str:
    """Alternating charset runs, built to force encodation-mode switching.

    Uppercase runs suit C40/alphanumeric modes, digit runs suit numeric/X12
    packing, and short lowercase/punctuation runs force byte/text excursions -
    a good mode-switching encoder produces far fewer codewords here than
    a single-mode one.
    """
    parts: list[str] = []
    for _ in range(rng.randint(3, 5)):
        parts.append("".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(rng.randint(8, 12))))
        parts.append("".join(rng.choice("0123456789") for _ in range(rng.randint(10, 20))))
        parts.append("".join(rng.choice("abcdefghijklmnopqrstuvwxyz_#@") for _ in range(rng.randint(4, 8))))
    return "".join(parts)


def _url(rng: random.Random) -> str:
    host = rng.choice(_WORDS) + rng.choice(_WORDS)
    path = "/".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 3)))
    ident = "".join(rng.choice("0123456789") for _ in range(12))
    ref = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(10))
    return f"https://{host}.example/{path}?id={ident}&ref={ref}"


def _sentence(rng: random.Random, words: tuple[str, ...] | list[str]) -> str:
    n = rng.randint(6, 12)
    picked = [rng.choice(words) for _ in range(n)]
    return (picked[0].capitalize() + " " + " ".join(picked[1:])).strip() + "."


def _text_ascii(rng: random.Random, lo: int, hi: int) -> str:
    target = rng.randint(lo, hi)
    return _text_ascii_exact(rng, target)


def _text_ascii_exact(rng: random.Random, target: int) -> str:
    out = ""
    while len(out) < target:
        out += ("" if not out else " ") + _sentence(rng, _WORDS)
    return out[:target].rstrip() or "x" * target


def _latin1_text(rng: random.Random) -> str:
    target = rng.randint(40, 120)
    out = ""
    while len(out) < target:
        words = [rng.choice(_LATIN1_WORDS if rng.random() < 0.4 else _WORDS) for _ in range(8)]
        out += ("" if not out else " ") + " ".join(words)
    payload = out[:target].rstrip()
    payload.encode("iso-8859-1")  # generator invariant: must be Latin-1 clean
    return payload


def _latin1_ambiguous(rng: random.Random) -> str:
    """Latin-1 text concentrated in the 0xA0..0xBF symbol band.

    The sibling of :func:`_latin1_text`, but built from the
    currency/maths/typographic symbols (`_LATIN1_SYMBOLS`) whose Latin-1 bytes
    collide with Shift-JIS half-width katakana, rather than the accented
    *letters* decoders reliably guess as Latin-1. This makes a *byte-mode,
    no-ECI* symbol charset-ambiguous - a reader that guesses or
    defaults to Shift-JIS decodes ``£50 ± 3°C`` as ``｣50 ｱ 3ｰC``. Only an
    explicit ECI 3 removes the ambiguity, so the case separates ECI-emitting
    encoders from those relying on the byte-mode default. Measured over 120
    payloads (all decoded by the pinned zxing-cpp 3.0.0): pyStrich emits an
    explicit ECI 3 for Latin-1 and is 100 % correct; segno and zint default to
    byte-mode Latin-1 with *no* ECI and are 0 % correct - the reader guesses
    Shift-JIS, so ``£50 ± 3°C`` decodes as ``｣50 ｱ 3ｰC``. python-qrcode round-
    trips 100 % but only incidentally: it always emits UTF-8 bytes (also no
    ECI), which zxing-cpp happens to guess. The failure is decoder-dependent -
    a Latin-1-defaulting reader would flip which encoders pass - so this case
    is pinned-decoder and demonstrative, not a universal verdict.

    Standards basis: ECI is required by the standard and the default
    assumption without it would be ISO-8859-1, not UTF-8.

    Accented Latin letters are kept out of it: those live at
    0xE0..0xFF, which are Shift-JIS double-byte lead bytes, so a few of them
    break the katakana interpretation and let a guessing decoder recover
    Latin-1 - which would make the probe fire only intermittently. Keeping the
    payload to ASCII + the 0xA0..0xBF band keeps the whole byte stream valid
    single-byte Shift-JIS, so the misdecode is reliable. Content stays
    realistic (prices, temperatures, fractions) without them.
    """
    target = rng.randint(40, 120)
    parts: list[str] = []
    n = 0
    while n < target:
        r = rng.random()
        if r < 0.4:
            frag = f"{rng.choice('£¥¤')}{_digits_exact(rng, rng.randint(2, 5))}"
        elif r < 0.65:
            frag = f"{rng.randint(1, 99)}±{rng.randint(1, 9)}°C"
        elif r < 0.8:
            frag = rng.choice(_WORDS)
        else:
            frag = rng.choice(_LATIN1_SYMBOLS)
        parts.append(frag)
        n += len(frag) + 1
    payload = " ".join(parts)[:target].rstrip()
    payload.encode("iso-8859-1")  # generator invariant: must be Latin-1 clean
    # generator invariant: enough 0xA0..0xBF symbols to stay charset-ambiguous
    assert sum(0xA0 <= ord(c) <= 0xBF for c in payload) >= 3, payload
    return payload


def _utf8_text(rng: random.Random) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(4, 8)):
        parts.append(rng.choice(_WORDS) if rng.random() < 0.6 else rng.choice(_UTF8_EXTRAS))
    return " ".join(parts)


def _kanji(rng: random.Random) -> str:
    n = rng.randint(10, 30)
    payload = "".join(rng.choice(_KANJI_CHARS) for _ in range(n))
    payload.encode("shift_jis")  # generator invariant: must be Shift-JIS clean
    return payload


def _xml_exact(rng: random.Random, target: int) -> str:
    """Well-formed XML document of exactly ``target`` chars.

    Real-world near-capacity shape: markup-heavy ASCII with angle brackets,
    quotes and repeated element names - lowercase-dominated with punctuation
    that no single text sub-mode covers. A `<pad>` element sized to
    the remaining budget keeps the document well-formed at the exact target
    (truncating markup would fake realism).
    """
    header = '<?xml version="1.0" encoding="UTF-8"?><manifest>'
    footer = "</manifest>"
    pad_overhead = len("<pad></pad>")
    body = ""
    while True:
        ident = "".join(rng.choice("0123456789") for _ in range(10))
        status, name_a, name_b = (rng.choice(_WORDS) for _ in range(3))
        item = (
            f'<item id="{ident}" status="{status}">'
            f"<name>{name_a} {name_b}</name><qty>{rng.randint(1, 999)}</qty></item>"
        )
        if len(header) + len(body) + len(item) + pad_overhead + len(footer) > target:
            break
        body += item
    fill = target - (len(header) + len(body) + pad_overhead + len(footer))
    pad = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(fill))
    return f"{header}{body}<pad>{pad}</pad>{footer}"


def _json_exact(rng: random.Random, target: int) -> str:
    """Well-formed JSON document of exactly ``target`` chars.

    The API-payload sibling of :func:`_xml_exact`: braces, quotes, colons
    and repeated keys around the same vocabulary. A final ``"pad"`` string
    member absorbs the remaining budget so the document stays parseable at
    the exact target length.
    """
    header = '{"orders":['
    pad_overhead = len('],"pad":""}')
    body = ""
    while True:
        ident = "".join(rng.choice("0123456789") for _ in range(10))
        status, name_a, name_b = (rng.choice(_WORDS) for _ in range(3))
        item = (
            f'{{"id":"{ident}","status":"{status}",'
            f'"name":"{name_a} {name_b}","qty":{rng.randint(1, 999)}}}'
        )
        sep = "," if body else ""
        if len(header) + len(body) + len(sep) + len(item) + pad_overhead > target:
            break
        body += sep + item
    fill = target - (len(header) + len(body) + pad_overhead)
    pad = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(fill))
    payload = f'{header}{body}],"pad":"{pad}"}}'
    json.loads(payload)  # generator invariant: must stay parseable JSON
    return payload


def _base64_exact(rng: random.Random, target: int) -> str:
    """Base64 blob of ≈``target`` chars (rounded down to the 4-char
    block grid; '='-padded on ~2/3 of trials, like real encodings of
    arbitrary-length data). Mixed case + digits + ``+/=`` - content that
    defeats every single-charset text mode and pushes encoders toward byte
    compaction."""
    n_chars = target - target % 4
    n_bytes = (n_chars // 4) * 3 - rng.choice((0, 1, 2))
    return base64.b64encode(rng.randbytes(n_bytes)).decode("ascii")


def _url_long_exact(rng: random.Random, target: int) -> str:
    """URL of exactly ``target`` chars: host, multi-segment path, long query
    string, with realistic percent-encoded runs (%20, %2F, UTF-8 multibyte
    escapes) scattered through segment and parameter values."""
    encoded = ("%20", "%2F", "%3D", "%26", "%C3%A9", "%C3%BC", "%E2%82%AC")
    host = f"{rng.choice(_WORDS)}-{rng.choice(_WORDS)}.example"
    path = "/".join(
        rng.choice(_WORDS) + (f"%20{rng.choice(_WORDS)}" if rng.random() < 0.4 else "")
        for _ in range(rng.randint(2, 4))
    )
    url = f"https://{host}/{path}?src={rng.choice(_WORDS)}"
    n = 0
    while len(url) < target:
        n += 1
        val = "".join(
            rng.choice(encoded)
            if rng.random() < 0.25
            else rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            for _ in range(rng.randint(8, 24))
        )
        url += f"&{rng.choice(_WORDS)}{n}={val}"
    url = url[:target]
    # the cut may have split a %XX triplet; a trailing '%' or '%X' is not a
    # valid escape, so overwrite the stub with unreserved filler
    cut = url.rfind("%", max(0, target - 2))
    if cut != -1:
        url = url[:cut] + "x" * (target - cut)
    return url


_BCBP_AIRPORTS = (
    "LHR", "JFK", "CDG", "FRA", "AMS", "MAD", "DUB", "MAN",
    "EDI", "BOS", "ORD", "SFO", "YYZ", "SYD", "SIN", "DXB",
)
_BCBP_CARRIERS = ("BA", "AA", "AF", "LH", "KL", "IB", "EI", "VS", "UA", "DL")
_BCBP_SURNAMES = ("SMITH", "JONES", "GARCIA", "MULLER", "ROSSI", "TANAKA", "OBRIEN", "NOVAK")
_BCBP_GIVEN = ("JOHN", "MARIA", "WEI", "AISHA", "LARS", "PRIYA", "SEAN", "EMMA")


def _bcbp(rng: random.Random) -> str:
    """IATA BCBP-like boarding pass (Resolution 792 'M' format), 1-3 legs.

    The real-world payload of airline 2D symbols (typically Aztec or PDF417,
    increasingly QR/DM): fixed-width uppercase fields - 20-char padded name,
    7-char PNR, airport triplets, flight/julian-date/seat/sequence numbers -
    with an empty conditional section ('00'). One leg = the spec's 60-char
    mandatory block; upper-heavy content with digit runs, suited to
    C40/alphanumeric modes.
    """
    legs = rng.randint(1, 3)
    name = f"{rng.choice(_BCBP_SURNAMES)}/{rng.choice(_BCBP_GIVEN)}"[:20].ljust(20)
    out = f"M{legs}{name}E"
    for _ in range(legs):
        pnr = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ123456789") for _ in range(6))
        origin, dest = rng.sample(_BCBP_AIRPORTS, 2)
        out += (
            f"{pnr} {origin}{dest}{rng.choice(_BCBP_CARRIERS):<3}"
            f"{rng.randint(1, 9999):04d} {rng.randint(1, 365):03d}{rng.choice('FJYM')}"
            f"{rng.randint(1, 45):03d}{rng.choice('ABCDEF')}{rng.randint(1, 300):04d} 100"
        )
    return out


# name -> (spec template, generator). near_capacity is handled separately
# because its target length depends on the case's symbology and EC level.
_GENERATORS: dict[str, tuple[str, Callable[[random.Random], str]]] = {
    "numeric_short": ("digits(6..12)", lambda rng: _digits(rng, 6, 12)),
    "numeric_long": ("digits(100..150)", lambda rng: _digits(rng, 100, 150)),
    "alnum": ("qr_alnum(25..50)", lambda rng: _qr_alnum(rng, 25, 50)),
    "tiny": ("alnum_tiny(4..6)", _alnum_tiny),
    "numeric_even": ("digits_even(24..48)", _digits_even),
    "numeric_odd": ("digits_odd(25..49)", _digits_odd),
    "numeric_prefix": ("letter+digits_even(12..20)", _numeric_prefix),
    "numeric_suffix": ("digits_even(12..20)+letter", _numeric_suffix),
    "upper_c40": ("upper_c40(42..60)", _upper_c40),
    "lower_text": ("lower_text(42..60)", _lower_text),
    "edifact_charset": ("edifact_charset(42..60)", _edifact_charset),
    "x12_charset": ("x12_charset(42..60)", _x12_charset),
    "mixed_modes": ("mixed_blocks(3..5)", _mixed_blocks),
    "url": ("url()", _url),
    "text_para": ("text_ascii(200..500)", lambda rng: _text_ascii(rng, 200, 500)),
    "latin1": ("latin1_text(40..120)", _latin1_text),
    "latin1_ambiguous": ("latin1_ambiguous(40..120)", _latin1_ambiguous),
    "utf8": ("utf8_text(4..8 tokens)", _utf8_text),
    "kanji": ("kanji(10..30)", _kanji),
    "bcbp": ("bcbp(1..3 legs)", _bcbp),
}

# Categories whose payload length is sized from the symbology's capacity
# (near_capacity_target), not a fixed range: the classic ASCII-prose case
# plus the realistic large-payload shapes (XML documents, base64 blobs,
# percent-encoded URLs). All ASCII byte-mode-safe by construction, so the
# same 85%-of-byte-capacity target applies; encoders whose mode selection
# handles the shape badly overflow into encode_error - a finding.
_SIZED_GENERATORS: dict[str, tuple[str, Callable[[random.Random, int], str]]] = {
    "near_capacity": ("text_ascii", _text_ascii_exact),
    "xml_near_capacity": ("xml", _xml_exact),
    "json_near_capacity": ("json", _json_exact),
    "base64_near_capacity": ("base64", _base64_exact),
    "url_near_capacity": ("url_long", _url_long_exact),
}


# ---------------------------------------------------------------------------
# Case matrix


@dataclass(frozen=True)
class _CaseDef:
    case_id: str
    category: str
    options: EncodeOptions
    quick: bool = False
    # RNG stream key override: an `*-auto` case names its declared-encoding
    # sibling here so both draw byte-identical payloads - pinned vs auto then
    # differs in exactly one variable, the charset declaration.
    payloads_from: str | None = None


def _case_defs() -> list[_CaseDef]:
    """The full case matrix. Ids are stable: never renumber, only add."""
    defs: list[_CaseDef] = []

    def qr(case_id: str, category: str, ecl: str, encoding: str | None = None, quick: bool = False) -> None:
        defs.append(
            _CaseDef(case_id, category, EncodeOptions("qr", ec_level=ecl, encoding=encoding), quick)
        )

    qr("qr-numeric_short-eclM", "numeric_short", "M")
    qr("qr-numeric_long-eclM", "numeric_long", "M")
    for ecl in ("L", "M", "Q", "H"):
        qr(f"qr-alnum-ecl{ecl}", "alnum", ecl, quick=(ecl == "M"))
    qr("qr-mixed_modes-eclM", "mixed_modes", "M")
    qr("qr-url-eclM", "url", "M", quick=True)
    qr("qr-text_para-eclM", "text_para", "M")
    qr("qr-latin1-eclM", "latin1", "M", encoding="iso-8859-1")
    # Charset-ambiguity probe: Latin-1 symbols (0xA0..0xBF) that collide with
    # Shift-JIS half-width katakana. Conformant only if the encoder emits an
    # explicit ECI 3; a byte-mode default misdecodes. See `_latin1_ambiguous`.
    qr("qr-latin1_ambiguous-eclM", "latin1_ambiguous", "M", encoding="iso-8859-1")
    qr("qr-utf8-eclM", "utf8", "M", encoding="utf-8")
    qr("qr-kanji-eclM", "kanji", "M", encoding="shift_jis")
    for ecl in ("L", "M", "Q", "H"):
        qr(f"qr-near_capacity-ecl{ecl}", "near_capacity", ecl)
    # Realistic large-payload shapes at one ECL each (sized like
    # near_capacity), plus the boarding-pass shape at its natural size.
    qr("qr-xml_nc-eclM", "xml_near_capacity", "M")
    qr("qr-json_nc-eclM", "json_near_capacity", "M")
    qr("qr-base64_nc-eclM", "base64_near_capacity", "M")
    qr("qr-url_nc-eclM", "url_near_capacity", "M")
    qr("qr-bcbp-eclM", "bcbp", "M")

    def auto_sibling(declared_id: str, category: str, options: EncodeOptions) -> None:
        # The auto (undeclared-charset) twin of a declared-encoding case:
        # identical payloads (payloads_from), encoding=None - measures the
        # library's own charset selection instead of the pinned one. Only
        # encoders whose capabilities claim supports_text_auto take part.
        assert options.encoding is None
        defs.append(
            _CaseDef(f"{declared_id}-auto", category, options, payloads_from=declared_id)
        )

    auto_sibling("qr-latin1-eclM", "latin1", EncodeOptions("qr", ec_level="M"))
    auto_sibling("qr-latin1_ambiguous-eclM", "latin1_ambiguous", EncodeOptions("qr", ec_level="M"))
    auto_sibling("qr-utf8-eclM", "utf8", EncodeOptions("qr", ec_level="M"))
    auto_sibling("qr-kanji-eclM", "kanji", EncodeOptions("qr", ec_level="M"))

    def dm(case_id: str, category: str, encoding: str | None = None, quick: bool = False) -> None:
        # dm_force_square on every DM case: shape-free writers (libzint)
        # otherwise pick DMRE rectangles, and the codeword comparison stops
        # measuring encodation quality and starts measuring symbol-grid
        # granularity. Rectangles deserve their own axis some day.
        defs.append(
            _CaseDef(
                case_id,
                category,
                EncodeOptions("datamatrix", encoding=encoding, dm_force_square=True),
                quick,
            )
        )

    dm("dm-numeric_short", "numeric_short")
    dm("dm-numeric_long", "numeric_long")
    # Numeric-mode edges: ASCII digit-pair packing best case (even), the
    # stranded-digit case (odd), and the mode boundary in each direction.
    dm("dm-numeric_even", "numeric_even")
    dm("dm-numeric_odd", "numeric_odd")
    dm("dm-numeric_prefix", "numeric_prefix")
    dm("dm-numeric_suffix", "numeric_suffix")
    dm("dm-alnum", "alnum", quick=True)
    # Mode-isolating payloads: one charset family each, so a missing/badly
    # chosen encodation mode (C40 / Text / X12 / EDIFACT) shows up as a
    # larger symbol. Caveat: square symbol sizes quantize coarsely; small
    # codeword gaps are most visible for encoders free to pick rectangles.
    dm("dm-upper_c40", "upper_c40")
    dm("dm-lower_text", "lower_text")
    dm("dm-edifact_charset", "edifact_charset")
    dm("dm-x12_charset", "x12_charset")
    dm("dm-mixed_modes", "mixed_modes")
    dm("dm-url", "url", quick=True)
    dm("dm-text_para", "text_para")
    dm("dm-latin1", "latin1", encoding="iso-8859-1")
    dm("dm-utf8", "utf8", encoding="utf-8")
    auto_sibling("dm-latin1", "latin1", EncodeOptions("datamatrix", dm_force_square=True))
    auto_sibling("dm-utf8", "utf8", EncodeOptions("datamatrix", dm_force_square=True))
    dm("dm-near_capacity", "near_capacity")
    dm("dm-xml_nc", "xml_near_capacity")
    dm("dm-json_nc", "json_near_capacity")
    dm("dm-base64_nc", "base64_near_capacity")
    dm("dm-url_nc", "url_near_capacity")
    dm("dm-bcbp", "bcbp")

    def dmr(case_id: str, category: str) -> None:
        # Rectangular DM is its own symbology: only encoders that can *force*
        # rectangles play (currently treepoem; pylibdmtx planned). Categories
        # are limited to payloads that fit the largest standard rectangle
        # (16x48 = 49 data codewords) so auto-sizing never overflows.
        defs.append(_CaseDef(case_id, category, EncodeOptions("datamatrix_rect")))

    dmr("dmr-numeric_short", "numeric_short")
    dmr("dmr-alnum", "alnum")
    dmr("dmr-upper_c40", "upper_c40")
    dmr("dmr-lower_text", "lower_text")
    dmr("dmr-x12_charset", "x12_charset")

    def az(case_id: str, category: str, ecc: str, encoding: str | None = None, quick: bool = False) -> None:
        defs.append(
            _CaseDef(case_id, category, EncodeOptions("aztec", ec_level=ecc, encoding=encoding), quick)
        )

    az("az-tiny-ecc23", "tiny", "23")  # small enough for a compact symbol
    az("az-numeric_short-ecc23", "numeric_short", "23")
    az("az-numeric_long-ecc23", "numeric_long", "23")
    for ecc in ("10", "23", "50"):
        az(f"az-alnum-ecc{ecc}", "alnum", ecc, quick=(ecc == "23"))
    az("az-upper_c40-ecc23", "upper_c40", "23")
    az("az-lower_text-ecc23", "lower_text", "23")
    az("az-mixed_modes-ecc23", "mixed_modes", "23")
    az("az-url-ecc23", "url", "23", quick=True)
    az("az-text_para-ecc23", "text_para", "23")
    az("az-latin1-ecc23", "latin1", "23", encoding="iso-8859-1")
    az("az-utf8-ecc23", "utf8", "23", encoding="utf-8")
    auto_sibling("az-latin1-ecc23", "latin1", EncodeOptions("aztec", ec_level="23"))
    auto_sibling("az-utf8-ecc23", "utf8", EncodeOptions("aztec", ec_level="23"))
    az("az-near_capacity-ecc23", "near_capacity", "23")
    az("az-xml_nc-ecc23", "xml_near_capacity", "23")
    az("az-json_nc-ecc23", "json_near_capacity", "23")
    az("az-base64_nc-ecc23", "base64_near_capacity", "23")
    az("az-url_nc-ecc23", "url_near_capacity", "23")
    az("az-bcbp-ecc23", "bcbp", "23")

    def pdf(
        case_id: str,
        category: str,
        ecl: str,
        columns: int | None = None,
        encoding: str | None = None,
        quick: bool = False,
    ) -> None:
        defs.append(
            _CaseDef(
                case_id,
                category,
                EncodeOptions("pdf417", ec_level=ecl, encoding=encoding, pdf417_columns=columns),
                quick,
            )
        )

    pdf("pdf-numeric_long-ecl2", "numeric_long", "2")
    for ecl in ("2", "5", "8"):
        pdf(f"pdf-alnum-ecl{ecl}", "alnum", ecl, quick=(ecl == "2"))
    pdf("pdf-upper_c40-ecl2", "upper_c40", "2")
    pdf("pdf-lower_text-ecl2", "lower_text", "2")
    pdf("pdf-mixed_modes-ecl2", "mixed_modes", "2")
    pdf("pdf-url-ecl2", "url", "2", quick=True)
    pdf("pdf-text_para-ecl2", "text_para", "2")
    pdf("pdf-text_para-ecl2-cols6", "text_para", "2", columns=6)
    pdf("pdf-text_para-ecl2-cols12", "text_para", "2", columns=12)
    pdf("pdf-latin1-ecl2", "latin1", "2", encoding="iso-8859-1")
    pdf("pdf-utf8-ecl2", "utf8", "2", encoding="utf-8")
    auto_sibling("pdf-latin1-ecl2", "latin1", EncodeOptions("pdf417", ec_level="2"))
    auto_sibling("pdf-utf8-ecl2", "utf8", EncodeOptions("pdf417", ec_level="2"))
    pdf("pdf-near_capacity-ecl5", "near_capacity", "5")
    # ecl5 matches pdf-near_capacity: higher EC would breach the 929-codeword
    # ceiling for byte-heavy shapes. BCBP is small, so it runs at the base ecl2.
    pdf("pdf-xml_nc-ecl5", "xml_near_capacity", "5")
    pdf("pdf-json_nc-ecl5", "json_near_capacity", "5")
    pdf("pdf-base64_nc-ecl5", "base64_near_capacity", "5")
    pdf("pdf-url_nc-ecl5", "url_near_capacity", "5")
    pdf("pdf-bcbp-ecl2", "bcbp", "2")

    # --- SVG output-format axis --------------------------------------------
    # An SVG twin of *every* PNG case above - the vector axis mirrors the PNG
    # workload exactly (same cases). Each twin shares payloads with its PNG
    # sibling (payloads_from), so the only variable is output_format. Only
    # encoders declaring supports_svg for the symbology take part; the rest
    # record unsupported. Geometry is not re-derived for vectors (identical to
    # the PNG twin) - this axis measures encode time and validity.
    for cd in list(defs):
        defs.append(
            _CaseDef(
                f"{cd.case_id}-svg",
                cd.category,
                replace(cd.options, output_format="svg"),
                quick=cd.quick,
                payloads_from=cd.payloads_from or cd.case_id,
            )
        )

    return defs


def build_cases(seed: int, trials: int, symbology: str | None = None) -> list[Case]:
    """The full case matrix, or (when ``symbology`` is given) only that
    symbology's cases. Filtering by symbology leaves every kept case's payloads
    byte-identical to a full run of the same seed, since each case draws from
    its own ``seed:case_id`` stream."""
    if symbology is not None and symbology not in SYMBOLOGIES:
        raise ValueError(f"unknown symbology {symbology!r}; expected one of {SYMBOLOGIES}")
    cases: list[Case] = []
    for cd in _case_defs():
        if symbology is not None and cd.options.symbology != symbology:
            continue
        # payloads_from: an auto twin seeds its RNG with the *sibling's* id,
        # reproducing that case's payloads exactly (same generator, same
        # stream) - the corpus-stability invariant still holds because no
        # existing stream is consumed differently.
        rng = random.Random(f"{seed}:{cd.payloads_from or cd.case_id}")
        if cd.category in _SIZED_GENERATORS:
            name, sized_gen = _SIZED_GENERATORS[cd.category]
            target = near_capacity_target(cd.options.symbology, cd.options.ec_level)
            spec = f"{name}(len={target})"
            payloads = tuple(sized_gen(rng, target) for _ in range(trials))
        else:
            spec, gen = _GENERATORS[cd.category]
            payloads = tuple(gen(rng) for _ in range(trials))
        if cd.payloads_from:
            spec += f" [payloads = {cd.payloads_from}]"
        cases.append(
            Case(
                case_id=cd.case_id,
                symbology=cd.options.symbology,
                category=cd.category,
                options=cd.options,
                payload_spec=spec,
                trials=payloads,
                quick=cd.quick,
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Serialisation


def corpus_to_json(seed: int, trials: int, cases: list[Case]) -> str:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "trials_per_case": trials,
        "cases": [
            {**asdict(c), "options": asdict(c.options), "trials": list(c.trials)} for c in cases
        ],
    }
    # ensure_ascii + sort_keys: the same seed must produce a byte-identical
    # file regardless of locale or dict-building order.
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def load_corpus(path: Path) -> list[Case]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"corpus schema {doc['schema_version']} != expected {SCHEMA_VERSION}")
    cases = []
    for c in doc["cases"]:
        cases.append(
            Case(
                case_id=c["case_id"],
                symbology=c["symbology"],
                category=c["category"],
                options=EncodeOptions(**c["options"]),
                payload_spec=c["payload_spec"],
                trials=tuple(c["trials"]),
                quick=c["quick"],
            )
        )
    return cases


def generate_run(seed: int | None, trials: int, symbology: str | None = None) -> Path:
    """Create a new run directory containing corpus.json + manifest.json."""
    if seed is None:
        seed = random.SystemRandom().randrange(2**32)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / f"{stamp}-seed{seed}"
    run_dir.mkdir(parents=True)

    cases = build_cases(seed, trials, symbology)
    (run_dir / "corpus.json").write_text(corpus_to_json(seed, trials, cases), encoding="utf-8")
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "trials_per_case": trials,
        "n_cases": len(cases),
        "symbology": symbology,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"run dir: {run_dir}")
    scope = f"  symbology: {symbology}" if symbology else ""
    print(f"seed: {seed}  cases: {len(cases)}  trials/case: {trials}{scope}")
    return run_dir


def cmd_gen(seed: int | None, trials: int, symbology: str | None = None) -> int:
    generate_run(seed, trials, symbology)
    return 0


def require_run_dir(run_dir: str) -> Path:
    """Resolve ``run_dir`` to an absolute path, erroring if it wasn't created
    by ``gen`` (no ``corpus.json``) - catches empty/typoed paths before a stage
    does confusing partial work. ``resolve()`` because podman bind mounts need
    absolute source paths."""
    path = Path(run_dir).resolve()
    if not (path / "corpus.json").is_file():
        raise SystemExit(f"not a run directory (no corpus.json): {run_dir!r}")
    return path
