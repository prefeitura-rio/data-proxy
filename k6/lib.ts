import { sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";

declare const __ENV: Record<string, string | undefined>;

export const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
export const PRODUCER_CRONJOB = __ENV.PRODUCER_CRONJOB || "data-proxy-producer";

/** Creates a producer Job from the configured CronJob template. */
export function triggerSync(k8s: Kubernetes): string {
    const cronJob = k8s.get("CronJob.batch", PRODUCER_CRONJOB, NAMESPACE) as {
        spec: { jobTemplate: { spec: object } };
    };
    const name = `data-proxy-producer-k6-${Date.now()}`;
    k8s.create({
        apiVersion: "batch/v1",
        kind: "Job",
        metadata: { name, namespace: NAMESPACE },
        spec: cronJob.spec.jobTemplate.spec,
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
        if (job.status?.failed && job.status.failed > 0) throw new Error(`Job ${name} failed`);
        sleep(1);
    }
    throw new Error(`Job ${name} timed out`);
}
