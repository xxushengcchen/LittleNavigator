import json
import pathlib
import unittest

from src.voicepack import VoicePack

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ContentEntryTests(unittest.TestCase):
    """文案侧：这条素材必须出现在文案文件里，否则合成流程根本不会跑。"""

    def _items(self):
        rows = []
        for path in sorted((ROOT / "content").glob("assistant-telemetry-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.extend(data.get("items") or [])
        return rows

    NEW_IDS = ("AST_AI_SEE_SCREEN", "AST_NO_CAR_CONTROL",
               "AST_CC_TYRE", "AST_CC_FUEL", "AST_CC_WS",
               "AST_CC_FR", "AST_CC_CLEAR", "AST_CC_FAIL",
               "AST_CC_COMPOUND",
               # 拒绝话术：engine._say_with_fallback 的后两级兜底。
               # 曾经只存在于代码常量里、content 与语音包都没有，导致缺素材时静默。
               "AST_REFUSE_CANT", "AST_REFUSE_HEARD")

    def test_msgids_exist_in_content(self):
        ids = [it.get("msgId") for it in self._items()]
        for msg_id in self.NEW_IDS:
            with self.subTest(msgId=msg_id):
                self.assertIn(msg_id, ids)

    def test_entries_have_variants_and_instruct(self):
        found = set()
        for item in self._items():
            if item.get("msgId") not in self.NEW_IDS:
                continue
            found.add(item["msgId"])
            self.assertGreaterEqual(len(item.get("variants") or []), 1)
            self.assertTrue(item.get("instruct"))
            for text in item["variants"]:
                self.assertNotIn("{", text)      # 不做模板替换，是固定话术
        self.assertEqual(found, set(self.NEW_IDS))


class RefusalConstantTests(unittest.TestCase):
    """代码里的拒绝类常量必须在 content 里有定义。

    engine._say_with_fallback 的后两级兜底用的就是 REFUSE_CANT / REFUSE_HEARD。
    这两个 msgId 曾经只写在代码里、content 与语音包都没有，结果素材缺失时
    「降级」降到了空处，用户按完 PTT 什么都听不到。测试用假语音包发现不了，
    所以这里直接比对代码常量与内容定义。
    """

    def test_refusal_constants_defined_in_content(self):
        from src import engine

        ids = {it.get("msgId") for it in ContentEntryTests()._items()}
        for name in ("REFUSE_CANT", "REFUSE_HEARD", "REFUSE_LATER",
                     "NO_CAR_CONTROL"):
            with self.subTest(constant=name):
                msg_id = getattr(engine, name, None)
                if msg_id is None:
                    continue
                self.assertIn(msg_id, ids,
                              f"{name}={msg_id} 在 content 里没有定义，"
                              f"合成后语音包里也不会有，降级会落到空处")


class PackAssetTests(unittest.TestCase):
    """运行时侧：素材合成进语音包后，必须能被 VoicePack 找到并播出来。

    未导入运行时素材时跳过——本机（源码 clone）通常没有 build/ 下的音频。
    """

    def test_msgid_exists_in_pack(self):
        pack_dir = ROOT / "build" / "assistant-telemetry"
        if not (pack_dir / "spmsg.txt").exists():
            self.skipTest("未导入运行时素材（需先跑 scripts/import_runtime_assets.py）")
        pack = VoicePack.load(pack_dir)
        self.assertTrue(pack.has("AST_AI_SEE_SCREEN"),
                        "文案已加但音频未合成：需重跑 synthesize_telemetry 再重建语音包")

    def test_pack_audio_is_readable(self):
        pack_dir = ROOT / "build" / "assistant-telemetry"
        if not (pack_dir / "spmsg.txt").exists():
            self.skipTest("未导入运行时素材")
        pack = VoicePack.load(pack_dir)
        if not pack.has("AST_AI_SEE_SCREEN"):
            self.skipTest("该素材尚未合成")
        variant = pack.variants["AST_AI_SEE_SCREEN"][0]
        self.assertTrue(pack.path_of(variant).is_file())


if __name__ == '__main__':
    unittest.main()
