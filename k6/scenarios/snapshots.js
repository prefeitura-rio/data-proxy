/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    cacheHeader,
    FULL_TABLE,
    mutateSnapshotFixture,
    postgrestDeploymentRevision,
    proxyGet,
    rowsOf,
    scenario,
    SNAPSHOT_SOURCE,
    SNAPSHOT_TABLE,
    snapshotHeader,
    snapshotValue,
    sourceHeader,
    stable,
    triggerSync,
    userToken,
    waitForJob,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("snapshots");

/**
 * @param {Kubernetes} k8s
 */
export function historical(k8s) {
    const token = userToken();
    const path = `/${SNAPSHOT_TABLE}?id=eq.snapshot-row&select=version`;
    const snapshotN = snapshotValue(token);
    const before = proxyGet(path, token, { "X-DuckLake-Snapshot": snapshotN });
    requirePrecondition("the snapshot fixture can be read", before.status === 200, { status: before.status });

    requirePrecondition("snapshot fixture starts at version A", rowsOf(before)[0]?.version === "A", {
        status: before.status,
    });

    waitForJob(k8s, mutateSnapshotFixture(k8s, SNAPSHOT_SOURCE, "B"));
    waitForJob(k8s, triggerSync(k8s));
    const snapshotN1 = snapshotValue(token);
    const historical = proxyGet(path, token, { "X-DuckLake-Snapshot": snapshotN });
    const latest = proxyGet(path, token);
    expect("the snapshot fixture publication creates a new snapshot", snapshotN1 !== snapshotN);

    expect("the historical snapshot keeps version A", rowsOf(historical)[0]?.version === "A");

    expect("the latest snapshot returns version B", rowsOf(latest)[0]?.version === "B");

    expect("publication clears the unpinned cache", cacheHeader(latest) === "MISS");
}

/**
 * @param {Kubernetes} k8s
 */
export function noChangeRun(k8s) {
    const snapshotBefore = snapshotValue(userToken());
    const revisionBefore = postgrestDeploymentRevision(k8s);
    const job = triggerSync(k8s);
    waitForJob(k8s, job);
    const snapshotAfter = snapshotValue(userToken());
    const revisionAfter = postgrestDeploymentRevision(k8s);
    expect("an unchanged sync preserves the snapshot", snapshotAfter === snapshotBefore);
    expect("an unchanged sync does not restart PostgREST", revisionAfter === revisionBefore);
}

/**
 * Verifies a client can pin a DuckLake snapshot version.
 * @param {string} token
 */
export function pinnedVersion(token) {
    const path = `/${FULL_TABLE}?select=id&order=id&limit=5`;
    const latest = proxyGet(path, token);
    const snapshot = snapshotHeader(latest);
    requirePrecondition("the latest answer is served", latest.status === 200, { status: latest.status, snapshot });

    requirePrecondition("the latest answer names its snapshot", /^[0-9]+$/.test(snapshot), {
        status: latest.status,
        snapshot,
    });

    const pinned = proxyGet(path, token, { "X-DuckLake-Snapshot": snapshot });
    expect("a pinned version is served", pinned.status === 200);
    expect("a pinned version is read from DuckLake only", sourceHeader(pinned) === "ducklake");
    expect("a pinned version reports that version", snapshotHeader(pinned) === snapshot);

    expect(
        "the latest version returns the same rows as the default request",
        stable(rowsOf(pinned)) === stable(rowsOf(latest)),
    );

    const unknown = proxyGet(path, token, {
        "X-DuckLake-Snapshot": String(Number(snapshot) + 1000),
    });

    expect("an unknown version is not found", unknown.status === 404);
    const invalid = proxyGet(path, token, { "X-DuckLake-Snapshot": "latest" });
    expect("a version that is not a number is rejected", invalid.status === 400);
}

/** @type {Step[]} */
export const steps = [
    { scenario: "snapshots", name: "historical", stage: 20, run: ({ k8s }) => historical(k8s) },
    { scenario: "snapshots", name: "noChangeRun", stage: 30, run: ({ k8s }) => noChangeRun(k8s) },
    { scenario: "snapshots", name: "pinnedVersion", stage: 220, run: () => pinnedVersion(userToken()) },
];
