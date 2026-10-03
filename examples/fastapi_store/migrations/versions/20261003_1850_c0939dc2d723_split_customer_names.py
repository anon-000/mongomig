"""split customer names

Revision: c0939dc2d723
Revises: 7b3c14f5cbef
Created: 2026-10-03 18:50:54 UTC
"""

revision = "c0939dc2d723"
down_revision = "7b3c14f5cbef"
branch_labels = None
depends_on = None
reversible = True
snapshot_hash = "sha256:b1caa29404c7019e1baeb1a3d90ae141537bcd65196bc5c4e58fdba667ace3e2"
mongomig_format = 1


def upgrade(ctx):
    orders = ctx.collection("orders")
    for batch in ctx.batches("orders", {"full_name": {"$exists": True}}, projection={"full_name": 1}):
        for order in batch:
            first, _, last = order["full_name"].partition(" ")
            orders.update_one(
                {"_id": order["_id"]},
                {"$set": {"customer": {"first": first, "last": last}}, "$unset": {"full_name": ""}},
            )


def downgrade(ctx):
    orders = ctx.collection("orders")
    for batch in ctx.batches("orders", {"customer": {"$exists": True}}, projection={"customer": 1}):
        for order in batch:
            name = f"{order['customer']['first']} {order['customer']['last']}".strip()
            orders.update_one(
                {"_id": order["_id"]}, {"$set": {"full_name": name}, "$unset": {"customer": ""}}
            )
