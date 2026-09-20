"""Bounded disk audio cache. Standard library only; no plaintext prompts in metadata."""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import sys
import time
import urllib.request
import urllib.error
import http.client

from localtts import config, lock
from localtts.errors import TTSError

HEADER = 1024
ENTRY = re.compile(r'[0-9a-f]{64}\.entry\Z')


def cache_path(cfg):
    if cfg.get('cache_dir'):
        return Path(cfg['cache_dir']).expanduser() / 'audio-v1'
    if os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library/Caches'
    else:
        root = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
    return root / 'local-tts/audio-v1'


def server_matches(settings, known_assets=None):
    """Do not cache a resident model under settings it has not loaded yet."""
    url, command = settings.get('server_url'), settings.get('server_start')
    if not url or not command: return True  # remote servers use explicit cache_revision
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/health', timeout=.25) as response:
            state = json.load(response).get('cache_state')
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError as exc:
        # A busy listener is not proof that no old model is resident.
        return isinstance(exc.reason, ConnectionRefusedError)
    except (OSError, http.client.HTTPException):
        return False
    except (ValueError, AttributeError):
        return False
    if not isinstance(state, dict): return False  # refresh legacy local server first
    try:
        tokens = shlex.split(command)
        index = next(i for i, token in enumerate(tokens) if token.endswith('.py'))
        arguments = tokens[index + 1:]
        if arguments and arguments[-1] == '&': arguments.pop()
        if arguments != state['argv'] or os.path.realpath(os.path.expanduser(tokens[index])) != state['script']:
            return False
        for path, expected in state['assets'].items():
            if known_assets is not None and path not in known_assets:
                return False  # opaque local asset: configure its identity before caching
            st = os.stat(path)
            if [st.st_size, st.st_mtime_ns] != expected: return False
    except (StopIteration, ValueError, KeyError, TypeError, OSError):
        return False
    return True


