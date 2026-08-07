"""Service dependency graph — the reference mirror of the Neo4j knowledge
graph (docs/06-retrieval-and-memory.md §6). Answers the blast-radius
question: "if this service degrades, what is transitively affected?"

Production ingests this from the service catalog and deploy topology; here
edges are declared directly. The query surface, not the storage, is the
design surface.
"""
from __future__ import annotations


class ServiceGraph:
    def __init__(self):
        self._dependencies: dict[str, set[str]] = {}   # service -> what it calls

    def add_dependency(self, service: str, depends_on: str) -> None:
        self._dependencies.setdefault(service, set()).add(depends_on)
        self._dependencies.setdefault(depends_on, set())

    def dependents(self, service: str) -> set[str]:
        """Transitive reverse dependencies: every service whose call path
        reaches `service`."""
        reverse: dict[str, set[str]] = {}
        for src, targets in self._dependencies.items():
            for target in targets:
                reverse.setdefault(target, set()).add(src)

        found: set[str] = set()
        frontier = [service]
        while frontier:
            current = frontier.pop()
            for dependent in reverse.get(current, ()):
                if dependent not in found:
                    found.add(dependent)
                    frontier.append(dependent)
        return found

    def blast_radius(self, service: str) -> int:
        return len(self.dependents(service))


def default_graph() -> ServiceGraph:
    """Demo topology for the golden scenarios."""
    graph = ServiceGraph()
    graph.add_dependency("checkout-service", "payments-service")
    graph.add_dependency("checkout-service", "search-api")
    graph.add_dependency("orders-service", "payments-service")
    graph.add_dependency("storefront-web", "checkout-service")
    graph.add_dependency("storefront-web", "search-api")
    graph.add_dependency("storefront-web", "orders-service")
    graph.add_dependency("mobile-bff", "orders-service")
    return graph
