import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from localtts import servers
from localtts.providers.rvc import RvcProvider
from localtts.errors import TTSError

class RvcParametersTest(unittest.TestCase):
    def setUp(self):
        scope = {'__name__': 'test_template'}
        exec(compile(servers.template('rvc'), '<server>', 'exec'), scope)
        class Fake:
            f0up_key = 2
            index_rate = .88
            protect = .2
            fail = False
            def __init__(self): self.calls = []
            def set_params(self, **params): self.__dict__.update(params)
            def infer_file(self, source, target):
                self.calls.append((self.f0up_key, self.index_rate, self.protect, target))
                if self.fail: raise RuntimeError('conversion failed')
                with open(target, 'wb') as f: f.write(b'audio')
        self.model = Fake()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, 'source.wav')
        with open(self.source, 'wb') as f: f.write(b'input')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), scope['make_handler'](
            {'voice': self.model}, 'voice', [0], threading.Lock()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def request(self, **params):
        body = dict(input_path=self.source, **params)
        request = urllib.request.Request(self.url + '/convert', data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response: return response.read()

    def test_overrides_are_isolated_and_zero_pitch_is_honored(self):
        self.request(index_rate=.6, protect=0, pitch=0)
        self.request()
        self.assertEqual(self.model.calls[0][:3], (0, .6, 0))
        self.assertEqual(self.model.calls[1][:3], (2, .88, .2))
        self.assertEqual((self.model.f0up_key,self.model.index_rate,self.model.protect),(2,.88,.2))
        self.assertNotEqual(self.model.calls[0][3], self.model.calls[1][3])
        self.assertTrue(all(not os.path.exists(c[3]) for c in self.model.calls))

    def test_failure_restores_parameters_and_removes_partial_output(self):
        self.model.fail=True
        with self.assertRaises(urllib.error.HTTPError) as raised: self.request(pitch=8)
        self.assertEqual(raised.exception.code, 500)
        self.assertEqual(self.model.f0up_key, 2)
        self.assertFalse(os.path.exists(self.model.calls[0][3]))

    def test_invalid_values_fail_before_inference(self):
        for params in ({'protect':.8}, {'index_rate':float('nan')}, {'pitch':1.2}):
            with self.assertRaises(urllib.error.HTTPError) as raised: self.request(**params)
            self.assertEqual(raised.exception.code,400)
        self.assertEqual(self.model.calls,[])

    def test_provider_uses_regional_parameters(self):
        provider=RvcProvider({'server_url':self.url,'conversion':{'es':{'index_rate':.7},'es-MX':{'index_rate':.6,'pitch':0}}},lang='ES_mx')
        with patch.object(provider,'ensure_server'):
            provider._convert_via_server(self.url,self.source,os.path.join(self.tmp.name,'out.wav'))
        self.assertEqual(self.model.calls[0][:2],(0,.6))

    def test_old_server_cannot_silently_ignore_requested_tuning(self):
        provider=RvcProvider({'index_rate':.6})
        with patch.object(provider,'ensure_server'),patch.object(provider,'server_capabilities',return_value={}):
            with self.assertRaisesRegex(TTSError,'refresh'):
                provider._convert_via_server(self.url,self.source,os.path.join(self.tmp.name,'out.wav'))

    def test_retrieval_override_keeps_startup_pitch_when_not_requested(self):
        provider=RvcProvider({'index_rate':.6,'pitch':0})
        with patch.object(provider,'ensure_server'):
            provider._convert_via_server(self.url,self.source,os.path.join(self.tmp.name,'out.wav'))
        self.assertEqual(self.model.calls[0][:2],(2,.6))
