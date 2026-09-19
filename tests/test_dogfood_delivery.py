from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import dogfood_application, dogfood_roadmap


class DogfoodDeliveryEvidenceTests(unittest.TestCase):
    def _repo(self, root: Path, *, readiness: str) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        spine_id = "dogfood-358-g1-test"
        dogfood_roadmap.save_projection(
            repo,
            {
                "version": dogfood_roadmap.PROJECTION_VERSION,
                "spines": [
                    {
                        "id": spine_id,
                        "parent_issue": 358,
                        "parent_url": "https://github.test/owner/repo/issues/358",
                        "status": dogfood_roadmap.AWAITING_LEARNING,
                        "readiness": readiness,
                        "delivery": {
                            "state": dogfood_roadmap.DELIVERY_NOT_RECORDED,
                            "evidence": [],
                        },
                        "learning": {
                            "state": (
                                dogfood_roadmap.LEARNING_PENDING
                                if readiness == dogfood_roadmap.DOGFOODABLE
                                else dogfood_roadmap.LEARNING_NOT_READY
                            ),
                            "assumptions": dogfood_roadmap.ASSUMPTIONS_UNVALIDATED,
                            "finding_ids": [],
                            "recursive_mvp_required": False,
                        },
                        "slices": [],
                    }
                ],
            },
        )
        return repo, spine_id

    @staticmethod
    def _delivery() -> dict[str, str]:
        return {
            "kind": "snapshot",
            "source_identity": "git:abc123",
            "artifact_identity": "snapshot-manifest:run-42",
            "reference": "https://github.test/actions/runs/42",
            "notes": "Installable Android dogfood APK",
        }

    def test_delivery_requires_dogfood_ready_spine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, spine_id = self._repo(
                Path(temp_dir),
                readiness=dogfood_roadmap.BUILDING,
            )
            with self.assertRaisesRegex(
                dogfood_roadmap.DogfoodRoadmapError,
                "dogfoodable",
            ):
                dogfood_application.record_delivery(
                    repo,
                    spine_id,
                    self._delivery(),
                )

    def test_delivery_is_durable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, spine_id = self._repo(
                Path(temp_dir),
                readiness=dogfood_roadmap.DOGFOODABLE,
            )
            first = dogfood_application.record_delivery(
                repo,
                spine_id,
                self._delivery(),
            )
            second = dogfood_application.record_delivery(
                repo,
                spine_id,
                self._delivery(),
            )

        self.assertEqual(
            first["delivery"]["state"],
            dogfood_roadmap.DELIVERY_RECORDED,
        )
        self.assertEqual(len(first["delivery"]["evidence"]), 1)
        self.assertEqual(len(second["delivery"]["evidence"]), 1)
        self.assertEqual(
            second["delivery"]["evidence"][0]["source_identity"],
            "git:abc123",
        )

    def test_learning_requires_delivery_and_retains_delivery_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, spine_id = self._repo(
                Path(temp_dir),
                readiness=dogfood_roadmap.DOGFOODABLE,
            )
            learning = {
                "assumptions": dogfood_roadmap.ASSUMPTIONS_VALIDATED,
                "findings": [],
                "supersede_issue_ids": [],
                "recursive_mvp_required": False,
                "notes": "Core loop exercised successfully.",
            }
            with self.assertRaisesRegex(
                dogfood_roadmap.DogfoodRoadmapError,
                "delivery evidence",
            ):
                dogfood_application.record_learning(repo, spine_id, learning)

            delivered = dogfood_application.record_delivery(
                repo,
                spine_id,
                self._delivery(),
            )
            evidence_id = delivered["delivery"]["evidence"][0]["id"]
            reconciled = dogfood_application.record_learning(
                repo,
                spine_id,
                learning,
            )

        self.assertEqual(
            reconciled["learning"]["state"],
            dogfood_roadmap.LEARNING_RECONCILED,
        )
        self.assertEqual(
            reconciled["learning"]["delivery_evidence_ids"],
            [evidence_id],
        )
        self.assertEqual(
            reconciled["status"],
            dogfood_roadmap.RECONCILED,
        )


if __name__ == "__main__":
    unittest.main()
