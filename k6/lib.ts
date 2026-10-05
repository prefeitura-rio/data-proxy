import { sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import type {
  KubernetesCronJob,
  KubernetesDeployment,
  KubernetesPod,
  KubernetesPodSpec,
} from "k6/x/kubernetes";

declare const __ENV: Record<string, string | undefined>;

export const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
export const PIPELINE = __ENV.PIPELINE || "data-proxy-sync";
export const E2E_SCRIPT_CONFIGMAP =
  __ENV.E2E_SCRIPT_CONFIGMAP || "data-proxy-e2e";

/** Returns the sync pod spec, used as a template for one-off Jobs. */
export function deploymentPodSpec(
  k8s: Kubernetes,
  deploymentName: string,
): KubernetesPodSpec {
  const deployment = k8s.get(
    "Deployment.apps",
    deploymentName,
    NAMESPACE,
  ) as KubernetesDeployment;
  return deployment.spec.template.spec;
}

export function workerPodSpec(k8s: Kubernetes): KubernetesPodSpec {
  return deploymentPodSpec(k8s, PIPELINE);
}

/** Returns the pod spec of a CronJob, used as a template for one-off Jobs. */
export function cronJobPodSpec(
  k8s: Kubernetes,
  cronJobName: string,
): KubernetesPodSpec {
  const cronJob = k8s.get(
    "CronJob.batch",
    cronJobName,
    NAMESPACE,
  ) as KubernetesCronJob;
  return cronJob.spec.jobTemplate.spec.template.spec;
}

/** Adds the standalone e2e scripts to a sync pod spec. */
function scriptPodSpec(podSpec: KubernetesPodSpec): KubernetesPodSpec {
  const scriptVolume = {
    name: "e2e-scripts",
    configMap: { name: E2E_SCRIPT_CONFIGMAP },
  };
  const scriptMount = {
    name: "e2e-scripts",
    mountPath: "/scripts",
    readOnly: true,
  };
  return {
    ...podSpec,
    volumes: [...(podSpec.volumes || []), scriptVolume],
    containers: podSpec.containers.map((container) => ({
      ...container,
      volumeMounts: [...(container.volumeMounts || []), scriptMount],
    })),
  };
}

/** Creates a Job that triggers the DBOS sync schedule once and waits for the run. */
export function mutateSnapshotFixture(
  k8s: Kubernetes,
  source: string,
  version: string,
): string {
  const podSpec = scriptPodSpec(workerPodSpec(k8s));
  const container = podSpec.containers[0];
  const name = `data-proxy-mutate-snapshot-${Date.now()}`;
  k8s.create({
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name, namespace: NAMESPACE },
    spec: {
      backoffLimit: 0,
      ttlSecondsAfterFinished: 300,
      template: {
        spec: {
          ...podSpec,
          restartPolicy: "Never",
          containers: [
            {
              ...container,
              name: "mutate-snapshot",
              command: ["python", "/scripts/mutate.py", "--version", version],
              env: [
                ...(container.env || []),
                { name: "SNAPSHOT_SOURCE", value: source },
              ],
            },
          ],
        },
      },
    },
  });
  return name;
}

export function triggerSync(k8s: Kubernetes, detached = false): string {
  const podSpec = scriptPodSpec(workerPodSpec(k8s));
  const container = podSpec.containers[0];
  const name = `data-proxy-sync-k6-${Date.now()}`;
  k8s.create({
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name, namespace: NAMESPACE },
    spec: {
      backoffLimit: 0,
      ttlSecondsAfterFinished: 300,
      template: {
        spec: {
          ...podSpec,
          restartPolicy: "Never",
          containers: [
            {
              ...container,
              name: "trigger",
              command: detached
                ? ["python", "/scripts/trigger.py", "--detach"]
                : ["python", "/scripts/trigger.py"],
            },
          ],
        },
      },
    },
  });
  return name;
}

/** Lists the sync pods. DBOS uses each pod UID as its executor ID. */
export function syncPods(k8s: Kubernetes): KubernetesPod[] {
  return (k8s.list("Pod", NAMESPACE) as KubernetesPod[]).filter(
    (pod) => pod.metadata.labels?.["app.kubernetes.io/component"] === "sync",
  );
}

/** Creates a Job that waits for the latest DBOS sync workflow to complete after a detached trigger. */
export function waitForWorkflow(
  k8s: Kubernetes,
): void {
  const podSpec = scriptPodSpec(workerPodSpec(k8s));
  const name = `data-proxy-workflow-k6-${Date.now()}`;
  k8s.create({
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name, namespace: NAMESPACE },
    spec: {
      backoffLimit: 0,
      ttlSecondsAfterFinished: 300,
      template: {
        spec: {
          ...podSpec,
          restartPolicy: "Never",
          containers: [
            {
              ...podSpec.containers[0],
              name: "wait",
              command: [
                "python",
                "/scripts/trigger.py",
                "--wait",
              ],
            },
          ],
        },
      },
    },
  });
  waitForJob(k8s, name);
}

/** Starts one Job from a CronJob template, as `kubectl create job --from` does. */
export function runCronJob(k8s: Kubernetes, cronJobName: string): string {
  const cronJob = k8s.get(
    "CronJob.batch",
    cronJobName,
    NAMESPACE,
  ) as KubernetesCronJob;
  const name = `${cronJobName}-e2e-${Date.now()}`;
  k8s.create({
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name, namespace: NAMESPACE },
    spec: { ...cronJob.spec.jobTemplate.spec, backoffLimit: 0 },
  });
  return name;
}

/** Waits for a Kubernetes Job to finish and returns how it ended. */
function jobResult(
  k8s: Kubernetes,
  name: string,
): "succeeded" | "failed" {
  const deadline = Date.now() + 900_000;
  while (Date.now() < deadline) {
    const job = k8s.get("Job.batch", name, NAMESPACE) as {
      status?: { succeeded?: number; failed?: number };
    };
    if (job.status?.succeeded && job.status.succeeded > 0) return "succeeded";
    if (job.status?.failed && job.status.failed > 0) return "failed";
    sleep(1);
  }
  throw new Error(`Job ${name} timed out`);
}

/** Waits for a Kubernetes Job to succeed and fails when it fails. */
export function waitForJob(k8s: Kubernetes, name: string): void {
  if (jobResult(k8s, name) === "failed") throw new Error(`Job ${name} failed`);
}