def fingerprint(provider, spoken, voice, cfg, output_format="wav"):
    """Hash effective synthesis inputs, local asset identities, and implementation.

    Server-side replacements at the same remote URL need cache_revision or a clear.
    Dynamic phonetics hooks are deliberately not cached.
    """
    settings = {provider.name: provider.settings}
    if provider.name == 'rvc':
        base = provider.base_provider_instance()
        settings[base.name] = base.settings
    assets = {}
    def visit(value):
        if isinstance(value, dict):
            for item in value.values(): visit(item)
        elif isinstance(value, (tuple, list)):
            for item in value: visit(item)
        elif isinstance(value, str):
            candidates = [value]
            try: candidates += shlex.split(value)
            except ValueError: pass
            for candidate in candidates:
                candidate = candidate.split('=', 1)[-1]
                path = Path(candidate).expanduser()
                try:
                    if path.is_dir():
                        for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
                            item = path / name
                            if item.is_file():
                                st = item.stat()
                                assets[str(item.resolve())] = [st.st_size, st.st_mtime_ns]
                    if path.is_file():
                        st = path.stat()
                        assets[str(path.resolve())] = [st.st_size, st.st_mtime_ns]
                except (OSError, ValueError): pass
    visit(settings)
    # The bundled Kokoro server uses this model directory even when its startup
    # command does not name it. Include it offline too, so cache hits need no model.
    if 'kokoro' in settings and settings['kokoro'].get('server_start'):
        visit(str(Path.home() / '.local/share/kokoro-models'))
    visit(voice)
    for candidate in list(assets):
        if candidate.endswith('.onnx'):
            visit(candidate + '.json')
    for options in settings.values():
        if options.get('model_dir'):
            from localtts.providers.kokoro import MODEL_FILES
            for filename in MODEL_FILES:
                visit(str(Path(options['model_dir']).expanduser() / filename))
    if not all(server_matches(options, assets) for options in settings.values()):
        return None
    code = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.glob('*.py')) + sorted((root / 'providers').glob('*.py')):
        code.update(path.read_bytes())
    body = dict(schema=1, text=spoken, voice=voice, language=provider.lang, format=output_format,
                provider=provider.name, settings=settings, assets=assets,
                platform=[sys.platform, platform.machine(), platform.release()],
                implementation=code.hexdigest(), pronunciations=cfg.get('pronunciations'),
                ending_silence_ms=cfg.get('ending_silence_ms', 0),
                revision=cfg.get('cache_revision', ''))
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AudioCache:
    def __init__(self, cfg, clock=time.time):
        self.directory = cache_path(cfg)
        self.clock = clock
        self.policy = cfg.get("cache_policy", "lfu")
        if self.policy not in ("lfu", "lru"):
            raise TTSError("cache_policy must be lfu or lru")
        try:
            hours = float(cfg.get('cache_ttl_hours', 36))
            mb = float(cfg.get('cache_max_mb', 256))
            if not math.isfinite(hours) or not math.isfinite(mb) or hours <= 0 or mb <= 0:
                raise ValueError
            self.ttl = hours * 3600
            self.limit = int(mb * 1024 * 1024)
        except (ValueError, TypeError, OverflowError):
            raise TTSError('cache_ttl_hours and cache_max_mb must be finite positive numbers')

    @contextlib.contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.directory / 'cache.lock', 'a+b') as handle:
            lock.acquire(handle)
            try: yield
            finally: lock.release(handle)

    def _scan(self):
        rows = []
        now = self.clock()
        for path in self.directory.iterdir():
            if path.name.endswith('.pending') and re.fullmatch(r'[0-9a-f]{64}\.pending', path.name):
                path.unlink()  # interrupted writer; the directory lock excludes live writers
            if not ENTRY.fullmatch(path.name) or path.is_symlink(): continue
            try:
                with path.open('rb') as handle:
                    meta = json.loads(handle.read(HEADER).rstrip(b' '))
                size = path.stat().st_size
                if (size != meta['size'] + HEADER or meta['size'] <= 0 or
                        now >= min(meta['expires'], meta['created'] + self.ttl)):
                    raise ValueError
                # Validate fields used by LFU ordering, including corrupted metadata.
                if not all(math.isfinite(meta[k]) for k in ('hits', 'accessed', 'created', 'expires')):
                    raise ValueError
                rows.append((path, meta, size))
            except (OSError, ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)
        return rows

    @staticmethod
    def _header(meta):
        raw = json.dumps(meta, separators=(',', ':')).encode()
        if len(raw) > HEADER: raise ValueError('cache header too large')
        return raw.ljust(HEADER, b' ')

    def _prune(self, rows, reserve=0):
        total = sum(row[2] for row in rows)
        # LFU, with least-recent access as the tie breaker; TTL is never extended on hits.
        for row in sorted(rows, key=lambda r: (r[1]['hits'] if self.policy == 'lfu' else 0, r[1]['accessed'], r[0].name)):
            if total + reserve <= self.limit: break
            row[0].unlink(missing_ok=True)
            total -= row[2]
            rows.remove(row)
        return rows

    def get(self, key, destination):
        with self.locked():
            rows = self._prune(self._scan())
            for path, meta, _ in rows:
                if path.name != key + '.entry': continue
                # Copy, never hand a player the evictable cache file.
                with path.open('rb') as source, open(destination, 'wb') as target:
                    source.seek(HEADER)
                    shutil.copyfileobj(source, target, 64 * 1024)
                meta['hits'] += 1
                meta['accessed'] = self.clock()
                with path.open('r+b') as handle: handle.write(self._header(meta))
                return True
        return False

    def put(self, key, source, replace=False):
        size = os.path.getsize(source)
        if size <= 0 or size + HEADER > self.limit: return False
        with self.locked():
            rows = self._scan()
            for row in list(rows):
                if row[0].name == key + '.entry':
                    if not replace: return False
                    row[0].unlink()
                    rows.remove(row)
            self._prune(rows, size + HEADER)
            now = self.clock()
            meta = dict(size=size, created=now, expires=now + self.ttl, hits=1, accessed=now)
            pending = self.directory / (key + '.pending')
            try:
                with pending.open('wb') as target, open(source, 'rb') as handle:
                    target.write(self._header(meta))
                    shutil.copyfileobj(handle, target, 64 * 1024)
                os.replace(pending, self.directory / (key + '.entry'))
            finally:
                pending.unlink(missing_ok=True)
        return True

    def maintain(self, clear=False):
        with self.locked():
            rows = self._scan()
            if clear:
                for path, _, _ in rows: path.unlink(missing_ok=True)
                rows = []
            else: rows = self._prune(rows)
            return dict(directory=str(self.directory), entries=len(rows),
                        bytes=sum(row[2] for row in rows), max_bytes=self.limit,
                        ttl_hours=self.ttl / 3600, policy=self.policy)


def command(argv):
    parser = argparse.ArgumentParser(prog='tts cache', description='Inspect, expire or clear cached audio.')
    parser.add_argument('action', nargs='?', choices=('status', 'prune', 'clear'), default='status')
    args = parser.parse_args(argv)
    cfg = config.load()
    result = AudioCache(cfg).maintain(clear=args.action == 'clear')
    result['enabled'] = bool(cfg.get('cache_enabled'))
    print(json.dumps(result, indent=2))
    return 0
