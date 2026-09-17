"""
Unit tests for command_label_normalizer.py — no hardware, no ML runtime.

Run with:  python -m pytest test_command_label_normalizer.py -v
      or:  python test_command_label_normalizer.py
"""

from command_label_normalizer import (
    EXCLUDED_LABELS,
    ORIENTATION_CANONICAL,
    _strip_known_prefix,
    normalize_command_label,
)


def test_none_label_passes_through_as_none():
    label, conf = normalize_command_label(None, 0.0)
    assert label is None
    assert conf == 0.0


def test_unaffected_label_passes_through_unchanged():
    for raw in ["ok", "like", "call", "one", "four", "three2", "fist", "palm"]:
        label, conf = normalize_command_label(raw, 0.88)
        assert label == raw
        assert conf == 0.88


def test_confidence_never_modified():
    # Confirms normalization is label-space only, regardless of outcome.
    _, c1 = normalize_command_label("ok", 0.42)
    _, c2 = normalize_command_label("three", 0.99)
    _, c3 = normalize_command_label("stop_inverted", 0.71)
    assert c1 == 0.42
    assert c2 == 0.99  # preserved even though label is dropped
    assert c3 == 0.71


# Exclusion — "three" as noise


def test_three_is_excluded_as_noise():
    label, conf = normalize_command_label("three", 0.95)
    assert label is None
    assert conf == 0.95  # confidence preserved for logging even though dropped


def test_three2_is_not_excluded():
    # three2 was NOT implicated in the sink pattern — must survive.
    label, conf = normalize_command_label("three2", 0.90)
    assert label == "three2"


def test_stock_none_class_maps_to_none_label():
    # The model's own literal "none" class (no confident gesture) is
    # semantically the same as no detection.
    label, conf = normalize_command_label("none", 0.60)
    assert label is None


# Orientation collapsing


def test_stop_inverted_collapses_to_stop():
    label, _ = normalize_command_label("stop_inverted", 0.80)
    assert label == "stop"


def test_peace_inverted_collapses_to_peace():
    label, _ = normalize_command_label("peace_inverted", 0.80)
    assert label == "peace"


def test_two_up_inverted_collapses_to_two_up():
    label, _ = normalize_command_label("two_up_inverted", 0.80)
    assert label == "two_up"


def test_canonical_forms_pass_through_unchanged():
    for canonical in ["stop", "peace", "two_up"]:
        label, _ = normalize_command_label(canonical, 0.80)
        assert label == canonical


def test_collapsed_pair_produces_identical_downstream_label():
    seq = ["stop", "stop_inverted", "stop", "stop_inverted", "stop"]
    normalized = [normalize_command_label(l, 0.9)[0] for l in seq]
    assert normalized == ["stop"] * 5  # streak-safe: all one label now


# Consistency checks on the configuration itself


def test_no_overlap_between_excluded_and_orientation_map():
    assert EXCLUDED_LABELS.isdisjoint(ORIENTATION_CANONICAL.keys())


def test_canonical_values_are_not_themselves_excluded():
    for canonical in ORIENTATION_CANONICAL.values():
        assert canonical not in EXCLUDED_LABELS


# train_val_ prefix stripping — regression tests for the bug found in
# live logs.


def test_strip_known_prefix_removes_train_val():
    assert _strip_known_prefix("train_val_fist") == "fist"
    assert _strip_known_prefix("train_val_stop_inverted") == "stop_inverted"


def test_strip_known_prefix_is_noop_without_prefix():
    assert _strip_known_prefix("fist") == "fist"
    assert _strip_known_prefix("ok") == "ok"


def test_prefixed_plain_label_passes_through_stripped():
    label, conf = normalize_command_label("train_val_ok", 0.9)
    assert label == "ok"
    assert conf == 0.9


def test_prefixed_three_is_excluded_as_noise():
    label, conf = normalize_command_label("train_val_three", 0.95)
    assert label is None
    assert conf == 0.95  # confidence still preserved for logging


def test_prefixed_orientation_variant_collapses_correctly():
    label, _ = normalize_command_label("train_val_stop_inverted", 0.8)
    assert label == "stop"
    label, _ = normalize_command_label("train_val_peace_inverted", 0.8)
    assert label == "peace"
    label, _ = normalize_command_label("train_val_two_up_inverted", 0.8)
    assert label == "two_up"


def test_prefixed_none_class_maps_to_none_label():
    label, _ = normalize_command_label("train_val_none", 0.6)
    assert label is None


def test_prefixed_and_bare_fist_produce_identical_output():
    # This is what makes the pre-latch guard in infer_live_v2.py
    # ("fist") correctly match this model's real output ("train_val_fist")
    # once normalization is applied — the orchestrator only ever sees
    # post-normalization labels.
    a, _ = normalize_command_label("train_val_fist", 0.9)
    b, _ = normalize_command_label("fist", 0.9)
    assert a == b == "fist"


if __name__ == "__main__":
    import sys
    import traceback

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
