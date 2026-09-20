"""Exercise the bundled server algorithm without a model or extra dependencies."""
import ast
import unittest

from localtts import servers


class ServerPhoneticsTest(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(servers.template('kokoro'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'substitute_phonetics')
        self.calls = []
        self.transcriptions = {}
        def phonemes(text, lang):
            self.calls.append(text)
            return self.transcriptions.get(text, text)
        namespace = {'phonemes': phonemes, '_warn_unsayable': lambda *args: None}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<server>', 'exec'), namespace)
        self.substitute = namespace['substitute_phonetics']

    def test_isolated_word_and_phrase_accept_ipa(self):
        for text, word in [('merge', 'merge'), ('merge.', 'merge'), ('pull request!', 'pull request')]:
            with self.subTest(text=text):
                suffix = text[-1] if text[-1] in '.!' else ''
                self.assertEqual(self.substitute(text, 'es', {word: 'IPA'}), ('IPA' + suffix, True))

    def test_longest_phrase_prevents_overlapping_word_replacement(self):
        result = self.substitute('before pull request after later', 'es',
                                 {'request': 'SHORT', 'pull request': 'LONG'})
        self.assertEqual(result, ('before LONG after later', True))
        self.assertEqual(len(self.calls), 2, 'one baseline and one placeholder transcription')

    def test_repeated_words_replace_each_occurrence(self):
        result = self.substitute('before merge and merge after later', 'es', {'merge': 'IPA'})
        self.assertEqual(result, ('before IPA and IPA after later', True))

    def test_unused_dictionary_does_not_phonemize(self):
        self.assertEqual(self.substitute('ordinary sentence', 'es', {'merge': 'IPA'}),
                         ('ordinary sentence', False))
        self.assertEqual(self.calls, [])

    def test_unrelated_phonemizer_divergence_is_still_rejected(self):
        self.transcriptions['before Kalakala after later'] = 'entirely different output'
        self.assertEqual(self.substitute('before merge after later', 'es', {'merge': 'IPA'}),
                         ('before merge after later', False))
