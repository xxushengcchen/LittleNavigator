# -*- coding: utf-8 -*-
"""v1.6.2 构建/工具链审计剩余项回归（Q01/Q02/Q07-Q16/Q20/Q21/Q23）。

原则：全部用临时目录/fixture 真跑子进程，不联网、不装依赖、不构建安装包。
需要 ffmpeg（imageio_ffmpeg）的用例在缺依赖时 skip，CI 也能跑。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]      # 仓库根目录
SCRIPTS = ROOT / "scripts"
TOOLS = ROOT / "voice-assistant" / "tools"
PKG = ROOT / "voice-assistant" / "packaging"
PY = sys.executable


def run(cmd, cwd, env=None, timeout=180):
    full_env = os.environ.copy()
    full_env.pop("DOUBAO_API_KEY", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), env=full_env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def git_fixture(base: pathlib.Path, files: dict[str, bytes]) -> None:
    (base / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS / "secret_scan.py", base / "scripts" / "secret_scan.py")
    for name, data in files.items():
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    run(["git", "init", "-q"], base)
    run(["git", "config", "user.email", "fixture@example.invalid"], base)
    run(["git", "config", "user.name", "fixture"], base)
    run(["git", "add", "-A"], base)
    run(["git", "commit", "-qm", "fixture"], base)


def script_cwd(fixture: pathlib.Path, *names: str) -> None:
    (fixture / "scripts").mkdir(exist_ok=True)
    for name in names:
        shutil.copy2(SCRIPTS / name, fixture / "scripts" / name)


FAKE_KEY = "sk-" + "A1b2C3d4" * 4            # 40 位，强规则会命中
PLACEHOLDER_KEY = "sk-test-SECRET-1234567890"  # 仓库测试夹具占位值


class Q01SecretScanTests(unittest.TestCase):
    def test_placeholder_fixture_is_allowlisted_but_real_key_still_caught(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            git_fixture(base, {"voice-assistant/tests/test_ai_models.py":
                               f'KEY = "{PLACEHOLDER_KEY}"\n'.encode()})
            r = run([PY, "scripts/secret_scan.py", "--all"], base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("findings: 0", r.stdout)

            (base / "leak.py").write_text(f'API_KEY = "{FAKE_KEY}"\n', encoding="utf-8")
            run(["git", "add", "-A"], base)
            run(["git", "commit", "-qm", "leak"], base)
            r = run([PY, "scripts/secret_scan.py", "--all"], base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("sk-A", r.stdout)  # 脱敏后只露前 4 位

    def test_gitleaks_allowlist_has_exact_fixture_not_broad_sk_test(self):
        import tomllib
        cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
        patterns = [p for entry in cfg.get("allowlists", []) for p in entry.get("regexes", [])]
        self.assertIn(PLACEHOLDER_KEY, patterns)
        # 不允许用宽泛的通配前缀掩盖真实 Key
        for p in patterns:
            self.assertNotIn(".*", p)
            self.assertNotIn("*", p)


class Q02StagedScanTests(unittest.TestCase):
    def test_staged_scan_handles_nonascii_and_space_paths(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            git_fixture(base, {})
            (base / "中文 说明.txt").write_text(f'key: "{FAKE_KEY}"\n', encoding="utf-8")
            run(["git", "add", "-A"], base)
            r = run([PY, "scripts/secret_scan.py", "--staged"], base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("findings: 1", r.stdout)
            self.assertIn("中文 说明.txt", r.stdout)


class Q07CacheTests(unittest.TestCase):
    def test_synth_cache_key_covers_text_voice_and_params(self):
        with tempfile.TemporaryDirectory() as td:
            tool = pathlib.Path(td)
            script_cwd(tool, "synthesize_telemetry.py", "tts_doubao.py", "content_loader.py")
            code = f"""
import sys, json, pathlib
sys.path.insert(0, r"{tool/'scripts'}")
import synthesize_telemetry as m
args = dict(speaker='zh_female_xiaohe_uranus_bigtts', resource_id='seed-tts-2.0',
            instruct='normal', speech_rate=None)
