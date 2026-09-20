import os
import tempfile
import unittest
import wave
from localtts import audio, text
from localtts.cli import _StreamSink
from localtts.providers.base import Provider


def write_wav(path):
    with wave.open(path, 'wb') as wav:
        wav.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x01' * 100)


class EndingSilenceTest(unittest.TestCase):
    def test_only_final_publication_is_padded_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source.wav')
            write_wav(source)
            sink = _StreamSink(directory, .35)
            sink(source)
            self.assertEqual(audio.duration(audio.stream_part_path(directory, 0)), .1)
            sink.final(source)
            self.assertEqual(audio.duration(audio.stream_part_path(directory, 1)), .45)
            self.assertEqual(audio.duration(source), .1)
            with wave.open(audio.stream_part_path(directory, 1)) as wav:
                wav.setpos(100)
                self.assertEqual(wav.readframes(350), b'\x00' * 700)

    def test_chunking_marks_only_last_chunk_without_delaying_first(self):
        class Fake(Provider):
            name = 'fake'
            default_format = 'wav'
            max_words = 2
            max_workers = 1
            def synthesize(self, sentence, path, voice=None):
                if 'Three' in sentence:
                    self_test.assertEqual(sink.count, 1)
                write_wav(path)
                return path
        self_test = self
        with tempfile.TemporaryDirectory() as directory:
            provider = Fake({})
            sink = _StreamSink(directory, .35)
            provider.on_part = sink
            text.synthesize_chunked(provider, 'One two. Three four.', os.path.join(directory, 'result.wav'))
            self.assertEqual(sink.count, 2)
            self.assertEqual(audio.duration(audio.stream_part_path(directory, 0)), .1)
            self.assertEqual(audio.duration(audio.stream_part_path(directory, 1)), .45)

    def test_legacy_callback_accepts_final_publication(self):
        provider = Provider({})
        received = []
        provider.on_part = received.append
        provider.emit_part('last.wav', final=True)
        self.assertEqual(received, ['last.wav'])
