"""A01–A04: command rejection must precede real car apply/broadcast.

Mutation targets: substring narration matches, sliced numeric tokens, ignored
axle/side scope, and early cancellation returns before multi-service checks.
Only voice-pack IO and the external SDK are replaced; parser/engine/controller
remain real, and apply is wrapped (not stubbed) to catch late validation.
"""
import io
from unittest.mock import patch

import pytest

from src.car_control import CarController
from src.command_match import match, parse_litres
from src.engine import Assistant
from src.telemetry import Snapshot
from tests.test_engine_car import FakeSdk, fake_pack, make_cfg


@pytest.fixture
def car_engine(tmp_path):
    cfg = make_cfg(tmp_path, cc_enabled=True, verify=False)
    sdk = FakeSdk()
    # Prevent even constructing a real SDK; no device/backend is opened.
    with patch.dict("sys.modules", {"irsdk": None}), patch(
        "src.engine.VoicePack.load_many", return_value=fake_pack()
    ):
        assistant = Assistant(cfg, no_play=True, out=io.StringIO())
    assistant.car = CarController(cfg, sdk=sdk, out=io.StringIO())
    assistant.snapshot = lambda: Snapshot(
        connected=True, fuel_level=20.0, fuel_pct=0.5,
        pit_sv_flags=0xFF, pit_sv_fuel=10.0,
    )
    with patch.object(assistant.car, "apply", wraps=assistant.car.apply) as apply:
        yield assistant, sdk, apply
    assistant.close()


def assert_rejected(car_engine, text):
    assistant, sdk, apply = car_engine
    result = assistant.ask(text, source="voice")
    assert apply.call_count == 0, result
    assert sdk.calls == [], result
    assert result["ok"] is False
    assert result["cc_action"]["kind"] == "unsupported"
    assert result["cc_action"]["reason"]


@pytest.mark.parametrize("text", [
    "先不换左前胎", "我没说换胎", "上一圈加了10升", "上一圈加了10升油",
    "我说过换左前胎", "刚才换了左前胎", "他说换胎", "我没有要求取消换胎",
    "不要换左前胎", "先别换右后", "换左前胎算了",
    "我没说换四个", "先不换全部", "换胎没说算了", "加油没说算了",
    "快修没说算了", "撕膜没说算了",
])
def test_a01_negation_never_reaches_car_apply(car_engine, text):
    assert_rejected(car_engine, text)


@pytest.mark.parametrize("text", [
    "换胎不要换左前", "换胎不要换左前胎", "换左前不要换左前",
    "换左前胎不要换左前胎", "换左前和右后不要换左前不要换右后",
])
def test_a01_exclusions_without_remaining_positive_corners_never_apply(car_engine, text):
    # A rejected/empty explicit target must not fall through to all tyres or an axle.
    assert_rejected(car_engine, text)


@pytest.mark.parametrize("text", [
    "加负十升油", "加−10升", "加-10升油", "加油1e2升", "加1E2升",
    "加10到20升油", "加10-20升", "加十至二十升", "加10~20升",
    "加1.5升油", "加零点五升油", "加10.0升", "加1。5升", "加一点五升",
    "加10升20升", "加10升和20升", "加10加仑油", "加油10gal",
    "加油10", "加121升", "加零升", "加0升", "加一二升", "加十十升",
    "加1 0升", "加一 二升", "加1e+2升", "加１．５升油",
])
def test_a02_whole_quantity_token(car_engine, text):
    assert_rejected(car_engine, text)


@pytest.mark.parametrize("text", [
    "加负十升油", "加−10升", "加油1e2升", "加10到20升油",
    "加10升20升", "加10加仑油", "加一二升", "加十十升",
])
def test_a02_quantity_helper_never_returns_a_numeric_fragment(text):
    assert parse_litres(text) is None


@pytest.mark.parametrize("text", [
    "换前轮和左后胎", "换左侧和右后胎", "换后胎和左前", "换右边轮胎和左前胎",
])
def test_a03_mixed_axle_corner_is_rejected(car_engine, text):
    assert_rejected(car_engine, text)


@pytest.mark.parametrize("text", [
    "取消换胎并加油10升", "换胎不要加油", "换左前加满", "换右后灌满",
    "不要换胎并勾上快修", "取消加油并撕膜", "清空进站再加10升油",
])
def test_a04_multi_service_is_rejected_before_any_send(car_engine, text):
    assert_rejected(car_engine, text)


