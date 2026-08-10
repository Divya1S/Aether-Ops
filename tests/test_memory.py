"""Episodic memory recall semantics (audit F11/F9): ties break toward
VERIFIED and RECENT precedent, not the oldest episode, and growth is bounded."""
import unittest

from aetherops.memory.store import EpisodicMemory

_EP = {"service": "s", "failure_class": "x", "summary": "pool oom cascade"}


class TestRecallTieBreak(unittest.TestCase):
    def test_verified_beats_older_unverified_at_equal_overlap(self):
        memory = EpisodicMemory()
        memory.add({**_EP, "verified": False})      # older, unverified
        memory.add({**_EP, "verified": True})       # newer, verified
        self.assertTrue(memory.search("pool oom cascade", k=1)[0]["verified"])

    def test_recency_breaks_ties_among_equally_verified(self):
        memory = EpisodicMemory()
        memory.add({**_EP, "verified": True})
        newer = memory.add({**_EP, "verified": True})
        self.assertEqual(memory.search("pool oom cascade", k=1)[0]["id"], newer)

    def test_max_episodes_bounds_growth(self):
        memory = EpisodicMemory(max_episodes=3)
        for i in range(10):
            memory.add({"service": "s", "failure_class": "x",
                        "summary": f"episode {i}"})
        self.assertEqual(len(memory), 3)


if __name__ == "__main__":
    unittest.main()
