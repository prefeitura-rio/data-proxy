import pytest
from lightkube.models.meta_v1 import ObjectMeta

from sync.kubernetes import job_from_cronjob
from tests.helpers import cronjob


class TestJobFromCronjob:
    """job_from_cronjob behavior tests."""

    def test_names_the_job_and_marks_it_as_a_manual_run(self) -> None:
        """Make the Job that kubectl create job --from=cronjob makes."""
        job = job_from_cronjob(cronjob(), "provision-ab12")

        assert job.metadata is not None
        assert job.metadata.name == "provision-ab12"
        assert job.metadata.annotations == {
            "cronjob.kubernetes.io/instantiate": "manual"
        }

    def test_makes_the_cronjob_the_controller_of_the_job(self) -> None:
        """Delete the Job together with its CronJob."""
        job = job_from_cronjob(cronjob(), "provision-ab12")

        assert job.metadata is not None
        assert job.metadata.ownerReferences is not None
        [owner] = job.metadata.ownerReferences
        assert (
            owner.apiVersion,
            owner.kind,
            owner.name,
            owner.uid,
            owner.controller,
        ) == (
            "batch/v1",
            "CronJob",
            "provision",
            "uid-1",
            True,
        )

    def test_keeps_the_labels_of_the_job_template(self) -> None:
        """Carry the metadata of the template to the Job."""
        job = job_from_cronjob(
            cronjob(template_metadata=ObjectMeta(labels={"app": "provision"})),
            "provision-ab12",
        )

        assert job.metadata is not None
        assert job.metadata.labels == {"app": "provision"}

    def test_copies_the_pod_template_without_sharing_it(self) -> None:
        """Change nothing in the CronJob when the caller changes the Job."""
        source = cronjob()
        job = job_from_cronjob(source, "provision-ab12")

        assert job.spec is not None
        assert job.spec.template.spec is not None
        job.spec.template.spec.containers[0].image = "other:2"

        assert source.spec is not None
        assert source.spec.jobTemplate.spec is not None
        assert source.spec.jobTemplate.spec.template.spec is not None
        assert source.spec.jobTemplate.spec.template.spec.containers[0].image == "img:1"

    @pytest.mark.parametrize(
        ("metadata", "missing"),
        [
            pytest.param(ObjectMeta(uid="uid-1"), "name", id="no-name"),
            pytest.param(ObjectMeta(name="provision"), "uid", id="no-uid"),
        ],
    )
    def test_refuses_a_cronjob_that_the_api_returned_incomplete(
        self, metadata: ObjectMeta, missing: str
    ) -> None:
        """Name the field that the CronJob lacks."""
        with pytest.raises(ValueError, match=f"The CronJob has no {missing}"):
            job_from_cronjob(cronjob(metadata=metadata), "provision-ab12")
