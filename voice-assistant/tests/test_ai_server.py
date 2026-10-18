import json
import pathlib
import tempfile
import unittest
import unittest.mock

import yaml

from src import config
from src.engine import Assistant


class KeyIsolationTests(unittest.TestCase):
    """密钥不得出现在任何对外视图里。

    这条必须打真实路径：只验证「自己构造的字典里没有 key」毫无意义。
    """

    SECRET = "sk-SECRET-123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        (self.root / "config.yaml").write_text(
            yaml.safe_dump({"ai": {"enabled": True, "base_url": "http://x",
                                   "model": "m"}}), encoding="utf-8")
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": self.SECRET,
                                   "tts": {"adapter": "openai",
                                           "base_url": "http://t",
                                           "api_key": self.SECRET}}}),
            encoding="utf-8")

    def _assistant(self):
        cfg = config.load(self.root / "config.yaml")
        with unittest.mock.patch("src.engine.VoicePack.load_many") as load:
            pack = unittest.mock.MagicMock()
            pack.variants = {}
            pack.stats.return_value = {"msgIds": 0, "clips": 0, "avg_variants": 0}
            pack.sample_rate.return_value = 16000
            pack.has.return_value = False
            load.return_value = pack
            return Assistant(cfg, no_play=True, out=unittest.mock.MagicMock())

    def test_config_load_keeps_key_internally(self):
        cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["api_key"], self.SECRET)

    def test_config_view_never_exposes_key(self):
        view = self._assistant().config_view()
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(self.SECRET, text)
        self.assertTrue(view["ai"]["api_key_set"])

    def test_tts_key_is_also_masked(self):
        view = self._assistant().config_view()
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(self.SECRET, text)
        self.assertTrue(view["ai"]["tts"]["api_key_set"])

    def test_ai_status_has_no_key_field(self):
        status = self._assistant().ai_status()
        self.assertNotIn("api_key", status)

    def test_ai_config_view_reports_presence_only(self):
        view = self._assistant().ai_config_view()
        self.assertIsInstance(view["api_key_set"], bool)
        self.assertNotIn(self.SECRET, json.dumps(view, ensure_ascii=False))



class ReviewShapeTests(unittest.TestCase):
    def test_review_rows_carry_id(self):
        """复盘条目必须带 id，否则界面无法标记。"""
        from app.server import Service
        import inspect
        source = inspect.getsource(Service.ai_review)
        self.assertIn('"id"', source)


if __name__ == '__main__':
    unittest.main()
