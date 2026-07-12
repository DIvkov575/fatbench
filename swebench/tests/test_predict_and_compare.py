"""Predictions JSONL schema + comparison math."""

import json

from swebench import compare as cmp
from swebench.predict import RunRecord, prediction_line, write_predictions, write_telemetry


def _rec(instance_id, arm, patch="diff --git a/x b/x\n", **kw):
    base = dict(
        instance_id=instance_id, arm=arm, model_patch=patch,
        empty_patch=not patch.strip(), agent_ok=True, timed_out=False,
        input_tokens=100, output_tokens=20, cache_creation_tokens=0,
        tokens_consumed=120, cost_usd=0.05, num_turns=5, duration_ms=3000, error=None,
    )
    base.update(kw)
    return RunRecord(**base)


def test_prediction_line_is_swebench_schema():
    line = prediction_line(_rec("astropy__astropy-1", "raw"))
    assert set(line.keys()) == {"instance_id", "model_name_or_path", "model_patch"}
    assert line["model_name_or_path"] == "raw"
    assert line["instance_id"] == "astropy__astropy-1"


def test_write_predictions_jsonl(tmp_path):
    recs = [_rec("a__b-1", "mine"), _rec("a__b-2", "mine", patch="")]
    p = write_predictions(recs, tmp_path / "predictions.mine.jsonl")
    lines = [json.loads(l) for l in p.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[1]["model_patch"] == ""      # empty patch still emitted (grader marks unresolved)
    assert all("model_name_or_path" in l for l in lines)


def test_write_telemetry_roundtrips(tmp_path):
    recs = [_rec("a__b-1", "raw", tokens_consumed=999, cost_usd=1.25)]
    p = write_telemetry(recs, tmp_path / "telemetry.raw.jsonl")
    row = json.loads(p.read_text().splitlines()[0])
    assert row["tokens_consumed"] == 999
    assert row["cost_usd"] == 1.25
    assert row["arm"] == "raw"


def test_summarize_arm_computes_rate_and_costs():
    tel = [
        {"instance_id": "i1", "tokens_consumed": 100, "cost_usd": 0.10, "num_turns": 4,
         "duration_ms": 2000, "empty_patch": False, "timed_out": False},
        {"instance_id": "i2", "tokens_consumed": 300, "cost_usd": 0.30, "num_turns": 6,
         "duration_ms": 4000, "empty_patch": True, "timed_out": False},
    ]
    s = cmp.summarize_arm("raw", resolved_ids=["i1"], submitted_ids=["i1", "i2"], telemetry=tel)
    assert s["n"] == 2
    assert s["resolved"] == 1
    assert s["resolved_rate"] == 0.5
    assert s["empty_patches"] == 1
    assert s["avg_tokens"] == 200.0
    assert s["total_tokens"] == 400
    assert s["avg_cost_usd"] == 0.2


def test_compare_head_to_head_deltas():
    tel_raw = [{"instance_id": f"i{n}", "tokens_consumed": 100, "cost_usd": 0.10,
                "num_turns": 4, "duration_ms": 2000, "empty_patch": False, "timed_out": False}
               for n in range(3)]
    tel_mine = [{"instance_id": f"i{n}", "tokens_consumed": 200, "cost_usd": 0.20,
                 "num_turns": 8, "duration_ms": 5000, "empty_patch": False, "timed_out": False}
                for n in range(3)]
    raw = cmp.summarize_arm("raw", ["i0"], ["i0", "i1", "i2"], tel_raw)
    mine = cmp.summarize_arm("mine", ["i0", "i1"], ["i0", "i1", "i2"], tel_mine)
    comp = cmp.compare([raw, mine])
    h = comp["head_to_head"]
    assert h["resolved_rate_delta"] == round(2 / 3 - 1 / 3, 4)
    assert h["only_mine_resolved"] == ["i1"]
    assert h["only_raw_resolved"] == []
    assert h["both_resolved"] == ["i0"]
    assert h["token_ratio_mine_over_raw"] == 2.0
    assert h["cost_ratio_mine_over_raw"] == 2.0


def test_render_table_has_both_arms():
    tel = [{"instance_id": "i0", "tokens_consumed": 100, "cost_usd": 0.1, "num_turns": 4,
            "duration_ms": 2000, "empty_patch": False, "timed_out": False}]
    comp = cmp.compare([
        cmp.summarize_arm("raw", [], ["i0"], tel),
        cmp.summarize_arm("mine", ["i0"], ["i0"], tel),
    ])
    table = cmp.render_table(comp)
    assert "raw" in table and "mine" in table
    assert "resolved_rate" in table
    assert "delta" in table
