declare const __VU: number;

declare module "k6" {
  export function check<T>(
    value: T,
    checks: Record<string, (value: T) => boolean>,
  ): boolean;

  export function sleep(seconds: number): void;
}

declare module "k6/http" {
  export interface Response {
    status: number;
    body: string;
    headers: Record<string, string>;
    timings: { duration: number };
    json(path?: string): unknown;
  }

  export interface RequestParams {
    headers?: Record<string, string>;
    tags?: Record<string, string>;
    [key: string]: unknown;
  }

  const http: {
    get(url: string, params?: RequestParams): Response;
    post(url: string, body?: unknown, params?: RequestParams): Response;
    del(url: string, body?: unknown, params?: RequestParams): Response;
    batch(requests: Array<{
      method: string;
      url: string;
      params?: RequestParams;
    }>): Response[];
  };

  export default http;
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
