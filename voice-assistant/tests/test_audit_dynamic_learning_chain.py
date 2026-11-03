"""A05: exercise real LLM parsing, pipeline, history, feedback and learning store."""
import collections
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.server import Service
from src.ai_learn import LearnStore
from src.ai_pipeline import AiPipeline
from src.engine import Assistant
from src.query_class import QueryKind
from src.telemetry import Snapshot


TEMPLATE = {
    "params": ["laps_of_fuel"],
    "rules": [{"when": "true", "say": "油够跑{laps_of_fuel}圈"}],
}


def _service(tmp_path):
    config = {
        "intent": {"miss_log": {"enabled": True}},
        "ai": {
            "enabled": True,
            "mode": "all",
            "global_stop_local": True,
            "base_url": "https://model.invalid",
            "api_key": "audit-fake-key",
            "model": "audit-fake-model",
            "tts": {"adapter": "off"},
        },
    }
    snapshot = Snapshot(
        connected=True, t=time.time(), laps_of_fuel=6.2, laps_remain=5,
    )
    assistant = Assistant.__new__(Assistant)
    assistant.cfg = config
    assistant.out = io.StringIO()
    assistant.live = {}
    assistant.history = collections.deque()
    assistant.derive = SimpleNamespace(lap_records=[])
    assistant.snapshot = lambda: snapshot
    assistant._play_ack = lambda: None
    assistant.pack = SimpleNamespace(has=lambda _msg: False)
    assistant.qa_log = tmp_path / "qa.jsonl"
    assistant.miss_log = tmp_path / "miss.jsonl"
    assistant.ai = AiPipeline(
        config, store=LearnStore(tmp_path / "learn"), out=assistant.out,
        snapshot_provider=assistant.snapshot, on_live=assistant._on_ai_answer,
    )
    service = Service.__new__(Service)
    service.asst = assistant
    return service, snapshot


def _ask(service, payload, question="油还够吗"):
    # Only replace the wire boundary: LlmClient.ask/json parsing and the complete
    # Assistant -> pipeline -> history -> Service feedback chain remain real.
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    with patch("src.llm_engine._post_json", return_value=response) as wire:
        record = service.asst.ask_ai(
            question, QueryKind.ANALYSIS, None, False, source="audit",
        )
    return record, wire.call_count


def _review(service, record):
    return service.ai_review_mark(record["review_id"], "ok")["stored_as"]


def test_dynamic_without_template_is_not_saved_as_static_and_fuel_is_recomputed(tmp_path):
    service, snapshot = _service(tmp_path)
    record, calls = _ask(service, {
        "answer": "油够跑6.2圈", "data_used": ["laps_of_fuel"],
        "dynamic": True, "template": None,
    })
    assert record["ok"] is True
    assert calls == 1
    assert _review(service, record) == "rejected"
    assert record["ai_dynamic"] is True
    assert service.asst.learn_store().stats() == {"templates": [], "knowledge": []}

    snapshot.laps_of_fuel = 1.1
    newer, calls = _ask(service, {
        "answer": "油够跑1.1圈", "data_used": ["laps_of_fuel"],
        "dynamic": True, "template": None,
    })
    assert calls == 1
    assert newer["ai_source"] == "model"
    assert newer["ai_text"] == "油够跑1.1圈"


@pytest.mark.parametrize("metadata", [{}, {"dynamic": None}], ids=["missing", "null"])
@pytest.mark.parametrize("template", [None, TEMPLATE], ids=["no-template", "template"])
def test_unknown_dynamic_metadata_cannot_become_static_or_cache_a_template(
    tmp_path, metadata, template,
):
    service, _ = _service(tmp_path)
    record, _ = _ask(service, {
        "answer": "油够跑6.2圈", "data_used": ["laps_of_fuel"],
        "template": template, **metadata,
    })
    assert record["ok"] is True  # A validated current answer remains displayable.
    assert _review(service, record) == "rejected"
    assert record["ai_dynamic"] is True  # Conservative through legacy bool callers.
    assert record["ai_template"] is None
    assert service.asst.learn_store().stats() == {"templates": [], "knowledge": []}


@pytest.mark.parametrize("dynamic", ["false", "true", 0, 1, [], {}])
def test_invalid_dynamic_metadata_fails_safely_without_learning(tmp_path, dynamic):
    service, _ = _service(tmp_path)
    record, _ = _ask(service, {
        "answer": "油够跑6.2圈", "data_used": ["laps_of_fuel"],
        "dynamic": dynamic, "template": None,
    })
    assert record["ok"] is False
    assert "dynamic" in record["reason"]
    assert _review(service, record) == "rejected"
    assert service.asst.learn_store().stats() == {"templates": [], "knowledge": []}


def test_explicit_static_answer_still_becomes_knowledge(tmp_path):
    service, _ = _service(tmp_path)
    record, _ = _ask(service, {
        "answer": "后方有快车", "data_used": [],
        "dynamic": False, "template": None,
    }, question="蓝旗什么意思")
    assert record["ok"] is True
    assert record["ai_dynamic"] is False
    assert _review(service, record) == "knowledge"
    rows = service.asst.learn_store().stats()
    assert rows["templates"] == []
    assert [row["a"] for row in rows["knowledge"]] == ["后方有快车"]


def test_dynamic_template_remains_dynamic_when_learned_answer_is_reviewed_again(tmp_path):
    service, snapshot = _service(tmp_path)
    record, _ = _ask(service, {
        "answer": "油够跑6.2圈", "data_used": ["laps_of_fuel"],
        "dynamic": True, "template": TEMPLATE,
    })
    assert record["ok"] is True
    assert record["ai_dynamic"] is True
    assert _review(service, record) == "template"

    snapshot.laps_of_fuel = 1.1
    learned, calls = _ask(service, {"answer": "must not reach the model"})
    assert calls == 0
    assert learned["ok"] is True
    assert learned["ai_source"] == "learn"
    assert learned["ai_text"] == "油够跑1.1圈"
    assert _review(service, learned) == "template"
    assert learned["ai_dynamic"] is True
    assert learned["ai_template"] == TEMPLATE
    assert service.asst.learn_store().stats()["knowledge"] == []
