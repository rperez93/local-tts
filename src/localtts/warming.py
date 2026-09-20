"""Explicit, bounded model warm-up and phrase prefill; no audio playback."""
import argparse
import contextlib
import io
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from localtts import config, providers
from localtts.errors import TTSError


def keepalive(url):
    request = urllib.request.Request(url.rstrip('/') + '/keepalive', data=b'', method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 204:
                raise TTSError('unexpected keepalive response from %s' % url)
    except (urllib.error.URLError, OSError) as exc:
        raise TTSError('cannot keep %s warm: %s; check the server or run `tts servers --refresh`' % (url, exc))


def command(argv, speak):
    parser = argparse.ArgumentParser(prog='tts warm', description=__doc__)
    parser.add_argument('--lang', action='append', default=[], help='repeat for multiple remembered languages')
    parser.add_argument('--text', help='exact phrase to prefill; default is a short language sample')
    parser.add_argument('--keep-alive', type=int, default=None, metavar='SECONDS',
                        help='keep selected servers resident for this many seconds (max 86400); Ctrl-C stops')
    args = parser.parse_args(argv)
    cfg = config.load()
    config.validate(cfg)
    if args.keep_alive is None: args.keep_alive = int(cfg['warm_keep_alive_seconds'])
    if not 0 <= args.keep_alive <= 86400:
        parser.error('--keep-alive must be between 0 and 86400 seconds')
    def plan(current):
        languages = args.lang or list(current.get('languages') or {})
        if not languages: raise TTSError('record a language with `tts languages --set` before warming')
        urls, plans = set(), []
        for lang in languages:
            entry = config.language_entry(current, lang)
            if not entry: raise TTSError('no backend remembered for language %r' % lang)
            provider = providers.build(entry.get('provider') or current['provider'], current, lang=lang)
            selected = [provider]
            if provider.name == 'rvc': selected.append(provider.base_provider_instance())
            urls.update(p.settings['server_url'] for p in selected if p.settings.get('server_url') and p.name in ('kokoro', 'rvc'))
            defaults = {'es': 'La voz está lista.', 'en': 'The voice is ready.'}
            sentence = args.text or defaults.get(config.normalize_language(lang)[1])
            if not sentence: raise TTSError('provide --text for language %s' % lang)
            plans.append((lang, provider.default_format, sentence))
        return urls, plans

    def render(plans):
        with tempfile.TemporaryDirectory(prefix='local-tts-warm-') as directory:
            for index, (lang, extension, sentence) in enumerate(plans):
                started = time.monotonic()
                with contextlib.redirect_stdout(io.StringIO()):
                    speak(['--lang', lang, '--no-play', '--refresh-cache', '-o',
                           str(Path(directory) / ('%d.%s' % (index, extension))), sentence])
                print('%s warmed in %.2fs; cache follows current limits' %
                      (lang, time.monotonic() - started), flush=True)

    urls, plans = plan(cfg)
    if args.keep_alive and not urls:
        raise TTSError('selected providers have no persistent local servers to keep warm')
    render(plans)
    deadline = time.monotonic() + args.keep_alive
    if args.keep_alive: print('Keeping servers warm for %ds; Ctrl-C stops. Settings reload automatically.' % args.keep_alive, flush=True)
    while time.monotonic() < deadline:
        latest = config.load()
        config.validate(latest)
        if latest != cfg:
            urls, plans = plan(latest)
            # Never kill a model that another agent may currently be using.
            for name in ('kokoro', 'rvc'):
                if cfg['providers'][name].get('server_start') != latest['providers'][name].get('server_start'):
                    print('%s startup settings changed; they apply after the server next exits. Active synthesis is preserved.' % name, flush=True)
            cfg = latest
            render(plans)
        for url in sorted(urls): keepalive(url)
        time.sleep(min(float(cfg['warm_interval_seconds']), max(0, deadline - time.monotonic())))
    return 0
