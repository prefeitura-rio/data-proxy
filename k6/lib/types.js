/** @import { RequestParams, Response as K6Response } from 'k6/http' */
/** @import { Kubernetes, KubernetesPodSpec } from 'k6/x/kubernetes' */
export { };
/** @typedef {"PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL"} Database */
/** @typedef {"GET" | "POST" | "PATCH" | "DELETE"} Method */
/**
 * @typedef {Object} TokenData
 * @property {string} token
 * @property {number} expiresAt
 */
/**
 * @typedef {Object} MetricRequest
 * @property {string} metric
 * @property {string} label
 * @property {string} url
 * @property {RequestParams} params
 * @property {(response: K6Response) => unknown} extract
 */
/**
 * Where a command Job runs: its pod spec, image, and service account.
 * @typedef {Object} CommandJobOptions
 * @property {KubernetesPodSpec} [podSpec]
 * @property {string} [image]
 * @property {string} [serviceAccountName]
 * @property {KubernetesPodSpec["containers"][number]["env"]} [env]
 */
/**
 * @typedef {Object} RbacRules
 * @property {Record<string, Record<string, string>>} permissions
 * @property {Array<{ name: string; inherits?: string[]; permissions?: string[] }>} roles
 */
/**
 * What a step gets from the orchestration.
 * @typedef {Object} Context
 * @property {Kubernetes} k8s
 */
/**
 * One step of a scenario. The steps of all the scenarios run in the order of their stage.
 * @typedef {Object} Step
 * @property {string} scenario
 * @property {string} name
 * @property {number} stage The place in the run. Leave gaps between the numbers, so that a new step fits between two others.
 * @property {boolean} [needsData] A step that needs no synced data still runs when the sync didn't finish.
 * @property {(context: Context) => void} run
 */
