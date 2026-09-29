from hermes.platform.civilization.delegation import route_task, record_task_result
from hermes.platform.observability.event_store import EventStore


def test_route_matches_registered_security_bot():
    store = EventStore()
    store.append(__import__('hermes.platform.observability.events', fromlist=['Event']).Event(
        name='bot.spec.registered', payload={'bot_id': 'security-bot', 'spec': {
            'id': 'security-bot', 'name': 'SecurityBot', 'capabilities': ['security'],
            'task_defaults': {}, 'policy': {}, 'version': 1, 'routines': [], 'triggers': []
        }}))
    task = route_task({'goal': 'audit firewall credentials'}, event_store=store)
    assert task['bot_id'] == 'security-bot'
    assert task['identity_version'] is None


def test_uncovered_domain_creates_versioned_bot_and_lifecycle_event():
    store = EventStore()
    task = route_task({'goal': 'design a postgres migration'}, event_store=store)
    assert task['bot_id'].endswith('-bot')
    assert task['identity_version'] == 1
    record_task_result(task, {'status': 'completed', 'summary': 'verified'}, event_store=store)
    names = [event.name for event in store.get_all()]
    assert 'civ.delegate.routed' in names
    assert 'civ.leaf.created' in names
    assert 'civ.leaf.completed' in names
