/**
 * Minimal type declarations for the xk6-kubernetes extension.
 * https://github.com/grafana/xk6-kubernetes
 */

declare module "k6/x/kubernetes" {
  export type KubernetesEnv = {
    name: string;
    value?: string;
    valueFrom?: Record<string, unknown>;
  };

  export type KubernetesVolumeMount = {
    name: string;
    mountPath: string;
    subPath?: string;
    readOnly?: boolean;
  };

  export type KubernetesVolume = {
    name: string;
    configMap?: { name: string };
  };

  export type KubernetesContainer = {
    name: string;
    image?: string;
    imagePullPolicy?: string;
    command?: string[];
    args?: string[];
    env?: KubernetesEnv[];
    volumeMounts?: KubernetesVolumeMount[];
    [key: string]: unknown;
  };

  export type KubernetesPodSpec = {
    containers: KubernetesContainer[];
    volumes?: KubernetesVolume[];
    restartPolicy?: string;
    [key: string]: unknown;
  };

  export type KubernetesMetadata = {
    name: string;
    uid?: string;
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
  };

  export type KubernetesCondition = { type: string; status: string };

  export type KubernetesPod = {
    metadata: KubernetesMetadata;
    spec: { containers: KubernetesContainer[] };
    status?: {
      podIP?: string;
      conditions?: KubernetesCondition[];
      containerStatuses?: { name: string; restartCount: number }[];
    };
  };

  export type KubernetesDeployment = {
    metadata: KubernetesMetadata;
    spec: {
      replicas?: number;
      template: {
        metadata?: { annotations?: Record<string, string> };
        spec: KubernetesPodSpec;
      };
    };
    status?: { availableReplicas?: number; observedGeneration?: number };
  };

  export type KubernetesCronJob = {
    metadata: KubernetesMetadata;
    spec: {
      jobTemplate: {
        spec: {
          template: { spec: KubernetesPodSpec };
          [key: string]: unknown;
        };
      };
    };
  };

  export type KubernetesJob = {
    status?: { succeeded?: number; failed?: number };
  };

  export type KubernetesCluster = {
    metadata: KubernetesMetadata;
    spec: { instances: number };
    status?: { readyInstances?: number; phase?: string; currentPrimary?: string };
  };

  export type KubernetesScaledObject = { metadata: KubernetesMetadata };

  /** The kinds that the scripts read, with the object that the API returns for each. */
  export interface KubernetesKinds {
    Pod: KubernetesPod;
    "Deployment.apps": KubernetesDeployment;
    "CronJob.batch": KubernetesCronJob;
    "Job.batch": KubernetesJob;
    "Cluster.postgresql.cnpg.io": KubernetesCluster;
    "ScaledObject.keda.sh": KubernetesScaledObject;
  }

  export class Kubernetes {
    get<K extends keyof KubernetesKinds>(kind: K, name: string, namespace: string): KubernetesKinds[K];
    list<K extends keyof KubernetesKinds>(kind: K, namespace: string): KubernetesKinds[K][];
    create(resource: unknown): void;
    delete(kind: string, name: string, namespace: string): void;
  }
}
