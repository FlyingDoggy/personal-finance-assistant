"""Independent restore rejection checks using small synthetic backups only."""
import json
from pathlib import Path
import zipfile

import pytest

from app import maintenance
from app.domain import DomainError
from app.storage import Store


def package_at(tmp_path):
    store = Store(tmp_path / 'source')
    result = maintenance.create_backup(store)
    return store, store.directory / 'backups' / (result['id'] + '.zip')


@pytest.mark.parametrize('bad_manifest', ['array', 'missing_id', 'invalid_id'])
def test_invalid_manifest_is_rejected_before_publishing_destination(tmp_path, bad_manifest):
    store, package = package_at(tmp_path)
    altered = tmp_path / 'altered.zip'
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(altered, 'w') as target:
        manifest = json.loads(source.read('manifest.json'))
        if bad_manifest == 'array':
            manifest = []
        elif bad_manifest == 'missing_id':
            del manifest['id']
        else:
            manifest['id'] = '../../outside'
        for name in source.namelist():
            target.writestr(name, json.dumps(manifest) if name == 'manifest.json' else source.read(name))
    destination = tmp_path / 'restored'
    with pytest.raises(DomainError) as caught:
        maintenance.restore_backup(altered, destination)
    assert caught.value.code == 'invalid_backup'
    assert not destination.exists()
    assert store.path.exists()


def test_dangling_destination_symlink_is_never_replaced(tmp_path):
    _, package = package_at(tmp_path)
    destination = tmp_path / 'restored'
    destination.symlink_to(tmp_path / 'nonexistent-ledger', target_is_directory=True)
    with pytest.raises(DomainError) as caught:
        maintenance.restore_backup(package, destination)
    assert caught.value.code == 'restore_target_exists'
    assert destination.is_symlink()
    assert destination.readlink() == tmp_path / 'nonexistent-ledger'


def test_target_created_during_validation_is_not_replaced(tmp_path, monkeypatch):
    _, package = package_at(tmp_path)
    destination = tmp_path / 'restored'
    real_inspect = maintenance._inspect

    def competing_directory(path):
        result = real_inspect(path)
        destination.mkdir()
        (destination / 'keep.txt').write_text('synthetic existing target')
        return result

    monkeypatch.setattr(maintenance, '_inspect', competing_directory)
    with pytest.raises(DomainError) as caught:
        maintenance.restore_backup(package, destination)
    assert caught.value.code == 'restore_target_exists'
    assert (destination / 'keep.txt').read_text() == 'synthetic existing target'
    assert list(destination.iterdir()) == [destination / 'keep.txt']


def test_backup_cannot_succeed_when_its_expanded_size_exceeds_restore_limit(tmp_path, monkeypatch):
    store = Store(tmp_path / 'source')
    monkeypatch.setattr(maintenance, 'MAX_PACKAGE', 1)
    with pytest.raises(DomainError):
        maintenance.create_backup(store)
    assert not list((store.directory / 'backups').glob('*.zip'))
    assert not list((store.directory / 'backups').glob('*.json'))
