import { sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import type {
  KubernetesDeployment,
  KubernetesPodSpec,
} from "./kubernetes.ts";

declare const __ENV: Record<string, string | undefined>;

export const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
export const PIPELINE = __ENV.PIPELINE || "data-proxy-sync";
export const E2E_SCRIPT_CONFIGMAP =
  __ENV.E2E_SCRIPT_CONFIGMAP || "data-proxy-e2e";

/** Returns the sync pod spec, used as a template for one-off Jobs. */
export function workerPodSpec(k8s: Kubernetes): KubernetesPodSpec {
  const deployment = k8s.get(
    "Deployment.apps",
    PIPELINE,
    NAMESPACE,
  ) as KubernetesDeployment;
  return deployment.spec.template.spec;
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

/** Restarts one running sync pod to exercise DBOS recovery. */
export function restartPipeline(k8s: Kubernetes): void {
  const pods = k8s.list("Pod", NAMESPACE) as Array<{
    metadata: { name: string; labels?: Record<string, string> };
  }>;
  const pod = pods.find(
    (item) => item.metadata.labels?.["app.kubernetes.io/component"] === "sync",
  );
  if (!pod) throw new Error("No sync pod is available for recovery");
  k8s.delete("Pod", pod.metadata.name, NAMESPACE);
}

/** Creates a DBOS inspector Job that waits for a successful sync workflow. */
export function waitForWorkflow(k8s: Kubernetes): void {
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
              name: "inspect",
              command: ["python", "/scripts/inspect_dbos.py"],
            },
          ],
        },
      },
    },
  });
  waitForJob(k8s, name);
}

/** Waits for a Kubernetes Job to succeed or fail. */
export function waitForJob(k8s: Kubernetes, name: string): void {
  const deadline = Date.now() + 300_000;
  while (Date.now() < deadline) {
    const job = k8s.get("Job.batch", name, NAMESPACE) as {
      status?: { succeeded?: number; failed?: number };
    };
    if (job.status?.succeeded && job.status.succeeded > 0) return;
    if (job.status?.failed && job.status.failed > 0)
      throw new Error(`Job ${name} failed`);
    sleep(1);
  }
  throw new Error(`Job ${name} timed out`);
}
