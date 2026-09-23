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
  spec: { template: { spec: KubernetesPodSpec } };
};
