import os
import tempfile
import unittest
from unittest.mock import patch
import wave

from localtts import audio, _playback_runner as runner


def write_part(directory, index, rate=1000):
    with wave.open(audio.stream_part_path(directory, index), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x01' * 100)


class StreamBatchingTest(unittest.TestCase):
    def run_stream(self, directory, callback):
        with patch.object(runner.signal, 'signal'), patch.object(audio, '_write_state'), patch.object(audio, 'find_player', side_effect=lambda path, preferred: [path]), patch.object(runner, '_play', side_effect=lambda cmd: callback(cmd[0])):
            runner.run_stream([os.path.join(directory, 'lock'), '', '', '', directory, '', '0', 'sample'])

    def test_ready_fragments_use_one_player_without_added_silence(self):
        directory = audio.stream_new()
        for i in range(3):
            write_part(directory, i)
        audio.stream_finish(directory, 3)
        durations = []
        self.run_stream(directory, lambda path: durations.append(audio.duration(path)))
        self.assertEqual(durations, [.3])
        self.assertFalse(os.path.exists(directory))

    def test_first_fragment_starts_before_later_fragments_exist(self):
        directory = audio.stream_new()
        write_part(directory, 0)
        durations = []
        def play(path):
            durations.append(audio.duration(path))
            if len(durations) == 1:
                write_part(directory, 1)
                write_part(directory, 2)
                audio.stream_finish(directory, 3)
        self.run_stream(directory, play)
        self.assertEqual(durations, [.1, .2])

    def test_different_sample_rates_keep_separate_players(self):
        directory = audio.stream_new()
        write_part(directory, 0)
        write_part(directory, 1, rate=2000)
        audio.stream_finish(directory, 2)
        durations = []
        self.run_stream(directory, lambda path: durations.append(audio.duration(path)))
        self.assertEqual(durations, [.1, .05])
