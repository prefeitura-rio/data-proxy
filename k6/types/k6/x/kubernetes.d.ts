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

  export type KubernetesDeployment = {
    metadata?: { name?: string; labels?: Record<string, string> };
    spec: { replicas?: number; template: { spec: KubernetesPodSpec } };
  };

  export type KubernetesCronJob = {
    spec: {
      jobTemplate: {
        spec: {
          template: { spec: KubernetesPodSpec };
          [key: string]: unknown;
        };
      };
    };
  };

  export type KubernetesPod = {
    metadata: {
      name: string;
      uid: string;
      labels?: Record<string, string>;
    };
  };

  export class Kubernetes {
    get(kind: string, name: string, namespace: string): unknown;
    list(kind: string, namespace: string): unknown[];
    create(resource: unknown): void;
    delete(kind: string, name: string, namespace: string): void;
  }
}
