"""Turning reserved capacity into euros.

OVH bills the cluster, never the namespace, so nothing here measures a cost. It
prices one: what a reserved core is worth at the rate of the *standing* node
pool, applied to what each namespace reserves. Pure arithmetic, no IO.

The standing pool is the one with a monthly forfait; a flavour sold only by the
hour is overflow capacity. That distinction is the whole basis of this module,
so it is worth stating why. Every long-lived workload is meant to live on the
standing pool, and 108 pods on this cluster say so with an explicit
`nodeSelector`. Nothing is pinned to the hourly pool. Pods land there when the
scheduler cannot fit them anywhere else, which makes that pool a symptom of
over-reservation rather than a separate kind of capacity: it held ten nodes one
day and three the next, tracking nothing but how much did not fit.

Blending both pools into one average was the earlier design and it had a defect
that only showed under movement. The average is weighted by fleet composition,
so when seven hourly nodes went away the figure moved from 10.36 to 11.64 and
every namespace's euro column rose by twelve percent on a day none of them had
changed anything. The number has to answer to tenant behaviour, not to how many
nodes were running when the snapshot was taken.

Pricing against the standing pool removes that entirely, because the node count
cancels: fifteen b2-15 nodes cost 15 x 48.05 and carry 15 x 3.830 cores, so the
rate is 48.05 / 3.830 whether the pool holds fifteen nodes or forty. What is
left is a property of the flavour. It moves when OVH reprices b2-15, when the
pool changes flavour, or when a node's allocatable CPU changes because something
altered the kubelet's reservations, and `allocatable_spread` exists so that last
one leaves a trace instead of shifting the rate in silence.

The consequence to be honest about: this is a rate, not a share of the invoice.
Reservations summed at the standing rate can exceed the whole node bill, and on
this cluster they do, because 81.5 cores are reserved against a standing pool
that has 57.4. That excess is not an arithmetic error to be normalised away. It
is the diagnosis, and the overflow pool's bill is what it costs.

Money is `Decimal` throughout and never `float`. Binary floating point cannot
hold 48.05 or 0.086 exactly, and the error survives into the rounding: `4.35 *
100` evaluates to 434.99999999999994, so `floor` returns 434 where the arithmetic
says 435. Postgres stores these columns as `numeric` and its `floor()` is exact,
so a float pipeline would quietly disagree with the dashboard by a euro with
nothing to show for it. Inputs cross into `Decimal` at the boundary through
`money()`, which goes via `str` so a price written as 48.05 comes back as
Decimal("48.05") rather than the float that approximates it.

CPU is the divisor because CPU is what runs out first on this cluster, and the
resource that runs out first is the one that triggers the next node purchase.
`binding_resource` exists to make that assumption checkable rather than
implicit: when memory overtakes CPU, the basis is wrong and should change.

One limit worth knowing before quoting anything from here: only compute is
counted. Storage is priced separately below, but load balancers and egress sit
outside this model entirely.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

# OVH bills hourly pools by the hour; 730 is the conventional month used in
# their own estimates, so the overflow line stays comparable to the standing
# one. It overstates a pool that spends part of the month scaled down, which is
# most of what an overflow pool does — the figure is an upper bound on what not
# fitting cost, not a reading of the invoice.
HOURS_PER_MONTH = Decimal(730)

# Enough places to keep a per-core price meaningful for a fleet of any size
# without pretending to precision the invoice does not have.
_PRICE_PLACES = Decimal("0.0001")
_EURO = Decimal(1)


def money(value: float | int | str | Decimal | None) -> Decimal | None:
    """Bring a number in from the outside world as an exact decimal.

    Via `str` deliberately. `Decimal(48.05)` captures the binary approximation
    and carries it forward; `Decimal(str(48.05))` recovers the figure that was
    written in the price file, which is the one the invoice uses.
    """
    if value is None:
        return None
    return Decimal(str(value))


@dataclass(frozen=True)
class NodeFlavour:
    """One flavour's presence in the fleet: how many nodes, and their price."""

    name: str
    count: int
    cores: Decimal  # allocatable, summed across this flavour's nodes
    monthly_eur: Decimal | None
    hourly_eur: Decimal | None

    @property
    def is_standing(self) -> bool:
        """Whether this flavour is standing capacity rather than overflow.

        A monthly forfait is the marker. It is a commitment: the node is paid
        for whether or not anything runs on it, which is what makes it the pool
        long-lived workloads belong on and the honest rate to price a permanent
        reservation against. A flavour sold only by the hour is rented for as
        long as it is needed and released, so its price says what a burst cost,
        not what a standing core is worth.

        Read from the price file rather than from the pool name, so a pool
        renamed or a flavour that gains a forfait is picked up without a code
        change. The file's comments carry the pairing.
        """
        return self.monthly_eur is not None

    @property
    def monthly_total_eur(self) -> Decimal | None:
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
class CostBasis:  # pylint: disable=too-many-instance-attributes
    """What a reserved core costs per month, and whether that is a fair basis."""

    eur_per_core_month: Decimal  # the standing rate: standing bill / standing cores
    node_bill_eur_month: Decimal  # whole priced fleet, for reconciling the invoice
    standing_cores: Decimal
    standing_nodes: int
    standing_bill_eur_month: Decimal
    overflow_nodes: int
    overflow_bill_eur_month: Decimal
    unpriced_nodes: int
    binding_resource: str  # "cpu" or "memory": whichever is closer to full
    # Block storage is billed per gigabyte and independently of compute, so it
    # needs no allocation at all: a claim belongs to exactly one namespace.
    eur_per_gib_month: Decimal | None = None

    @property
    def priced_nodes(self) -> int:
        """Nodes this basis could put a price on, of either kind."""
        return self.standing_nodes + self.overflow_nodes

    def allocate(self, reserved_cores: float | Decimal) -> Decimal:
        """Cost attributed to a reservation, rounded down to whole euros.

        A figure someone wants to dispute should be one they can only revise
        upwards. ROUND_FLOOR, not truncation: the two disagree on negatives, and
        negatives are ordinary here since any namespace using more than it
        reserved produces one. Postgres applies `floor()` to the same product,
        and the two must not drift apart.
        """
        cores = money(reserved_cores) or Decimal(0)
        return (cores * self.eur_per_core_month).quantize(_EURO, rounding=ROUND_FLOOR)

    def allocate_storage(self, gib: float | Decimal) -> Decimal | None:
        """Cost of claimed block storage, rounded down. None when unpriced.

        Not a rate like the CPU figure: a volume is billed per gigabyte and
        belongs to one namespace, so this is the closest thing here to an actual
        cost rather than a price applied to a reservation.
        """
        if self.eur_per_gib_month is None:
            return None
        size = money(gib) or Decimal(0)
        return (size * self.eur_per_gib_month).quantize(_EURO, rounding=ROUND_FLOOR)