fp1 = m.cache_fingerprint('黄旗', **args)
fp2 = m.cache_fingerprint('绿旗', **args)
assert fp1 != fp2, 'text must change fingerprint'
args2 = dict(args); args2['speaker'] = 'other'
assert m.cache_fingerprint('黄旗', **args2) != fp1, 'speaker must change fingerprint'
mp3 = pathlib.Path(r"{tool}") / 'a.mp3'
mp3.write_bytes(b'x' * 200)
assert m.cache_is_current(mp3, fp1) is False      # 没有 sidecar 的旧缓存必须重合成
m.remember_cache(mp3, fp1)
assert m.cache_is_current(mp3, fp1) is True
assert m.cache_is_current(mp3, fp2) is False      # 文案改了必须重合成
print('OK')
"""
            r = run([PY, "-c", code], tool)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)

    def test_build_pack_reconverts_when_mp3_newer_than_wav(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            script_cwd(base, "build_pack.py", "content_loader.py")
            code = f"""
import sys, pathlib, tempfile
sys.path.insert(0, r"{base/'scripts'}")
import build_pack as m
d = pathlib.Path(r"{base}")
src = d/'a.mp3'; dst = d/'a.wav'
src.write_bytes(b'1'); dst.write_bytes(b'2')
assert m.needs_convert(src, dst, force=False) is False
now = __import__('time').time()
__import__('os').utime(src, (now + 5, now + 5))
assert m.needs_convert(src, dst, force=False) is True, '更新的 mp3 必须重转'
assert m.needs_convert(src, dst, force=True) is True
print('OK')
"""
            r = run([PY, "-c", code], base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_synthesize_cli_exposes_force(self):
        r = run([PY, str(SCRIPTS / "synthesize_telemetry.py"), "--help"], ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--force", r.stdout)


class Q08PackFailClosedTests(unittest.TestCase):
    def _pack_fixture(self, td):
        base = pathlib.Path(td)
        script_cwd(base, "build_pack.py", "content_loader.py")
        (base / "content").mkdir()
        (base / "audio").mkdir()
        return base

    def test_missing_content_file_errors(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._pack_fixture(td)
            r = run([PY, "scripts/build_pack.py", "--content", "content/nope.json",
                     "--audio", "audio", "--out", "out", "--no-null"], base)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nope.json", r.stdout + r.stderr)

    def test_empty_items_errors(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._pack_fixture(td)
            (base / "content" / "empty.json").write_text(
                json.dumps({"items": []}), encoding="utf-8")
            r = run([PY, "scripts/build_pack.py", "--content", "content/empty.json",
                     "--audio", "audio", "--out", "out", "--no-null"], base)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_audio_errors_instead_of_warning(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._pack_fixture(td)
            (base / "content" / "c.json").write_text(json.dumps(
                {"items": [{"msgId": "SPCC_A", "text": "测试"}]},
                ensure_ascii=False), encoding="utf-8")
            r = run([PY, "scripts/build_pack.py", "--content", "content/c.json",
                     "--audio", "audio", "--out", "out", "--no-null"], base)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("找不到", r.stdout + r.stderr)


class Q09TextUnreachableTests(unittest.TestCase):
    def test_text_mode_wins_and_dry_run_does_not_touch_content(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            script_cwd(base, "tts_doubao.py", "content_loader.py")
            (base / "content").mkdir()
            (base / "content" / "messages.json").write_text(
                json.dumps({"items": [{"msgId": "SPCC_X", "text": "主文案"}]},
                           ensure_ascii=False), encoding="utf-8")
            r = run([PY, "scripts/tts_doubao.py", "--text", "单句测试",
                     "--dry-run", "--out", "out"], base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("single", r.stdout)
            self.assertNotIn("載入", r.stdout)
            self.assertNotIn("SPCC_X", r.stdout)

    def test_no_input_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            script_cwd(base, "tts_doubao.py", "content_loader.py")
            r = run([PY, "scripts/tts_doubao.py", "--dry-run", "--out", "out"], base)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("imageio_ffmpeg") is not None,
    "需要 imageio_ffmpeg 才能真跑转换失败路径")
class Q10WavFailureTests(unittest.TestCase):
    def test_existing_mp3_missing_wav_retries_and_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            script_cwd(base, "tts_doubao.py", "content_loader.py")
            out = base / "out"
            out.mkdir()
            (out / "single.mp3").write_bytes(b"not an mp3 at all")
            r = run([PY, "scripts/tts_doubao.py", "--text", "测试", "--api-key", "fake-key",
                     "--out", "out", "--wav-rate", "16000"], base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertEqual(list(out.glob("*.wav")), [])


class Q11QcAudioTests(unittest.TestCase):
    def test_check_reports_missing_and_fix_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            code = f"""
