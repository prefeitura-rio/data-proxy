/**
 * Minimal type declarations for the xk6-kubernetes extension.
 * https://github.com/grafana/xk6-kubernetes
 */

declare module 'k6/x/kubernetes' {
    export class Kubernetes {
        get(kind: string, name: string, namespace: string): unknown;
        create(resource: unknown): void;
    }
}
