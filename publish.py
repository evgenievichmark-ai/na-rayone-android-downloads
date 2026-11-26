"""Publish the exact Playground APK described by the triggering commit."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = 'evgenievichmark-ai/na-rayone-android-downloads'
ORIGIN = 'https://moscow-game.116.203.229.54.sslip.io/install/'
SIGNER = 'e1f8b4f5c3814a4e57ee85ccdbf924924ac7a8fc59899ea17c2c3fbf0602e21b'


def release_info(tag):
    req = Request(f'https://api.github.com/repos/{REPO}/releases/tags/{tag}',
                  headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN']})
    try:
        with urlopen(req, timeout=20) as res:
            return json.load(res)
    except HTTPError as exc:
        if exc.code != 404:
            raise
    # The by-tag endpoint only guarantees published releases. An authenticated listing includes drafts.
    page = 1
    while True:
        listing = Request(f'https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}',
                          headers=req.headers)
        with urlopen(listing, timeout=20) as res:
            releases = json.load(res)
        for release in releases:
            if release['tag_name'] == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


def gh(*args):
    return subprocess.check_output(['gh', 'release', *args, '--repo', REPO], text=True).strip()


def verify_asset(info, name, size, digest):
    asset = next((a for a in info['assets'] if a['name'] == name), None)
    if not asset or asset['size'] != size or asset['digest'] != 'sha256:' + digest:
        raise ValueError(f'Published asset {name} differs from expected release')


def main():
    spec = json.loads(Path('release.json').read_text())
    version, size, sha = spec['versionName'], spec['bytes'], spec['sha256']
    if (not re.fullmatch(r'\d+\.\d+\.\d+', version) or type(spec['versionCode']) is not int or
            not 0 < size <= 250_000_000 or not re.fullmatch(r'[0-9a-f]{64}', sha)):
        raise ValueError('Invalid APK release metadata')
    tag = 'v' + version
    qr = Path('na-rayone-github-qr.png')
    qr_hash = hashlib.sha256(qr.read_bytes()).hexdigest()
    existing = release_info(tag)
    if existing and not existing['draft']:
        verify_asset(existing, 'na-rayone.apk', size, sha)
        verify_asset(existing, qr.name, qr.stat().st_size, qr_hash)
        print(f'{tag} is already published and verified')
        return

    with tempfile.TemporaryDirectory() as staging:
        apk = Path(staging) / 'na-rayone.apk'
        subprocess.run(['curl', '--fail', '--silent', '--show-error', '--retry', '3',
                        '--retry-all-errors', '--max-time', '90', '--max-filesize', str(size),
                        '--output', str(apk), ORIGIN + f'na-rayone-mvp-{version}.apk'], check=True)
        if apk.stat().st_size != size or hashlib.sha256(apk.read_bytes()).hexdigest() != sha:
            raise ValueError('Origin APK differs from the CI release')
        tools = Path(os.environ['ANDROID_HOME']) / 'build-tools'
        sdk = max((d for d in tools.iterdir() if re.fullmatch(r'\d+\.\d+\.\d+', d.name)),
                  key=lambda d: tuple(map(int, d.name.split('.'))))
        signers = subprocess.check_output([sdk / 'apksigner', 'verify', '--print-certs', apk], text=True)
        if re.findall(r'^Signer #\d+ certificate SHA-256 digest: (\w+)$', signers, re.M) != [SIGNER]:
            raise ValueError('Unexpected Android signing certificate')
        badging = subprocess.check_output([sdk / 'aapt', 'dump', 'badging', apk], text=True)
        identity = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
        if not identity or identity.groups() != ('com.krysha.game', str(spec['versionCode']), version):
            raise ValueError('Unexpected APK package or version')
        if not existing:
            gh('create', tag, '--target', os.environ['GITHUB_SHA'], '--draft',
               '--title', f'На районе {version} для Android',
               '--notes', f'Скачайте na-rayone.apk и выберите «Обновить». SHA-256 — {sha}.')
        gh('upload', tag, str(apk), str(qr), '--clobber')
        draft = release_info(tag)
        verify_asset(draft, apk.name, size, sha)
        verify_asset(draft, qr.name, qr.stat().st_size, qr_hash)
        latest = gh('view', '--json', 'tagName', '--jq', '.tagName')
        advance = tuple(map(int, version.split('.'))) >= tuple(map(int, latest.removeprefix('v').split('.')))
        gh('edit', tag, '--draft=false', '--latest=' + str(advance).lower())
        print(f'{tag} published and verified')


if __name__ == '__main__':
    main()
