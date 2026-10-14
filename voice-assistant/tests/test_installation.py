import json,os,pathlib,tempfile,unittest
from unittest.mock import patch
import yaml
from src import config

class InstalledDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.tmp.name).resolve()
        self.app=self.root/'安装位置'
        self.data=self.root/'用户数据'
        self.app.mkdir()
        (self.app/'installed-mode').write_text('installed')
        (self.app/'config.yaml').write_text(yaml.safe_dump({'voice_pack':{'dir':'build/pack'},'asr':{'models_dir':'models'}}),encoding='utf-8')
        self.patches=[patch.object(config,'is_frozen',return_value=True),patch.object(config,'runtime_dir',return_value=self.app),patch.dict(os.environ,{'LITTLE_NAVIGATOR_DATA_DIR':str(self.data)})]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_assets_stay_in_install_directory(self):
        cfg=config.load()
        self.assertEqual(config.resolve(cfg,cfg['voice_pack']['dir']),self.app/'build/pack')
        self.assertEqual(config.resolve(cfg,cfg['asr']['models_dir']),self.app/'models')
    def test_all_writes_are_in_user_directory(self):
        cfg=config.load()
        paths=[cfg['_local_path'],cfg['voice_pack']['state_file'],cfg['logging']['dir'],cfg['logging']['qa_log'],cfg['intent']['miss_log']['path']]
        for value in paths:self.assertTrue(pathlib.Path(value).is_relative_to(self.data))
    def test_old_local_settings_migrate_without_deleting_source(self):
        old=self.app/'config.local.yaml'
        old.write_text('playback:\n  volume: 0.4\n',encoding='utf-8')
        cfg=config.load()
        self.assertEqual(cfg['playback']['volume'],.4)
        self.assertTrue(old.exists())
        self.assertEqual((self.data/old.name).read_bytes(),old.read_bytes())
    def test_existing_user_settings_win_on_upgrade(self):
        self.data.mkdir()
        (self.app/'config.local.yaml').write_text('playback:\n  volume: 0.1\n')
        local=self.data/'config.local.yaml'
        local.write_text('playback:\n  volume: 0.7\n')
        before=local.read_bytes()
        self.assertEqual(config.load()['playback']['volume'],.7)
        self.assertEqual(local.read_bytes(),before)
    def test_old_log_override_cannot_redirect_writes_to_app(self):
        (self.app/'config.local.yaml').write_text('logging:\n  dir: ./logs\n  qa_log: ./logs/qa.jsonl\n')
        self.assertEqual(pathlib.Path(config.load()['logging']['dir']),self.data/'logs')
    def test_cooldown_is_copied_once(self):
        (self.app/'state').mkdir()
        (self.app/'state/cooldown.json').write_text('{"old":1}')
        config.load()
        dest=self.data/'state/cooldown.json'
        self.assertEqual(json.loads(dest.read_text()),{'old':1})
        dest.write_text('{"new":2}')
        config.load()
        self.assertEqual(json.loads(dest.read_text()),{'new':2})
    def test_explicit_config_and_portable_mode_keep_local_behavior(self):
        cfg=config.load(self.app/'config.yaml')
        self.assertEqual(pathlib.Path(cfg['_local_path']),self.app/'config.local.yaml')
        (self.app/'installed-mode').unlink()
        self.assertEqual(config.data_dir(),self.app)
    def test_relative_user_data_override_is_rejected(self):
        with patch.dict(os.environ,{'LITTLE_NAVIGATOR_DATA_DIR':'relative'}):
            with self.assertRaises(ValueError):config.load()

if __name__=='__main__':unittest.main()
