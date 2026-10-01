from cryptography.fernet import Fernet

from backend.security import Store


def make_store() -> Store:
    return Store(":memory:", Fernet.generate_key().decode())


def test_resources_and_history_are_tenant_scoped() -> None:
    store = make_store()
    alice = store.register_user("alice2", "a" * 12)
    bob = store.register_user("bobby2", "b" * 12)
    resource_id = store.put_resource(
        "alice-site", "health_check", "https://example.com", None,
        owner_user_id=alice["id"],
    )
    store.record_check(resource_id, {"ok": True, "latency_ms": 1})

    assert store.resources(alice)
    assert store.resources(bob) == []
    assert store.resource(resource_id, bob) is None
    assert store.history(resource_id, user=bob) == []
    assert store.summary(resource_id, user=bob)["total_checks"] == 0


def test_approval_cannot_be_read_across_tenants() -> None:
    store = make_store()
    alice = store.register_user("alice3", "a" * 12)
    bob = store.register_user("bobby3", "b" * 12)
    resource_id = store.put_resource(
        "alice-site-3", "health_check", "https://example.com", None,
        owner_user_id=alice["id"],
    )
    approval = store.create_approval("disable_monitor", resource_id, "alice3", "viewer")

    assert store.approval(approval["id"], alice) is not None
    assert store.approval(approval["id"], bob) is None