@pytest.mark.parametrize("text,action,calls", [
    ("换左前", {"kind": "tyre", "corners": ["LF"]}, [(7, 0), (3, 0)]),
    ("不要换胎", {"kind": "clear_tires"}, [(7, 0)]),
    ("帮我取消换胎", {"kind": "clear_tires"}, [(7, 0)]),
    ("换胎不用了", {"kind": "clear_tires"}, [(7, 0)]),
    ("勾上快修", {"kind": "fast_repair"}, [(8, 0)]),
    ("换左前和右后胎", {"kind": "tyre", "corners": ["LF", "RR"]}, [(7, 0), (3, 0), (6, 0)]),
    ("换左前、右后", {"kind": "tyre", "corners": ["LF", "RR"]}, [(7, 0), (3, 0), (6, 0)]),
    ("换左前右后胎", {"kind": "tyre", "corners": ["LF", "RR"]}, [(7, 0), (3, 0), (6, 0)]),
    ("换左前胎，不要换右前胎", {"kind": "tyre", "corners": ["LF"]}, [(7, 0), (3, 0)]),
    ("只换前胎", {"kind": "tyre", "corners": ["LF", "RF"]}, [(7, 0), (3, 0), (4, 0)]),
    ("把四个轮子都换了", {"kind": "tyre", "corners": ["LF", "RF", "LR", "RR"]}, [(3, 0), (4, 0), (5, 0), (6, 0)]),
    ("我马上要进站帮我换左侧轮胎", {"kind": "tyre", "corners": ["LF", "LR"]}, [(7, 0), (3, 0), (5, 0)]),
    ("加10升", {"kind": "fuel", "litres": 10.0}, [(2, 10)]),
    ("加十升油", {"kind": "fuel", "litres": 10.0}, [(2, 10)]),
    ("加120升", {"kind": "fuel", "litres": 120.0}, [(2, 120)]),
    ("加一百二十升", {"kind": "fuel", "litres": 120.0}, [(2, 120)]),
    ("我要进站了帮我加10升油", {"kind": "fuel", "litres": 10.0}, [(2, 10)]),
    ("加油", {"kind": "fuel", "litres": None}, [(2, 0)]),
    ("加满", {"kind": "fuel", "full": True, "litres": None}, [(2, 20)]),
    ("清空进站", {"kind": "clear"}, [(0, 0)]),
    ("全部取消", {"kind": "clear"}, [(0, 0)]),
    ("进站设置全清", {"kind": "clear"}, [(0, 0)]),
    ("帮我进站清空", {"kind": "clear"}, [(0, 0)]),
    ("换左轮", {"kind": "tyre", "corners": ["LF", "LR"]}, [(7, 0), (3, 0), (5, 0)]),
    ("换右轮", {"kind": "tyre", "corners": ["RF", "RR"]}, [(7, 0), (4, 0), (6, 0)]),
    ("加 10 升", {"kind": "fuel", "litres": 10.0}, [(2, 10)]),
])
def test_supported_single_commands_preserve_exact_actions(car_engine, text, action, calls):
    assert match(text) == action
    assistant, sdk, apply = car_engine
    result = assistant.ask(text, source="voice")
    assert result["ok"] is True, result
    assert apply.call_count == 1
    assert sdk.calls == calls


@pytest.mark.parametrize("text,corners,calls", [
    ("把左前胎都换了", ["LF"], [(7, 0), (3, 0)]),
    # Review R2 regression: 都换 is grammar, not an all-tyre range noun.
    # Before the fix these silently became all four tyres (real-chain probe:
    # sdk [(3,0),(4,0),(5,0),(6,0)]), which is a silent scope expansion.
    ("把前胎都换了", ["LF", "RF"], [(7, 0), (3, 0), (4, 0)]),
    ("把后胎都换了", ["LR", "RR"], [(7, 0), (5, 0), (6, 0)]),
    ("把左边都换了", ["LF", "LR"], [(7, 0), (3, 0), (5, 0)]),
    ("把左轮都换了", ["LF", "LR"], [(7, 0), (3, 0), (5, 0)]),
    ("把前面两个都换了", ["LF", "RF"], [(7, 0), (3, 0), (4, 0)]),
    ("换左前前右胎", ["LF", "RF"], [(7, 0), (3, 0), (4, 0)]),
    ("换左后後右胎", ["LR", "RR"], [(7, 0), (5, 0), (6, 0)]),
])
def test_review_explicit_corner_boundaries_preserve_exact_sdk_targets(car_engine, text, corners, calls):
    # Grammatical 都换 is not an all-tyre noun; adjacent aliases retain boundaries.
    assistant, sdk, apply = car_engine
    assert match(text) == {"kind": "tyre", "corners": corners}
    result = assistant.ask(text, source="voice")
    assert result["ok"] is True, result
    assert apply.call_count == 1
    assert sdk.calls == calls
