from unittest.mock import AsyncMock

import pytest
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import ConfigMap

from sync.kubernetes import job_from_cronjob, publish_schemas, schemas_script
from tests.helpers.dbos import cronjob


class TestJobFromCronjob:
    def test_names_the_job_and_marks_it_as_a_manual_run(self) -> None:
        """Name the Job, and annotate it as kubectl create job --from=cronjob does."""
        job = job_from_cronjob(cronjob(), "provision-ab12")

        assert job.metadata is not None
        assert job.metadata.name == "provision-ab12"
        assert job.metadata.annotations == {
            "cronjob.kubernetes.io/instantiate": "manual"
        }

    def test_makes_the_cronjob_an_owner_but_not_the_controller(self) -> None:
        """Delete the Job together with its CronJob, and keep the CronJob controller from reporting it as UnexpectedJob."""
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
            False,
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


class TestSchemasScript:
    """The script that the Swagger UI page loads to list the schemas."""

    @pytest.mark.parametrize(
        ("schemas", "expected"),
        [
            pytest.param([], "window.dataProxySchemas = [];\n", id="no-schema"),
            pytest.param(["a"], 'window.dataProxySchemas = ["a"];\n', id="one"),
            pytest.param(
                ["b", "a"],
                'window.dataProxySchemas = ["b", "a"];\n',
                id="keeps-the-order",
            ),
            pytest.param(
                ['a"b'],
                'window.dataProxySchemas = ["a\\"b"];\n',
                id="escapes-a-quote",
            ),
        ],
    )
    def test_writes_the_names_as_a_javascript_list(
        self, schemas: list[str], expected: str
    ) -> None:
        """Make the same text that the chart makes for its seed."""
        assert schemas_script(schemas) == expected


class TestPublishSchemas:
    async def test_patches_the_key_of_the_configmap(self) -> None:
        """Replace the list of the page and nothing else of the ConfigMap."""
        client = AsyncMock()

        await publish_schemas(client, "release-swagger-ui-schemas", ["a", "b"])

        client.patch.assert_awaited_once_with(
            ConfigMap,
            "release-swagger-ui-schemas",
            {"data": {"schemas.js": 'window.dataProxySchemas = ["a", "b"];\n'}},
        )
