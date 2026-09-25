"""Helper scripts shipped in src/."""

import os


def test_generate_vapid_pair_targets_repo_root_and_is_import_safe():
    import generateVapidPair  # must not prompt or generate keys on import
    root = os.path.dirname(os.path.dirname(os.path.realpath(generateVapidPair.__file__)))
    assert generateVapidPair.SECRETS_FILE == os.path.join(root, ".secrets.yaml")
    assert generateVapidPair.SUBSCRIPTIONS_FILE == os.path.join(root, ".subscriptions.json")


def test_generated_keys_work_with_the_notifier(tmp_path):
    import generateVapidPair
    from coop.services.notifications import load_vapid_keys
    public, private = generateVapidPair.generate_vapid_keys()
    path = str(tmp_path / ".secrets.yaml")
    generateVapidPair.dump_keys_to_yaml(public, private, filename=path)
    assert load_vapid_keys(path) == (public, private)
