"""scorer tests: file metrics (migration-aware), gate logic, composite."""

from harness import scorer

GOLD = [
    "zerver/models/realms.py",
    "zerver/actions/realm_settings.py",
    "zerver/lib/events.py",
    "zerver/views/realm.py",
    "zerver/migrations/0710_realm_topics_policy.py",
    "zerver/migrations/0711_set_default_value_for_realm_topics_policy.py",
]
# 4 distinct non-migration gold files + 1 migration slot = 5 slots.


def test_perfect_match_with_differently_named_migration():
    agent = [
        "zerver/models/realms.py",
        "zerver/actions/realm_settings.py",
        "zerver/lib/events.py",
        "zerver/views/realm.py",
        "zerver/migrations/0710_something_else.py",  # different name, still a migration
    ]
    m = scorer.score_files(agent, GOLD)
    assert m.completeness == 1.0
    assert m.precision == 1.0
    assert m.migration_satisfied


def test_missing_event_layer_lowers_completeness():
    agent = [
        "zerver/models/realms.py",
        "zerver/views/realm.py",
        "zerver/migrations/0710_x.py",
    ]
    m = scorer.score_files(agent, GOLD)
    # matched: realms.py, realm.py (2) + migration (1) = 3 of 5 slots
    assert m.completeness == 0.6
    assert "zerver/lib/events.py" in m.missed
    assert m.precision == 1.0  # everything it touched was correct


def test_extra_files_hurt_precision():
    agent = [
        "zerver/models/realms.py",
        "zerver/lib/streams.py",   # red herring, not in gold
        "zerver/migrations/0710_x.py",
    ]
    m = scorer.score_files(agent, GOLD)
    # agent slots: 2 non-mig + 1 mig = 3; correct: realms.py + migration = 2
    assert round(m.precision, 4) == round(2 / 3, 4)
    assert "zerver/lib/streams.py" in m.extra


def test_no_migration_when_expected():
    agent = ["zerver/models/realms.py"]
    m = scorer.score_files(agent, GOLD)
    assert m.migration_expected
    assert not m.migration_satisfied
    assert any("migration" in x for x in m.missed)


def test_correctness_hard_gate():
    assert scorer.score_correctness(4, 4, ran=True) == 1.0
    assert scorer.score_correctness(4, 3, ran=True) == 0.0   # partial -> 0
    assert scorer.score_correctness(4, 0, ran=True) == 0.0
    assert scorer.score_correctness(4, 4, ran=False) == -1.0  # unknown


def test_composite_unknown_tests_flagged():
    m = scorer.score_files(GOLD, GOLD)  # perfect files
    s = scorer.compute_scores(
        file_metrics=m, correctness=-1.0, regression=-1.0, tokens_consumed=500_000
    )
    assert s.completeness == 1.0
    assert s.correctness == -1.0
    assert any("UNKNOWN" in n for n in s.notes)
    # composite uses 0 for unknown correctness/regression but full file metrics.
    assert 0 < s.composite < 1


def test_composite_full_pass():
    m = scorer.score_files(GOLD, GOLD)
    s = scorer.compute_scores(
        file_metrics=m, correctness=1.0, regression=1.0, tokens_consumed=400_000
    )
    # correctness 1, completeness 1, precision 1, regression 1, plus efficiency.
    assert s.composite > 0.9
    assert s.efficiency > 0


def test_efficiency_zero_tokens():
    assert scorer.efficiency_norm(0.5, 0) == 0.0
