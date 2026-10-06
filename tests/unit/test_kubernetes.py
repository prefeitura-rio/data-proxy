"""Behavior tests for the Kubernetes helpers."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from lightkube.core.exceptions import ApiError, ConditionError
from lightkube.models.apps_v1 import DeploymentStatus
from lightkube.models.batch_v1 import JobSpec
from lightkube.models.core_v1 import (
    Container,
    EmptyDirVolumeSource,
    PodCondition,
    PodSpec,
    PodStatus,
    PodTemplateSpec,
    Volume,
)
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta, Status
from lightkube.resources.batch_v1 import Job
from lightkube.resources.core_v1 import Pod
from lightkube.types import CascadeType

from data_proxy import kubernetes
from data_proxy.constants import POOLER_SELECTOR
from data_proxy.kubernetes import (
    build_refresh_job,
    is_pod_ready,
    is_rollout_complete,
    list_catalog_claims,
    list_deployments,
    restart_deployment,
    run_job,
)
from data_proxy.types import KubernetesClient
from tests.fixtures.types import FakeKubernetes
from tests.helpers import deployment

READY = DeploymentStatus(updatedReplicas=2, availableReplicas=2, observedGeneration=3)
METADATA = ObjectMeta(generation=3)


class TestRolloutComplete:
    """A rollout is complete only when every replica runs the new generation."""

    def test_accepts_a_finished_rollout(self) -> None:
        assert is_rollout_complete(
            deployment(status=READY, metadata=METADATA, replicas=2)
        )

    @pytest.mark.parametrize(
        ("status", "metadata", "replicas"),
        [
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=1, availableReplicas=2, observedGeneration=3
                ),
                METADATA,
                2,
                id="replica-not-updated",
            ),
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=2, availableReplicas=1, observedGeneration=3
                ),
                METADATA,
                2,
                id="replica-not-available",
            ),
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=2, availableReplicas=2, observedGeneration=2
                ),
                METADATA,
                2,
                id="generation-not-observed",
            ),
            pytest.param(None, METADATA, 2, id="missing-status"),
            pytest.param(READY, None, 2, id="missing-metadata"),
            pytest.param(READY, METADATA, None, id="missing-replicas"),
        ],
    )
    def test_rejects_an_unfinished_rollout(
        self,
        status: DeploymentStatus | None,
        metadata: ObjectMeta | None,
        replicas: int | None,
    ) -> None:
        assert not is_rollout_complete(
            deployment(status=status, metadata=metadata, replicas=replicas)
        )


class TestListDeployments:
    """Deployments are listed by label and returned in a stable order."""

    @pytest.mark.asyncio
    async def test_returns_the_sorted_names_for_the_labels(self) -> None:
        client = FakeKubernetes(
            [
                deployment(status=None, metadata=ObjectMeta(name=name), replicas=1)
                for name in ("pooler-ro", "pooler")
            ]
        )

        names = await list_deployments(client, "app", POOLER_SELECTOR)

        assert names == ["pooler", "pooler-ro"]
        assert client.list_labels == [POOLER_SELECTOR]


class TestRestartDeployment:
    """A restart patches the Deployment before it waits for the rollout."""

    @pytest.mark.asyncio
    async def test_patches_the_deployment_before_waiting_for_its_rollout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeKubernetes()
        events: list[str] = []

        async def wait(
            _client: KubernetesClient, _namespace: str, name: str, _timeout: float
        ) -> None:
            events.append(f"wait:{name}:patched={client.patched}")

        monkeypatch.setattr(kubernetes, "wait_for_rollout", wait)

        await restart_deployment(client, "app", "a", "2026-01-01T00:00:00Z", 5)

        assert events == ["wait:a:patched=['a']"]

    @pytest.mark.asyncio
    async def test_fails_at_once_when_the_stream_ends_before_the_rollout_completes(
        self,
    ) -> None:
        stuck = deployment(status=None, metadata=ObjectMeta(name="pgrst"), replicas=2)
        client = FakeKubernetes(watched=[stuck])

        with pytest.raises(TimeoutError, match="pgrst"):
            await restart_deployment(client, "app", "pgrst", "2026-01-01T00:00:00Z", 60)

    @pytest.mark.asyncio
    async def test_stops_waiting_on_a_hung_stream_when_the_timeout_expires(
        self,
    ) -> None:
        client = FakeKubernetes(hang_watch=True)

        with pytest.raises(TimeoutError, match="pgrst"):
            await restart_deployment(
                client, "app", "pgrst", "2026-01-01T00:00:00Z", 0.05
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [
            pytest.param(300, 300, id="whole-seconds"),
            pytest.param(0.05, 1, id="rounded-up"),
        ],
    )
    async def test_asks_the_server_to_end_the_stream_at_the_timeout(
        self, timeout: float, expected: int
    ) -> None:
        done = deployment(
            status=READY, metadata=ObjectMeta(name="pgrst", generation=3), replicas=2
        )
        client = FakeKubernetes(watched=[done])

        await restart_deployment(
            client, "app", "pgrst", "2026-01-01T00:00:00Z", timeout
        )

        assert client.watch_timeouts == [expected]

    @pytest.mark.asyncio
    async def test_finishes_when_the_watch_reports_a_complete_rollout(self) -> None:
        done = deployment(
            status=READY, metadata=ObjectMeta(name="pgrst", generation=3), replicas=2
        )
        client = FakeKubernetes(watched=[done])

        await restart_deployment(client, "app", "pgrst", "2026-01-01T00:00:00Z", 5)

        assert client.patched == ["pgrst"]


def pod(name: str, *, ready: bool = True, terminating: bool = False) -> Pod:
    """Return a PostgreSQL Pod with the given readiness."""
    return Pod(
        metadata=ObjectMeta(
            name=name,
            deletionTimestamp=datetime(2026, 1, 1, tzinfo=UTC) if terminating else None,
        ),
        status=PodStatus(
            conditions=[PodCondition(type="Ready", status="True" if ready else "False")]
        ),
    )


class TestPodReady:
    """A Pod is ready when it is not terminating and reports the Ready condition."""

    @pytest.mark.parametrize(
        ("candidate", "expected"),
        [
            pytest.param(pod("pg-1"), True, id="ready"),
            pytest.param(pod("pg-1", ready=False), False, id="not-ready"),
            pytest.param(pod("pg-1", terminating=True), False, id="terminating"),
            pytest.param(Pod(metadata=ObjectMeta(name="pg-1")), False, id="no-status"),
            pytest.param(
                Pod(metadata=ObjectMeta(name="pg-1"), status=PodStatus()),
                False,
                id="no-conditions",
            ),
            pytest.param(Pod(status=PodStatus()), False, id="no-metadata"),
        ],
    )
    def test_reads_the_ready_condition(self, candidate: Pod, expected: bool) -> None:
        assert is_pod_ready(candidate) is expected


class TestListCatalogClaims:
    """Only ready, live PostgreSQL Pods have a claim to refresh."""

    @pytest.mark.asyncio
    async def test_maps_ready_pods_to_their_ephemeral_claims(self) -> None:
        client = FakeKubernetes(
            [
                pod("data-proxy-2"),
                pod("data-proxy-1"),
                pod("data-proxy-3", ready=False),
                pod("data-proxy-4", terminating=True),
            ]
        )

        claims = await list_catalog_claims(client, "app")

        assert list(claims.items()) == [
            ("data-proxy-1", "data-proxy-1-ducklake-catalogs"),
            ("data-proxy-2", "data-proxy-2-ducklake-catalogs"),
        ]


def template_job() -> Job:
    """Return a refresh Job template as Kubernetes stores it, with generated fields."""
    generated = {
        "controller-uid": "uid-1",
        "job-name": "template",
        "batch.kubernetes.io/controller-uid": "uid-1",
        "batch.kubernetes.io/job-name": "template",
    }
    return Job(
        metadata=ObjectMeta(name="template", namespace="app"),
        spec=JobSpec(
            completions=0,
            selector=LabelSelector(matchLabels={"controller-uid": "uid-1"}),
            template=PodTemplateSpec(
                metadata=ObjectMeta(
                    labels={"app.kubernetes.io/name": "data-proxy", **generated}
                ),
                spec=PodSpec(
                    containers=[Container(name="refresh", image="litestream:test")],
                    volumes=[
                        Volume(
                            name="ducklake-catalogs", emptyDir=EmptyDirVolumeSource()
                        ),
                        Volume(name="litestream-config"),
                    ],
                ),
            ),
        ),
    )


class TestBuildRefreshJob:
    """A refresh Job is a copy of the template that the cluster can run for one instance."""

    @staticmethod
    def build(template: Job) -> Job:
        assert template.spec is not None
        return build_refresh_job(
            template.spec, "app", "test", "pg-1", "pg-1-claim", "refresh-test-pg-1-ab"
        )

    def test_names_and_labels_the_job_and_its_pod(self) -> None:
        job = self.build(template_job())

        labels = {
            "app.kubernetes.io/component": "refresh-catalog",
            "data-proxy.io/schema": "test",
            "data-proxy.io/instance": "pg-1",
        }
        assert job.metadata == ObjectMeta(
            name="refresh-test-pg-1-ab", namespace="app", labels=labels
        )
        assert job.spec is not None
        assert job.spec.template.metadata == ObjectMeta(labels=labels)

    def test_clears_the_generated_selector_and_runs_once(self) -> None:
        job = self.build(template_job())

        assert job.spec is not None
        assert job.spec.selector is None
        assert job.spec.completions == 1
        assert job.spec.ttlSecondsAfterFinished == 300

    def test_swaps_only_the_catalog_volume_for_the_claim(self) -> None:
        job = self.build(template_job())

        assert job.spec is not None
        assert job.spec.template.spec is not None
        volumes = {v.name: v for v in job.spec.template.spec.volumes or []}
        assert volumes["ducklake-catalogs"].emptyDir is None
        assert volumes["ducklake-catalogs"].persistentVolumeClaim is not None
        assert (
            volumes["ducklake-catalogs"].persistentVolumeClaim.claimName == "pg-1-claim"
        )
        assert volumes["litestream-config"] == Volume(name="litestream-config")

    def test_leaves_the_template_unchanged(self) -> None:
        template = template_job()
        original = deepcopy(template)

        self.build(template)

        assert template == original


class TestRunJob:
    """A refresh Job is a copy of the chart template for one instance volume."""

    @pytest.mark.asyncio
    async def test_creates_a_runnable_copy_for_the_instance_claim(self) -> None:
        template = template_job()
        original = deepcopy(template)
        client = FakeKubernetes([template])

        name = await run_job(
            client, "app", "test", "data-proxy-1", "data-proxy-1-ducklake-catalogs"
        )

        job_labels = {
            "app.kubernetes.io/component": "refresh-catalog",
            "data-proxy.io/schema": "test",
            "data-proxy.io/instance": "data-proxy-1",
        }
        job = client.created[0]
        assert name.startswith("refresh-catalog-test-data-proxy-1-")
        assert job.metadata == ObjectMeta(name=name, namespace="app", labels=job_labels)
        assert job.spec is not None
        assert job.spec.selector is None
        assert job.spec.completions == 1
        assert job.spec.ttlSecondsAfterFinished == 300
        assert job.spec.template.spec is not None
        assert job.spec.template.metadata == ObjectMeta(labels=job_labels)
        catalogs = next(
            v
            for v in job.spec.template.spec.volumes or []
            if v.name == "ducklake-catalogs"
        )
        assert catalogs.emptyDir is None
        assert catalogs.persistentVolumeClaim is not None
        assert (
            catalogs.persistentVolumeClaim.claimName == "data-proxy-1-ducklake-catalogs"
        )
        assert template == original

    @pytest.mark.asyncio
    async def test_waits_for_completion_and_fails_on_job_failure(self) -> None:
        client = FakeKubernetes(
            [template_job()], wait_error=ConditionError("jobs/refresh", ["Job failed"])
        )

        with pytest.raises(ConditionError):
            await run_job(client, "app", "test", "pg-1", "claim-1")

        assert len(client.waited) == 1
        assert client.waited[0][1:] == (["Complete"], ["Failed"])
        assert [name for name, _ in client.deleted] == [client.waited[0][0]]

    @pytest.mark.asyncio
    async def test_deletes_the_finished_job_so_its_pod_releases_the_claim(self) -> None:
        client = FakeKubernetes([template_job()])

        name = await run_job(client, "app", "test", "pg-1", "claim-1")

        assert client.deleted == [(name, CascadeType.BACKGROUND)]

    @pytest.mark.asyncio
    async def test_ignores_a_job_that_is_already_gone(self) -> None:
        client = FakeKubernetes(
            [template_job()],
            delete_error=ApiError(status=Status(code=404, message="not found")),
        )

        name = await run_job(client, "app", "test", "pg-1", "claim-1")

        assert client.deleted[0][0] == name

    @pytest.mark.asyncio
    async def test_shows_a_delete_error_that_is_not_a_missing_job(self) -> None:
        client = FakeKubernetes(
            [template_job()],
            delete_error=ApiError(status=Status(code=403, message="forbidden")),
        )

        with pytest.raises(ApiError, match="forbidden"):
            await run_job(client, "app", "test", "pg-1", "claim-1")

    @pytest.mark.asyncio
    async def test_gives_every_run_a_different_name(self) -> None:
        client = FakeKubernetes([template_job()])

        names = {
            await run_job(client, "app", "test", "pg-1", "claim-1") for _ in range(20)
        }

        assert len(names) == 20

    @pytest.mark.asyncio
    async def test_keeps_names_within_the_kubernetes_limit(self) -> None:
        name = await run_job(
            FakeKubernetes([template_job()]), "app", "s" * 80, "pg-1", "claim-1"
        )

        assert len(name) <= 63

    @pytest.mark.asyncio
    @pytest.mark.parametrize("templates", [0, 2])
    async def test_requires_exactly_one_template(self, templates: int) -> None:
        client = FakeKubernetes([template_job() for _ in range(templates)])

        with pytest.raises(RuntimeError, match="Expected one refresh Job template"):
            await run_job(client, "app", "test", "pg-1", "claim-1")
