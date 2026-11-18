"""Controlled workstation background entry point; no Render fitting or promotion.

May be invoked with --once by the existing workstation scheduling mechanism.
No scheduler is installed. Fresh authenticated control is checked before work
and before publication; a disabled/revised job never publishes its candidate.
"""
import argparse
import json
import re
from pathlib import Path

from scripts import frost_local as local
from scripts.frost_shadow_collect import exclusive
from app.services import frost_vault as vault
from app.services.frost_intelligence import VERSION


def control(remote):
    receipt = json.loads(remote.request('GET', '/neosubzero/frost-vault/intelligence-settings'))
    policy = receipt['settings']
    if receipt.get('ok') is not True or policy.get('schema') != VERSION or policy.get('automatic_retraining') is not True:
        raise ValueError('Automatic Retraining is OFF. No new training work or candidate publication is allowed.')
    return policy


def once(remote, directory, output, version):
    local.local_only()  # Never weaken the existing Render/web-request prohibition.
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', version):
        raise ValueError('Use a safe, unique research candidate version.')
    root = Path(output).resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        relative = root.relative_to(repository)
    except ValueError:
        relative = None
    if relative is not None and (not relative.parts or relative.parts[0] not in ('instance', '_local_archive')):
        raise ValueError('Use an ignored private output directory.')
    policy = control(remote)
    with exclusive(root):
        destination = root / (version + '.json')
        if destination.exists():
            raise ValueError('An immutable candidate already exists; nothing overwritten.')
        dataset = local.prepare(directory)
        if dataset['manifest'].get('gateway_id') != policy['gateway_id']:
            raise ValueError('Training dataset and authenticated gateway control do not match.')
        if control(remote) != policy:
            raise ValueError('FROST Intelligence changed; this training attempt stopped safely.')
        # Canonical seasonal, evidence, calibration and memory/domain guards
        # stay in the established workstation trainer. No duplicate fitter.
        candidate = local.train(dataset, version)
        if control(remote) != policy:
            raise ValueError('FROST Intelligence changed; in-flight result discarded without publishing.')
        candidate['training']['automatic_control'] = policy
        candidate['approved'] = False
        candidate['operationally_validated'] = False
        raw = vault.canonical(candidate)
        with destination.open('xb') as handle:
            handle.write(raw)
        return dict(path=str(destination), sha256=vault.digest(raw), approved=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', required=True, action='store_true')
    parser.add_argument('--once', required=True, action='store_true')
    parser.add_argument('--origin', required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args(argv)
    local.local_only()
    remote = local.Remote(args.origin, args.email)
    try:
        print(json.dumps(once(remote, args.directory, args.output, args.version)))
    finally:
        remote.close()


if __name__ == '__main__':
    main()
