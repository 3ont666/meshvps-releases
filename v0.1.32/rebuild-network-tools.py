#!/usr/bin/env python3
"""Rebuild the pinned MeshVPS network tools offline, or package existing binaries.

Only the standard library and the exact Go toolchain named in the manifest are
required. The corresponding sources include the vendor packages selected for
these Windows/Linux amd64 builds; this command never runs go mod vendor.
"""

import argparse
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCE_MEMBERS = 200_000
BINARY_NAMES = (
    'meshvps-engine.exe', 'meshvps-engine-linux',
    'yggdrasil.exe', 'yggdrasil-linux',
    'yggdrasilctl.exe', 'yggdrasilctl-linux',
)
ENGINE_TAGS = ['with_quic', 'with_gvisor']
DOS_NAMES = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{kind}{i}' for kind in ('COM', 'LPT') for i in range(1, 10)}


class RebuildError(RuntimeError):
    pass


def default_assets(script_directory=None):
    directory = Path(script_directory) if script_directory is not None else Path(__file__).resolve().parent
    if all((directory / name).is_file() for name in ('network-build.json', 'network-sources.tar.xz')):
        return directory
    return directory.parent / 'assets'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, dict):
        raise RebuildError('The build manifest must be a JSON object.')
    for key, pattern in (
        ('version', r'[0-9]+\.[0-9]+\.[0-9]+'),
        ('go_version', r'go[0-9]+\.[0-9]+\.[0-9]+'),
        ('source_sha256', r'[0-9a-fA-F]{64}'),
    ):
        if not isinstance(manifest.get(key), str) or not re.fullmatch(pattern, manifest[key]):
            raise RebuildError(f'Invalid manifest field: {key}.')
    if manifest.get('engine_tags', ENGINE_TAGS) != ENGINE_TAGS:
        raise RebuildError('This builder requires engine_tags with_quic, with_gvisor.')
    url = manifest.get('source_url', '')
    if not isinstance(url, str) or not re.fullmatch(r'https?://[^\s]+', url):
        raise RebuildError('The manifest needs a corresponding-source URL.')
    return manifest


def verify_sources(path, manifest):
    actual = sha256(path)
    if actual.lower() != manifest['source_sha256'].lower():
        raise RebuildError(f'Source archive SHA-256 mismatch: expected {manifest["source_sha256"]}, got {actual}.')


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) &
                                           getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def safe_extract(archive, destination, max_bytes=MAX_SOURCE_BYTES):
    """Extract only portable regular files/directories, without tar extraction APIs."""
    destination = Path(destination)
    if destination.exists() and is_link(destination):
        raise RebuildError('The source directory must not be a link or reparse point.')
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    total = 0
    seen = set()
    with tarfile.open(archive, 'r:*') as source:
        for count, member in enumerate(source, 1):
            if count > MAX_SOURCE_MEMBERS:
                raise RebuildError('The source archive contains too many entries.')
            name = member.name
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith(('/', '\\')) or '\\' in name or '\0' in name or
                    any(part in ('.', '..') or ':' in part or part.endswith((' ', '.')) or
                        part.split('.')[0].upper() in DOS_NAMES for part in parts)):
                raise RebuildError(f'Unsafe archive path: {name!r}.')
            if not member.isdir() and not member.isfile():
                raise RebuildError(f'Only regular files/directories are allowed: {name!r}.')
            if member.size < 0 or member.isdir() and member.size:
                raise RebuildError(f'Invalid archive entry size: {name!r}.')
            total += member.size
            if total > max_bytes:
                raise RebuildError('The extracted sources exceed the 2 GiB size limit.')
            key = '/'.join(parts).casefold()
            if key in seen:
                raise RebuildError(f'Duplicate archive path: {name!r}.')
            seen.add(key)
            target = base.joinpath(*parts)
            if not target.resolve().is_relative_to(base):
                raise RebuildError(f'Archive path escapes the source directory: {name!r}.')
            current = base
            for part in parts[:-1]:
                current /= part
                if current.exists() and (is_link(current) or not current.is_dir()):
                    raise RebuildError(f'Unsafe archive parent: {name!r}.')
                current.mkdir(exist_ok=True)
            if target.exists() and is_link(target):
                raise RebuildError(f'Unsafe archive destination: {name!r}.')
            if member.isdir():
                target.mkdir(exist_ok=True)
                continue
            with source.extractfile(member) as incoming, target.open('xb') as outgoing:
                copied = 0
                while chunk := incoming.read(min(1024 * 1024, member.size - copied)):
                    outgoing.write(chunk)
                    copied += len(chunk)
                if copied != member.size:
                    raise RebuildError(f'Truncated archive member: {name!r}.')
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def check_source_layout(directory, manifest):
    directory = Path(directory)
    for project in ('engine', 'yggdrasil'):
        for relative in ('go.mod', 'go.sum', 'vendor/modules.txt'):
            if not (directory / project / relative).is_file():
                raise RebuildError(f'Missing corresponding source file: {project}/{relative}.')
    for relative in ('build-info.json', 'MODIFICATIONS.txt'):
        if not (directory / relative).is_file():
            raise RebuildError(f'Missing source attribution: {relative}.')
    if not (directory / 'licenses').is_dir() or not any((directory / 'licenses').rglob('*')):
        raise RebuildError('The source archive has no licenses.')
    info = json.loads((directory / 'build-info.json').read_text(encoding='utf-8-sig'))
    if any(info.get(key) != manifest[key] for key in ('version', 'go_version')):
        raise RebuildError('Source build-info.json does not match the external manifest.')


