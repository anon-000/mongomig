import mongomig
from mongomig import MigrationContext


def test_every_migration_applies_and_reverts(db):
    """The whole history runs on an empty database, and back down to base."""
    mongomig.upgrade(yes=True)
    assert mongomig.current_state().pending == []
    mongomig.downgrade("base")
    assert mongomig.current_state().applied_heads == []


def test_split_customer_names(db):
    """One migration's data logic, on hand-made documents."""
    db.orders.insert_many([
        {"total": 10, "full_name": "Ada Lovelace"},
        {"total": 20, "full_name": "Plato"},
        {"total": 30, "customer": {"first": "Already", "last": "Migrated"}},
    ])
    migration = mongomig.load_revision("split_customer_names")
    migration.upgrade(MigrationContext(db))

    assert db.orders.find_one({"total": 10})["customer"] == {"first": "Ada", "last": "Lovelace"}
    assert db.orders.find_one({"total": 20})["customer"] == {"first": "Plato", "last": ""}
    assert db.orders.count_documents({"full_name": {"$exists": True}}) == 0