def build_cost_basis(
    flavours: list[NodeFlavour],
    cpu_reserved_ratio: float,
    memory_reserved_ratio: float,
    eur_per_gib_month: Decimal | None = None,
) -> CostBasis | None:
    """Derive the standing per-core rate, or None when nothing can be priced.

    Both the bill and the divisor come from standing flavours only, so the rate
    is a property of the flavour rather than of the fleet: adding or removing
    nodes of that flavour scales the numerator and the denominator together and
    leaves it unchanged. Overflow nodes are counted and billed, but kept out of
    the division, because their number tracks how much did not fit and folding
    that into the rate would move every tenant's figure for reasons no tenant
    caused.

    Returns None when no flavour carries a monthly forfait. That is a hard stop
    rather than a quiet fall back to the fleet average: a cluster with no
    standing pool, or a price file that lost its forfait, is a change worth
    failing on rather than absorbing into a number nobody would think to check.
    """
    standing_bill = overflow_bill = standing_cores = Decimal(0)
    standing = overflow = unpriced = 0
    for flavour in flavours:
        total = flavour.monthly_total_eur
        if total is None:
            unpriced += flavour.count
        elif flavour.is_standing:
            standing_bill += total
            standing_cores += flavour.cores
            standing += flavour.count
        else:
            overflow_bill += total
            overflow += flavour.count

    if standing == 0 or standing_cores <= 0:
        return None

    return CostBasis(
        eur_per_core_month=(standing_bill / standing_cores).quantize(_PRICE_PLACES),
        node_bill_eur_month=standing_bill + overflow_bill,
        standing_cores=standing_cores,
        standing_nodes=standing,
        standing_bill_eur_month=standing_bill,
        overflow_nodes=overflow,
        overflow_bill_eur_month=overflow_bill,
        unpriced_nodes=unpriced,
        binding_resource=(
            "cpu" if cpu_reserved_ratio >= memory_reserved_ratio else "memory"
        ),
        eur_per_gib_month=eur_per_gib_month,
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


def allocatable_spread(
    nodes: list[dict[str, Any]], learned: dict[str, str]
) -> dict[str, list[Decimal]]:
    """Distinct allocatable-core counts seen per flavour, for drift detection.

    The standing rate divides a fixed price by the cores one node of that
    flavour hands out, so that per-node figure is half the answer and nothing
    else in the pipeline would notice it changing. All fifteen b2-15 nodes here
    report exactly 3830m, but allocatable is capacity minus whatever the kubelet
    and system reserve, and those reservations are configuration: an OVH image
    change or a Kubernetes upgrade can move them. The rate would shift with no
    price having changed and no error anywhere.

    Returns every distinct value per flavour so the caller can say something
    when a flavour reports more than one. Mid-roll a mixed fleet is expected and
    harmless; the same mixture still there next week is not.
    """
    seen: dict[str, set[Decimal]] = {}
    for node in nodes:
        name = flavour_of(node, learned)
        if name is None:
            continue
        cores = money(node.get("cpu"))
        if cores is not None:
            seen.setdefault(name, set()).add(cores)
    return {name: sorted(values) for name, values in seen.items()}


__all__ = [
    "HOURS_PER_MONTH",
    "CostBasis",
    "NodeFlavour",
    "allocatable_spread",
    "build_cost_basis",
    "flavour_of",
    "money",
    "pool_flavours",
]
