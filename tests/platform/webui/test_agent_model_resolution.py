from hermes.platform.webui.agent_hierarchy import AgentHierarchyStore, HierarchyError


def test_valid_configured_model_provider_persists(tmp_path):
    path = tmp_path / 'hierarchy.json'
    store = AgentHierarchyStore(path)
    value = store.set_bot_model('a6api', 'deepseek-v4-flash')
    assert value == {'provider': 'a6api', 'model': 'deepseek-v4-flash'}
    assert AgentHierarchyStore(path).snapshot()['bot_model'] == value


def test_invalid_model_fails_closed_without_persistence(tmp_path):
    path = tmp_path / 'hierarchy.json'
    store = AgentHierarchyStore(path)
    store.set_bot_model('a6api', 'deepseek-v4-flash')
    before = path.read_text()
    try:
        store.set_bot_model('a6api', 'not-configured')
    except HierarchyError:
        pass
    else:
        raise AssertionError('invalid model accepted')
    assert path.read_text() == before
    assert store.snapshot()['bot_model']['model'] == 'deepseek-v4-flash'


def test_invalid_provider_for_profile_fails_closed(tmp_path):
    path = tmp_path / 'hierarchy.json'
    store = AgentHierarchyStore(path)
    with __import__('pytest').raises(HierarchyError):
        store.upsert_node({'id': 'bot', 'role': 'bot', 'provider': 'missing', 'profile': 'coding-primary'})
    assert not path.exists()
