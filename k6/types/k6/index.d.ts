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
