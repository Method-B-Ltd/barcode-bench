"""Corpus invariants: determinism, stability, payload well-formedness."""

from __future__ import annotations

import pytest

from barcode_bench.corpus import SYMBOLOGIES, build_cases, corpus_to_json


def test_same_seed_is_byte_identical() -> None:
    a = corpus_to_json(1234, 5, build_cases(1234, 5))
    b = corpus_to_json(1234, 5, build_cases(1234, 5))
    assert a == b


def test_different_seed_differs() -> None:
    a = corpus_to_json(1, 5, build_cases(1, 5))
    b = corpus_to_json(2, 5, build_cases(2, 5))
    assert a != b


def test_trials_vary_within_case() -> None:
    # Different trial payloads per case is the anti-caching mechanism.
    for case in build_cases(7, 5):
        assert len(set(case.trials)) > 1, case.case_id


def test_case_ids_unique_and_stable_stream() -> None:
    cases = build_cases(7, 3)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    # per-case RNG streams: a case's payloads don't depend on the case list
    by_id = {c.case_id: c for c in cases}
    again = {c.case_id: c for c in build_cases(7, 3)}
    for cid, case in by_id.items():
        assert again[cid].trials == case.trials


def test_declared_encodings_are_clean() -> None:
    for case in build_cases(99, 5):
        enc = case.options.encoding
        for payload in case.trials:
            if enc is not None:
                payload.encode(enc)  # must not raise
            elif "-auto" not in case.case_id:
                # undeclared-charset cases are pure ASCII - except the auto
                # twins (and their `-auto-svg` mirrors), whose whole point is
                # non-ASCII content with no pin
                payload.encode("ascii")


def test_auto_twins_share_payloads_with_declared_sibling() -> None:
    # pinned vs auto must differ in exactly one variable: the declaration.
    by_id = {c.case_id: c for c in build_cases(7, 5)}
    autos = [c for c in by_id.values() if c.case_id.endswith("-auto")]
    assert autos, "auto twin cases missing from the matrix"
    for case in autos:
        sibling = by_id[case.case_id.removesuffix("-auto")]
        assert case.trials == sibling.trials, case.case_id
        assert case.options.encoding is None
        assert sibling.options.encoding is not None
        assert any(ord(ch) > 127 for t in case.trials for ch in t), case.case_id


def test_latin1_ambiguous_stays_in_the_collision_band() -> None:
    # The probe only works if its payloads carry Latin-1 symbols in 0xA0..0xBF
    # (which collide with Shift-JIS half-width katakana) and *no* accented
    # letters at 0xE0..0xFF (Shift-JIS lead bytes that let a guessing decoder
    # recover Latin-1). Guard both, or a future edit quietly defuses the case.
    for seed in (1, 7, 42, 99):
        for case in build_cases(seed, 5):
            if case.category != "latin1_ambiguous":
                continue
            for payload in case.trials:
                payload.encode("iso-8859-1")  # must not raise
                assert sum(0xA0 <= ord(ch) <= 0xBF for ch in payload) >= 3, payload
                assert not any(0xE0 <= ord(ch) <= 0xFF for ch in payload), payload


def test_svg_twins_share_payloads_with_png_sibling() -> None:
    # The SVG axis must differ from its PNG sibling in exactly one variable:
    # output_format. Same payloads, same everything else.
    by_id = {c.case_id: c for c in build_cases(7, 5)}
    svg = [c for c in by_id.values() if c.case_id.endswith("-svg")]
    assert svg, "svg twin cases missing from the matrix"
    for case in svg:
        assert case.options.output_format == "svg"
        sibling = by_id[case.case_id.removesuffix("-svg")]
        assert sibling.options.output_format == "png"
        assert case.trials == sibling.trials, case.case_id
        assert case.symbology == sibling.symbology
        assert case.options.ec_level == sibling.options.ec_level


def test_datamatrix_cases_force_square() -> None:
    # Suite policy: DM codeword comparisons are square-vs-square (see README).
    for case in build_cases(1, 2):
        if case.symbology == "datamatrix":
            assert case.options.dm_force_square, case.case_id


def test_quick_subset_covers_every_symbology() -> None:
    quick = [c for c in build_cases(1, 2) if c.quick]
    assert {c.symbology for c in quick} == {"qr", "datamatrix", "aztec", "pdf417"}


def test_symbology_filter_keeps_only_that_symbology() -> None:
    full = build_cases(1, 3)
    for sym in SYMBOLOGIES:
        filtered = build_cases(1, 3, sym)
        assert filtered, sym
        assert {c.symbology for c in filtered} == {sym}
        # every case the full matrix has for this symbology, and no others
        assert [c.case_id for c in filtered] == [c.case_id for c in full if c.symbology == sym]


def test_symbology_filter_leaves_payloads_byte_identical() -> None:
    # Filtering must not disturb a kept case's payloads (per-case RNG streams).
    full = {c.case_id: c.trials for c in build_cases(42, 5)}
    for case in build_cases(42, 5, "qr"):
        assert case.trials == full[case.case_id], case.case_id


def test_unknown_symbology_rejected() -> None:
    with pytest.raises(ValueError, match="unknown symbology"):
        build_cases(1, 2, "ean13")
