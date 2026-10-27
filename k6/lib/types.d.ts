declare const __VU: number;
declare const __ENV: Record<string, string | undefined>;
declare function open(path: string): string;

declare module "k6" {
    export function check<T>(
        value: T,
        checks: Record<string, (value: T) => boolean>,
    ): boolean;

    export function sleep(seconds: number): void;
}

declare module "k6/metrics" {
    export class Rate {
        constructor(name: string);
        add(value: boolean | number, tags?: Record<string, string>): void;
    }

    export class Trend {
        constructor(name: string);
        add(value: number, tags?: Record<string, string>): void;
    }

    export class Counter {
        constructor(name: string);
        add(value: number, tags?: Record<string, string>): void;
    }
}

declare module "k6/encoding" {
    const encoding: {
        b64decode(input: string, encoding: "std" | "rawstd" | "url" | "rawurl", format: "s"): string;
    };
    export default encoding;
}

declare module "k6/http" {
    export interface Response {
        status: number;
        body: string;
        headers: Record<string, string>;
        timings: { duration: number };
        request: { method: string; url: string };
        json(path?: string): unknown;
    }

    export interface RequestParams {
        headers?: Record<string, string>;
        tags?: Record<string, string>;
        [key: string]: unknown;
    }

    const http: {
        request(
            method: string,
            url: string,
            body?: unknown,
            params?: RequestParams,
        ): Response;
        get(url: string, params?: RequestParams): Response;
        post(url: string, body?: unknown, params?: RequestParams): Response;
        del(url: string, body?: unknown, params?: RequestParams): Response;
        expectedStatuses(...statuses: number[]): unknown;
        batch(requests: Array<{
            method: string;
            url: string;
            params?: RequestParams;
        }>): Response[];
    };

    export default http;
}

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

type Database = "PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL";

type Method = "GET" | "POST" | "PATCH" | "DELETE";

type Fields = Record<string, string | number | boolean | undefined>;

type TokenData = {
    token: string;
    expiresAt: number;
};

type MetricRequest = {
    metric: string;
    label: string;
    url: string;
    params: import("k6/http").RequestParams;
    extract: (response: import("k6/http").Response) => unknown;
};

type CommandJobOptions = {
    podSpec?: import("k6/x/kubernetes").KubernetesPodSpec;
    image?: string;
    serviceAccountName?: string;
    env?: import("k6/x/kubernetes").KubernetesPodSpec["containers"][number]["env"];
};

type PodJob = {
    container: string;
    command: string[];
    args?: string[];
    image?: string;
    env?: CommandJobOptions["env"];
    serviceAccountName?: string;
    ttlSecondsAfterFinished?: number;
    podSpec: import("k6/x/kubernetes").KubernetesPodSpec;
};

type RbacRules = {
    permissions: Record<string, Record<string, string>>;
    roles: { name: string; inherits?: string[]; permissions?: string[] }[];
};

type Context = {
    k8s: import("k6/x/kubernetes").Kubernetes;
};

type Step = {
    scenario: string;
    name: string;
    stage: number;
    needsData?: boolean;
    run: (context: Context) => void;
};

type SetupData = {
    tokens: TokenData[];
    fallbackDates: string[];
    idsByClient: Record<string, string[]>;
    snapshots: string[];
    otherTokens: Record<string, TokenData>;
    replicaBaseline: Record<string, number>;
};

type Route = {
    profile: string;
    path: string;
    name: string;
    expectedSource: "ducklake" | "fallback";
    heavy?: boolean;
    weight: number;
    clients: string[];
    checkBody: (body: unknown) => boolean;
};

type Stage = {
    target: number;
    duration: string;
};

type Profile = {
    executor: "ramping-vus" | "ramping-arrival-rate";
    stages?: Stage[];
    duration?: string;
    startRate?: number;
    timeUnit?: string;
    preAllocatedVUs?: number;
    maxVUs?: number;
};

type ConsoleRoute = {
    name: string;
    path: string;
    clients: string[];
    nonEmpty: boolean;
    checkRows?: (rows: Record<string, unknown>[]) => boolean;
};
