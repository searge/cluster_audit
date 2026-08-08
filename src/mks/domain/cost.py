"""Turning reserved capacity into euros.

OVH bills the cluster, never the namespace, so nothing here measures a cost.
It allocates one: the node bill divided by the CPU those nodes can hand out,
applied to what each namespace reserves. Pure arithmetic, no IO.

The divisor is CPU because CPU is what runs out first on this cluster, and the
resource that runs out first is the one that triggers the next node purchase.
`binding_resource` exists to make that assumption checkable rather than
implicit: when memory overtakes CPU, the basis is wrong and should change.

Two limits worth knowing before quoting anything from here. Hourly pools are
priced at a full month of hours, so an autoscaled pool that spends half its life
scaled down is billed as though it never was. And only compute is counted:
storage, load balancers and egress sit outside this model entirely.
"""

import math
from dataclasses import dataclass
from typing import Any

# OVH bills hourly pools by the hour; 730 is the conventional month used in
# their own estimates, so the two pools stay comparable.
HOURS_PER_MONTH = 730


@dataclass(frozen=True)
class NodeFlavour:
    """One flavour's presence in the fleet: how many nodes, and their price."""

    name: str
    count: int
    cores: float  # allocatable, summed across this flavour's nodes
    monthly_eur: float | None
    hourly_eur: float | None

    @property
    def monthly_total_eur(self) -> float | None:
        """Monthly cost for all nodes of this flavour, or None if unpriced.

        A monthly forfait wins over an hourly rate when a flavour has both,
        which is a guess: whether a pool is billed monthly or hourly is a
        property of the pool, not of the flavour, and Kubernetes carries no
        label for it. It holds for this cluster because the price reference
        lists a forfait only for the flavour whose pool is monthly. Adding a
        monthly price for an hourly-billed flavour would halve the bill without
        any error, so that pairing belongs in the price file's comments.
        """
        if self.monthly_eur is not None:
            return self.monthly_eur * self.count
        if self.hourly_eur is not None:
            return self.hourly_eur * HOURS_PER_MONTH * self.count
        return None


@dataclass(frozen=True)
class CostBasis:
    """What a reserved core costs per month, and whether that is a fair basis."""

    eur_per_core_month: float
    node_bill_eur_month: float
    allocatable_cores: float
    priced_nodes: int
    unpriced_nodes: int
    binding_resource: str  # "cpu" or "memory": whichever is closer to full

    def allocate(self, reserved_cores: float) -> float:
        """Cost attributed to a reservation, rounded down.

        A figure someone wants to dispute should be one they can only revise
        upwards. `math.floor`, not `int`: the two disagree on negative values,
        and negative values are ordinary here, since any namespace using more
        than it reserved produces one. The dashboard applies SQL `floor()` to
        the same quantity and the two must not drift apart.
        """
        return float(math.floor(reserved_cores * self.eur_per_core_month))


def build_cost_basis(
    flavours: list[NodeFlavour],
    cpu_reserved_ratio: float,
    memory_reserved_ratio: float,
) -> CostBasis | None:
    """Derive the per-core price, or None when nothing can be priced.

    Both the bill and the divisor come from priced nodes only. Dividing a
    partial bill by the whole fleet's cores would quietly deflate the price of
    every core: one node of an unlisted flavour would drop every namespace's
    figure by its share, leaving `unpriced_nodes` as the only trace. Excluding
    those nodes from both sides keeps the price honest for the part of the fleet
    that can actually be priced.
    """
    bill = 0.0
    priced_cores = 0.0
    priced = unpriced = 0
    for flavour in flavours:
        total = flavour.monthly_total_eur
        if total is None:
            unpriced += flavour.count
            continue
        bill += total
        priced_cores += flavour.cores
        priced += flavour.count

    if priced == 0 or priced_cores <= 0:
        return None

    return CostBasis(
        eur_per_core_month=bill / priced_cores,
        node_bill_eur_month=bill,
        allocatable_cores=priced_cores,
        priced_nodes=priced,
        unpriced_nodes=unpriced,
        binding_resource=(
            "cpu" if cpu_reserved_ratio >= memory_reserved_ratio else "memory"
        ),
    )


def pool_flavours(nodes: list[dict[str, Any]], known: frozenset[str]) -> dict[str, str]:
    """Learn which priced flavour each node pool runs, from the fleet itself.

    `instance-type` is the obvious source, and OVH sets it to the flavour name
    on ten of this cluster's twenty five nodes and to an opaque flavour UUID on
    the other fifteen, in the same pools, for identical hardware. Taking the
    label at face value priced ten nodes and understated the bill by two thirds.

    The gap is closed from observation rather than by guessing: inside a pool,
    the nodes that do carry a readable name say what the pool runs, and that
    name covers their unlabelled siblings. Nothing is inferred from how someone
    chose to name a pool, so a pool whose nodes all carry UUIDs stays unpriced
    and is reported as such, instead of being matched on a substring that might
    happen to appear in its name.
    """
    learned: dict[str, str] = {}
    for node in nodes:
        pool, flavour = node.get("pool"), node.get("flavour")
        if isinstance(pool, str) and isinstance(flavour, str) and flavour in known:
            learned[pool] = flavour
    return learned


def flavour_of(node: dict[str, Any], learned: dict[str, str]) -> str | None:
    """Name a node's priced flavour, or None when it cannot be established."""
    flavour = node.get("flavour")
    if isinstance(flavour, str) and flavour in learned.values():
        return flavour
    pool = node.get("pool")
    return learned.get(pool) if isinstance(pool, str) else None


__all__ = [
    "HOURS_PER_MONTH",
    "CostBasis",
    "NodeFlavour",
    "build_cost_basis",
    "flavour_of",
    "pool_flavours",
]
