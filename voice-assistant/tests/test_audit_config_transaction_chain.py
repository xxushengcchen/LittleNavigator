"""S07 regression: saving an override must preserve the base YAML layer.

The real configuration/Service methods run; only runtime audio and listener
objects are substitutes. No SDK, microphone, external API or user data is used.
"""
from __future__ import annotations

import copy
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from app.server import Service
from src import config


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv("LITTLE_NAVIGATOR_AI_KEY", raising=False)
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump({
        "ai": {"base_url": "https://saved.example/v1", "api_key": "synthetic-source-key",
               "model": "saved-model", "mode": "analysis_key", "enabled": False},
        "asr": {"num_threads": 3},
        "playback": {"device": "named-output", "volume": 0.4},
    }), encoding="utf-8")
    svc = Service.__new__(Service)
    svc.cfg_path = str(source)
    svc.cfg = config.load(source)
    svc.asr = None
    svc.listener = None
    svc.listener_lock = threading.RLock()
    svc.asst = SimpleNamespace(
        cfg=svc.cfg, picker=SimpleNamespace(cooldown=svc.cfg["voice_pack"]["cooldown"]),
        _operation_lock=threading.RLock(), apply_live=Mock(),
        reload_switchables=Mock(return_value={}),
    )
    svc.stop_listener = Mock(return_value={"running": False})
    svc.start_listener = Mock(return_value={"running": True})
    return svc


@pytest.mark.parametrize("section,key,expected", [
    ("ai", "base_url", "https://saved.example/v1"),
    ("ai", "api_key", "synthetic-source-key"),
    ("ai", "model", "saved-model"),
    ("ai", "mode", "analysis_key"),
    ("asr", "num_threads", 3),
    ("playback", "device", "named-output"),
])
def test_saving_volume_preserves_unrelated_base_values(service, section, key, expected):
    source = Path(service.cfg_path)
    original_source = source.read_bytes()
    result = service.configure({"playback": {"volume": 0.7}})
    assert result["ok"] is True
    assert service.cfg[section][key] == expected
    assert source.read_bytes() == original_source
    reloaded = config.load(source)
    assert service.cfg[section][key] == reloaded[section][key]
    assert service.cfg["playback"]["volume"] == 0.7
    assert yaml.safe_load(Path(service.cfg["_local_path"]).read_text(encoding="utf-8")) == {
        "playback": {"volume": 0.7},
    }


def test_consecutive_saves_keep_base_and_previous_override(service):
    service.configure({"playback": {"volume": 0.7}})
    service.configure({"playback": {"enabled": False}})
    assert service.cfg["playback"]["volume"] == 0.7
    assert service.cfg["playback"]["device"] == "named-output"
    assert service.cfg["ai"]["model"] == "saved-model"


def test_failed_replace_restores_previously_running_listener(service, monkeypatch):
    listener = SimpleNamespace(running=True)
    service.listener = listener

    def stop():
        listener.running = False
        return {"running": False}

    def start():
        listener.running = True
        return {"running": True}

    service.stop_listener = Mock(side_effect=stop)
    service.start_listener = Mock(side_effect=start)
    before_cfg = copy.deepcopy(service.cfg)
    local = Path(service.cfg["_local_path"])
    before_disk = local.read_bytes() if local.exists() else None

    def fail_replace(self, target):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        service.configure({"ptt": {"poll_ms": 17}})
    assert service.cfg == before_cfg
    assert service.asst.cfg == before_cfg
    assert (local.read_bytes() if local.exists() else None) == before_disk
    assert listener.running is True
    service.start_listener.assert_called_once()
    assert list(local.parent.glob(local.name + ".*.tmp")) == []
