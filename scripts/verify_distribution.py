"""Install the built wheel, then exercise it outside the source tree without sockets.

Run in a disposable virtual environment. Network is used by pip for dependencies;
no network, league configuration or files are used by the installed demo.
"""
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import zipfile

root = pathlib.Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
wheel = root / 'dist' / f'fantasy_football_toolkit-{version}-py3-none-any.whl'
if not wheel.is_file():
    raise SystemExit('Build the current release wheel in dist/ before verification.')
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    for filename in ('LICENSE', 'NOTICE.md'):
        if not any(name.endswith('/licenses/' + filename) for name in names):
            raise SystemExit(f'Wheel is missing {filename}')
subprocess.run([sys.executable, '-m', 'pip', 'install', '--force-reinstall', str(wheel)], check=True)
probe = '''
import importlib.metadata, pathlib, socket, sys
import ff
from ff import cli
assert importlib.metadata.version('fantasy-football-toolkit') == sys.argv[1]
assert 'site-packages' in str(pathlib.Path(ff.__file__).resolve())
def forbidden(*args, **kwargs):
    raise AssertionError('installed demo attempted network access')
socket.socket.connect = forbidden
socket.getaddrinfo = forbidden
assert cli.main(['demo']) == 0
assert not list(pathlib.Path.cwd().iterdir()), 'demo created files'
assert cli.build_parser().parse_args(['refresh-fantasypros', '--season', '2030', '--week', '2']).command == 'refresh-fantasypros'
'''
with tempfile.TemporaryDirectory() as outside:
    completed = subprocess.run([sys.executable, '-I', '-c', probe, version], cwd=outside,
                               text=True, capture_output=True, check=True)
    expected = (root / 'examples' / 'demo-output.txt').read_text()
    if completed.stdout != expected:
        raise SystemExit('Installed demo output differs from the checked-in example.')
    subprocess.run([sys.executable, '-I', '-m', 'ff.cli', '--help'], cwd=outside, check=True)
print('Exact built wheel: licenses, isolated imports, offline demo and sample output verified.')
