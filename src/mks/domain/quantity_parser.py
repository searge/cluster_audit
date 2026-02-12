"""Shared parsers for Kubernetes resource quantity strings."""


def parse_cpu(cpu_str: str) -> int:
    """Parse CPU quantity and return millicores."""
    if not cpu_str or cpu_str in {"0", "<none>"}:
        return 0

    value = str(cpu_str).lower().strip()
    if value.endswith("m"):
        return int(float(value[:-1]))
    if value.endswith("u"):
        return int(float(value[:-1]) / 1000)
    if value.endswith("n"):
        return int(float(value[:-1]) / 1_000_000)
    return int(float(value) * 1000)


def parse_memory(memory_str: str) -> int:
    """Parse memory quantity and return bytes."""
    if not memory_str or memory_str in {"0", "<none>"}:
        return 0

    value = str(memory_str).upper().strip()
    multipliers = {
        "KI": 1024,
        "K": 1000,
        "MI": 1024**2,
        "M": 1000**2,
        "GI": 1024**3,
        "G": 1000**3,
        "TI": 1024**4,
        "T": 1000**4,
    }

    for suffix, multiplier in multipliers.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * multiplier)

    return int(value) if value.isdigit() else 0