import sys, pathlib
sys.path.insert(0, r"{TOOLS}")
sys.path.insert(0, r"{ROOT/'scripts'}")
import qc_audio
entries = [{{'file': 'nope.wav'}}]
bad, missing = qc_audio.check(None, None, entries, pathlib.Path(r"{base}"))
assert missing == ['nope.wav'], missing
assert bad == [], bad
assert qc_audio.outcome_exit(missing=['nope.wav'], bad=[], fix_requested=True,
                             fix_failed=0, again=[]) == 1
assert qc_audio.outcome_exit(missing=[], bad=[{{'file':'x'}}], fix_requested=True,
                             fix_failed=1, again=[]) == 1
assert qc_audio.outcome_exit(missing=[], bad=[], fix_requested=False,
                             fix_failed=0, again=[]) == 0
print('OK')
"""
            r = run([PY, "-c", code], base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)


class Q12AudioContractTests(unittest.TestCase):
    def test_offline_audit_enforces_16k_16bit_mono(self):
        code = f"""
import sys
sys.path.insert(0, r"{TOOLS}")
import audit_offline as m
assert m.AUDIO_RATE == 16000 and m.AUDIO_BITS == 16
assert m.format_errors(16000, 2, 1) == []
assert m.format_errors(24000, 2, 1), '24k 必须报错'
assert m.format_errors(16000, 1, 1), '8bit 必须报错'
assert m.format_errors(16000, 2, 2), '立体声必须报错'
print('OK')
"""
        r = run([PY, "-c", code], ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Q15DistReadmeTests(unittest.TestCase):
    def test_generated_readme_has_no_removed_behaviour(self):
        code = f"""
import sys, pathlib
sys.path.insert(0, r"{PKG}")
import build_dist
text = build_dist.readme()
assert '右上角 × 退出' not in text, '旧的右上角关闭说法仍在'
assert '压低游戏音量' not in text, '已删除的 duck 功能仍在说明里'
assert '托盘' in text
print('OK')
"""
        r = run([PY, "-c", code], PKG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Q16LinkCheckTests(unittest.TestCase):
    def test_link_check_covers_current_release_and_dev_docs(self):
        r = run([PY, "scripts/check_readme_links.py"], ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertIn("DEVELOPMENT.md", r.stdout)
        self.assertIn("CONTRIBUTING.md", r.stdout)
        self.assertIn("SECURITY.md", r.stdout)
        self.assertIn("v1.6.2", r.stdout)
        self.assertRegex(r.stdout, r"in ([3-9]\d|\d{3,}) documents")

    def test_broken_transitive_link_fails(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            (base / "README.md").write_text("[a](docs/a.md)\n", encoding="utf-8")
            (base / "docs").mkdir()
            (base / "docs" / "a.md").write_text("[坏](b.md)\n", encoding="utf-8")
            r = run([PY, str(SCRIPTS / "check_readme_links.py"), "--root", str(base),
                     "--seed", "README.md"], base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("b.md", r.stdout)


class Q20TestAudioTests(unittest.TestCase):
    def test_partial_failure_is_nonzero_and_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            code = f"""
import sys, pathlib
sys.path.insert(0, r"{TOOLS}")
sys.path.insert(0, r"{ROOT/'scripts'}")
import make_test_audio as m
m.load_phrases = lambda: [('i1', 'p1', False), ('i2', 'p2', False), ('i3', 'p3', False)]
seq = {{'n': 0}}
def fake_synth(*a, **k):
    seq['n'] += 1
    if seq['n'] == 1:
        return b'ID3fake'
    from tts_doubao import TtsError
    raise TtsError('boom')
m.synthesize = fake_synth
def fake_conv(src, dst, rate):
    pathlib.Path(dst).write_bytes(b'RIFF')
m.mp3_to_wav = fake_conv
sys.argv = ['make_test_audio.py', '--out', r"{base}", '--voices', 'xiaohe2',
            '--rush-every', '0', '--api-key', 'fake']
raise SystemExit(m.main())
"""
            r = run([PY, "-c", code], base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            manifest = (base / "manifest.jsonl").read_text(encoding="utf-8")
            self.assertEqual(len(manifest.strip().splitlines()), 1)
            self.assertTrue((base / "manifest-failures.jsonl").is_file())
            self.assertIn("p2", (base / "manifest-failures.jsonl").read_text(encoding="utf-8"))


class Q23ReleaseConfigTests(unittest.TestCase):
    def _rewrite(self, config_text):
        code = f"""
