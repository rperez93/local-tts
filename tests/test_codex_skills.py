from pathlib import Path
import tempfile
import unittest
from localtts import skills


class CodexSkillsTest(unittest.TestCase):
    def test_native_install_migrates_only_managed_block_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / '.codex' / 'AGENTS.md'
            legacy.parent.mkdir()
            before = 'Keep my rules.\n\n'
            after = '\n\nAlso keep these rules.\n'
            old = before + skills.BEGIN + '\nold TTS instructions\n' + skills.END + after
            legacy.write_text(old)
            skills.install('codex', base=tmp)
            self.assertEqual(legacy.read_text(), before + after)
            self.assertEqual(legacy.with_name('AGENTS.md.local-tts.bak').read_text(), old)
            for name in skills.SKILLS:
                path = Path(tmp) / '.agents' / 'skills' / name / 'SKILL.md'
                self.assertEqual(path.read_text(), skills.read_skill(name))
            self.assertTrue(skills.status('codex', base=tmp)[0])
            skills.install('codex', base=tmp)
            self.assertEqual(legacy.read_text(), before + after)
            skills.uninstall('codex', base=tmp)
            self.assertEqual(legacy.read_text(), before + after)
            self.assertFalse(skills.status('codex', base=tmp)[0])

    def test_dry_run_preserves_legacy_file_and_does_not_create_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / '.codex' / 'AGENTS.md'
            legacy.parent.mkdir()
            old = skills.BEGIN + '\nold\n' + skills.END
            legacy.write_text(old)
            planned = skills.install('codex', base=tmp, dry_run=True)
            self.assertIn(legacy, planned)
            self.assertEqual(legacy.read_text(), old)
            self.assertFalse((Path(tmp) / '.agents').exists())

    def test_partial_install_does_not_remove_full_legacy_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / '.codex' / 'AGENTS.md'
            legacy.parent.mkdir()
            old = skills.BEGIN + '\nold\n' + skills.END
            legacy.write_text(old)
            skills.install('codex', base=tmp, names=['local-tts-speak'])
            self.assertEqual(legacy.read_text(), old)
