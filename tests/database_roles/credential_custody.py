"""Disposable Linux custody check using *existing* distinct non-root UIDs."""
import argparse
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-uid', type=int, required=True)
    parser.add_argument('--runtime-uid', type=int, required=True)
    args = parser.parse_args()
    owner, runtime = map(pwd.getpwuid, (args.owner_uid, args.runtime_uid))
    if os.geteuid() or min(owner.pw_uid, runtime.pw_uid) <= 0 or owner.pw_uid == runtime.pw_uid:
        raise RuntimeError("Two existing distinct non-root identities and a local test supervisor are required")
    with tempfile.TemporaryDirectory(prefix='neoapps_custody_') as scratch:
        root = Path(scratch)
        root.chmod(0o711)
        paths = []
        for name, principal in [('operator', owner), ('web', runtime)]:
            home = root / name
            home.mkdir(mode=0o700)
            key = home / 'fixture-credential'
            key.write_bytes(secrets.token_bytes(32))
            key.chmod(0o600)
            os.chown(key, principal.pw_uid, principal.pw_gid)
            os.chown(home, principal.pw_uid, principal.pw_gid)
            paths.append(key)
        def check(principal, source, *arguments):
            subprocess.run([sys.executable, '-I', '-c', source, *map(str, arguments)],
                           user=principal.pw_uid, group=principal.pw_gid, extra_groups=[],
                           env={'HOME':str(arguments[0].parent)}, cwd='/', timeout=10, check=True)
        own = "import os,sys; assert len(open(sys.argv[1],'rb').read())==32; assert not os.getgroups()"
        check(owner, own, paths[0])
        check(runtime, own + """
assert 'MIGRATION_DATABASE_URL' not in os.environ
try:
    open(sys.argv[2],'rb')
except PermissionError:
    pass
else:
    raise AssertionError('Web UID could read the operator credential')
""", paths[1], paths[0])
    print("PASS: separate non-root identities read their own fixtures; web UID denied operator credential")


if __name__ == '__main__':
    main()