def toolchain_environment():
    environment = os.environ.copy()
    environment.update({
        'CGO_ENABLED': '0', 'GOARCH': 'amd64', 'GOAMD64': 'v1',
        'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local',
        'GOWORK': 'off', 'GOENV': 'off', 'GOFLAGS': '', 'GOEXPERIMENT': '',
        'GOTELEMETRY': 'off', 'SOURCE_DATE_EPOCH': '0',
    })
    return environment


def run_go(arguments, *, cwd=None, env=None):
    try:
        return subprocess.run(arguments, cwd=cwd, env=env, check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (error.stderr or error.stdout or '') if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise RebuildError(f'Go command failed: {arguments[1]}\n{detail[-16000:]}') from error


def check_go(go, expected):
    environment = toolchain_environment()
    words = run_go([str(go), 'version'], env=environment).stdout.strip().split()
    if len(words) != 4 or words[:2] != ['go', 'version'] or words[2] != expected:
        raise RebuildError(f'The exact {expected} toolchain is required; got {" ".join(words)!r}.')


def build_binaries(go, directory, output, manifest):
    check_go(go, manifest['go_version'])
    version = manifest['version']
    engine_flags = f'-s -w -X github.com/sagernet/sing-box/constant.Version={version}'
    ygg_flags = ('-s -w -X github.com/yggdrasil-network/yggdrasil-go/src/version.buildName=MeshVPS '
                 f'-X github.com/yggdrasil-network/yggdrasil-go/src/version.buildVersion=0.5.15-meshvps.{version}')
    # Publish only once all six builds have succeeded. A failed toolchain cannot
    # replace a previously complete output set with a mixture of old/new binaries.
    with tempfile.TemporaryDirectory(prefix='meshvps-build-', dir=output) as temporary:
        staging = Path(temporary)
        for goos in ('windows', 'linux'):
            environment = toolchain_environment()
            environment['GOOS'] = goos
            for project, command in (('engine', 'sing-box'), ('yggdrasil', 'yggdrasil'), ('yggdrasil', 'yggdrasilctl')):
                name = 'meshvps-engine' if project == 'engine' else command
                name += '.exe' if goos == 'windows' else '-linux'
                arguments = [str(go), 'build', '-mod=vendor', '-trimpath', '-buildvcs=false', '-p=4']
                if project == 'engine':
                    arguments += ['-tags', ','.join(ENGINE_TAGS)]
                arguments += ['-ldflags', engine_flags if project == 'engine' else ygg_flags,
                              '-o', str(staging / name), './cmd/' + command]
                print(f'Building {name} offline...', flush=True)
                run_go(arguments, cwd=Path(directory) / project, env=environment)
        for name in BINARY_NAMES:
            os.replace(staging / name, Path(output) / name)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def attribution(directory, manifest):
    directory = Path(directory)
    sources = (f'MeshVPS {manifest["version"]}: corresponding network-tool sources\n'
               f'Source archive: {manifest["source_url"]}\n'
               f'SHA-256: {manifest["source_sha256"].lower()}\n'
               'Includes modifications, pinned dependencies and licenses for these builds.\n')
    result = {'SOURCES.txt': sources.encode('utf-8'), 'network-build.json': json_bytes(manifest)}
    for name in ('MODIFICATIONS.txt', 'build-info.json'):
        result[name] = (directory / name).read_bytes()
    for path in sorted((directory / 'licenses').rglob('*')):
        if path.is_file():
            result['licenses/' + path.relative_to(directory / 'licenses').as_posix()] = path.read_bytes()
    return result


def write_tar(stream, entries):
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, content, mode in sorted(entries, key=lambda entry: entry[0]):
            member = tarfile.TarInfo(name)
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ''
            member.mode = mode
            if isinstance(content, Path):
                member.size = content.stat().st_size
                with content.open('rb') as source:
                    archive.addfile(member, source)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))


