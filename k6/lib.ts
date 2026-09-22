import { sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";

declare const __ENV: Record<string, string | undefined>;

export const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
export const SYNC_WORKER = __ENV.SYNC_WORKER || "data-proxy-sync-worker";

/** Returns the sync worker pod spec, used as a template for one-off Jobs. */
export function workerPodSpec(k8s: Kubernetes): Record<string, any> {
  const deployment = k8s.get("Deployment.apps", SYNC_WORKER, NAMESPACE) as {
    spec: { template: { spec: Record<string, any> } };
  };
  return deployment.spec.template.spec;
}

/** Creates a Job that triggers the DBOS sync schedule once and waits for the run. */
export function triggerSync(k8s: Kubernetes): string {
  const podSpec = workerPodSpec(k8s);
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
              command: ["python", "-m", "dp.trigger"],
            },
          ],
        },
      },
    },
  });
  return name;
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