import sys
sys.path.insert(0, r"{PKG}")
import build_dist
text, warnings = build_dist.sanitize_release_config({config_text!r})
print(text)
for w in warnings:
    print(w, file=sys.stderr)
"""
        return run([PY, "-c", code], PKG)

    def test_secret_values_fail_the_gate(self):
        text = ('ai:\n  enabled: true\n  api_key: "sk-' + 'Z9y8X7w6' * 4 + '"\n')
        r = self._rewrite(text)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("密钥", r.stderr)

    def test_dangerous_switches_are_forced_off(self):
        text = ("ai:\n  enabled: true\n  api_key: \"\"\n"
                "car_control:\n  enabled: true\n")
        r = self._rewrite(text)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("ai:\n  enabled: false", out)
        self.assertIn("car_control:\n  enabled: false", out)
        self.assertIn("已强制关闭", r.stderr)

    def test_current_repo_config_passes_gate(self):
        code = f"""
import sys
sys.path.insert(0, r"{PKG}")
import build_dist
from pathlib import Path
text = (Path(r"{PKG.parent}") / 'config.yaml').read_text(encoding='utf-8')
out, warnings = build_dist.sanitize_release_config(text)
assert 'sk-' not in out.split('api_key:')[1].splitlines()[0]
print('OK')
"""
        r = run([PY, "-c", code], PKG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_config_only_cli(self):
        r = run([PY, "packaging/build_dist.py", "--check-config-only"],
                ROOT / "voice-assistant")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("配置检查通过", r.stdout)


class Q21LicenseTests(unittest.TestCase):
    def test_pyirsdk_license_text_and_spec_collection(self):
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("pyirsdk", notice)
        self.assertIn("MIT License", notice)
        self.assertIn("Copyright (c) 2014 Mihail Latyshov", notice)
        spec = (PKG / "xiaoxiao.spec").read_text(encoding="utf-8")
        self.assertIn("_collect_license_datas", spec)
        self.assertIn("pyirsdk", spec)
        self.assertIn("licenses", spec)


class Q13Q14InstallerTests(unittest.TestCase):
    def test_user_editable_phrase_file_survives_upgrade_and_uninstall(self):
        iss = (PKG / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("intents_examples.yaml", iss)
        self.assertIn("onlyifdoesntexist", iss)
        # Inno Setup 没有 uninsnofile 这个 flag；想表达「卸载不删除」必须用
        # uninsneveruninstall，否则 ISCC 直接编译失败（v1.6.3 构建时踩到）。
        self.assertIn("uninsneveruninstall", iss)
        self.assertNotIn("uninsnofile", iss)

    def test_installer_can_sync_spotter_pack_into_game_dir(self):
        iss = (PKG / "installer.iss").read_text(encoding="utf-8")
        self.assertIn('Name: "gamespotter"', iss)
        self.assertIn("spcc", iss)
        self.assertIn("iRacing", iss)
        self.assertIn("xcopy", iss.lower())
        self.assertIn("CurStepChanged", iss)


class Q03Q06GateTests(unittest.TestCase):
    def test_ci_workflow_runs_real_regressions(self):
        wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        # CI 必须先装依赖再跑 pytest；Q 组用 stdlib unittest（runner 不保证带 pytest）。
        for token in ("check_readme_links.py", "test_audit_build_batch2",
                      "pytest", "check-config-only"):
            self.assertIn(token, wf)

    def test_window_smoke_gate_rejects_false_green(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            ok = base / "ok.py"
            ok.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            r = run([PY, str(SCRIPTS / "verify_window_smoke.py"),
                     "--self-test-script", str(ok)], base)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("window-smoke", r.stdout + r.stderr)

            good = base / "good.py"
            good.write_text(
                "import json,os,pathlib,sys\n"
                "d=pathlib.Path(os.environ['LITTLE_NAVIGATOR_DATA_DIR'])/'logs'\n"
                "d.mkdir(parents=True,exist_ok=True)\n"
                "(d/'window-smoke.json').write_text(json.dumps("
                "{'renderer':'edgechromium','title':'小小领航员','loaded':True}),"
                "encoding='utf-8')\n", encoding="utf-8")
            r = run([PY, str(SCRIPTS / "verify_window_smoke.py"),
                     "--self-test-script", str(good)], base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