def write_gzip_tar(path, entries):
    with Path(path).open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream,
                                                     mtime=0, compresslevel=9) as compressed:
        write_tar(compressed, entries)


def write_ar(path, entries):
    with Path(path).open('wb') as archive:
        archive.write(b'!<arch>\n')
        for name, content in entries:
            size = content.stat().st_size if isinstance(content, Path) else len(content)
            if len(name) > 15 or size >= 10**10:
                raise RebuildError('Debian archive member exceeds the ar format limits.')
            header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{size:<10}`\n'.encode('ascii')
            archive.write(header)
            if isinstance(content, Path):
                with content.open('rb') as source:
                    shutil.copyfileobj(source, archive)
            else:
                archive.write(content)
            if size % 2:
                archive.write(b'\n')


def package_binaries(directory, output, manifest, package_only=False):
    output = Path(output)
    for name in BINARY_NAMES:
        binary = output / name
        if not binary.is_file() or is_link(binary) or binary.stat().st_size == 0:
            raise RebuildError(f'Missing, empty or linked binary: {name}.')
    notices = attribution(directory, manifest)
    prefix = f'meshvps-engine-{manifest["version"]}-linux-amd64/'
    control = ('Package: meshvps-yggdrasil\n'
               f'Version: 0.5.15+meshvps{manifest["version"]}\n'
               'Section: net\nPriority: optional\nArchitecture: amd64\n'
               'Maintainer: MeshVPS contributors\n'
               'Description: MeshVPS build of Yggdrasil overlay tools\n'
               ' Built from the accompanying corresponding sources with vendored dependencies.\n').encode('utf-8')
    with tempfile.TemporaryDirectory(prefix='meshvps-pack-', dir=output) as temporary:
        staging = Path(temporary)
        engine_archive = staging / 'meshvps-engine-linux.tar.gz'
        write_gzip_tar(engine_archive, [(prefix + 'meshvps-engine', output / 'meshvps-engine-linux', 0o755)] +
                       [(prefix + name, data, 0o644) for name, data in notices.items()])
        control_archive = staging / 'control.tar.gz'
        write_gzip_tar(control_archive, [('control', control, 0o644)])
        data_archive = staging / 'data.tar.xz'
        data_entries = [('usr/bin/yggdrasil', output / 'yggdrasil-linux', 0o755),
                        ('usr/bin/yggdrasilctl', output / 'yggdrasilctl-linux', 0o755)]
        data_entries += [('usr/share/doc/meshvps-yggdrasil/' + name, data, 0o644) for name, data in notices.items()]
        with lzma.open(data_archive, 'wb', preset=9) as compressed:
            write_tar(compressed, data_entries)
        debian_archive = staging / 'yggdrasil-linux.deb'
        write_ar(debian_archive, [('debian-binary', b'2.0\n'), ('control.tar.gz', control_archive), ('data.tar.xz', data_archive)])
        for path in (engine_archive, debian_archive):
            os.replace(path, output / path.name)
    names = [*BINARY_NAMES, 'meshvps-engine-linux.tar.gz', 'yggdrasil-linux.deb']
    report = {key: manifest[key] for key in ('version', 'go_version', 'source_sha256', 'source_url')}
    report['package_only'] = package_only
    report['sha256'] = {name: sha256(output / name) for name in names}
    (output / 'network-rebuild.json').write_bytes(json_bytes(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', default='go', help='Path to the exact Go toolchain executable.')
    assets = default_assets()
    parser.add_argument('--sources', type=Path, default=assets / 'network-sources.tar.xz')
    parser.add_argument('--manifest', type=Path, default=assets / 'network-build.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/generated/network-tools')
    parser.add_argument('--package-only', action='store_true', help='Package all six binaries already in --output; Go is not used.')
    arguments = parser.parse_args(argv)
    try:
        manifest = read_manifest(arguments.manifest)
        verify_sources(arguments.sources, manifest)
        arguments.output = arguments.output.resolve()
        arguments.output.mkdir(parents=True, exist_ok=True)
        go = str(Path(arguments.go).resolve()) if Path(arguments.go).exists() else arguments.go
        with tempfile.TemporaryDirectory(prefix='meshvps-network-sources-') as temporary:
            safe_extract(arguments.sources, temporary)
            check_source_layout(temporary, manifest)
            if not arguments.package_only:
                build_binaries(go, temporary, arguments.output, manifest)
            report = package_binaries(temporary, arguments.output, manifest, arguments.package_only)
        print(f'Network tools packaged in {arguments.output}; verified source SHA-256 {report["source_sha256"]}.')
        return 0
    except (RebuildError, OSError, ValueError, tarfile.TarError, lzma.LZMAError) as error:
        print(f'Network rebuild failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
