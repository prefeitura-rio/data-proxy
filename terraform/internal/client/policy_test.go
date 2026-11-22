package client_test

import (
	"context"
	"strings"
	"terraform-provider-data-proxy/internal/client"
	"testing"
	"time"
)

func TestGrantOperations(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		grant client.Grant
		uri   string
		body  string
		row   string
	}{
		{
			name:  "a grant on a schema",
			grant: client.Grant{Scope: "sales.orders", Subject: "reader@example.org", UnitType: "unit", UnitID: "42"},
			uri:   "/policy?scope=eq.sales.orders&subject=eq.reader%40example.org&unit_type=eq.unit&unit_id=eq.42",
			body:  `{"scope":"sales.orders","subject":"reader@example.org","unit_type":"unit","unit_id":"42"}`,
			row:   `{"scope":"sales.orders","subject":"reader@example.org","unit_type":"unit","unit_id":"42"}`,
		},
		{
			name:  "the scope * is the empty path",
			grant: client.Grant{Scope: "*", Subject: "svc", UnitType: "*", UnitID: "*"},
			uri:   "/policy?scope=eq.&subject=eq.svc&unit_type=eq.%2A&unit_id=eq.%2A",
			body:  `{"scope":"","subject":"svc","unit_type":"*","unit_id":"*"}`,
			row:   `{"scope":"","subject":"svc","unit_type":"*","unit_id":"*"}`,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			apiClient := newStepClient(t, []step{
				{method: "POST", uri: "/policy", contentProfile: "access", body: testCase.body, status: 201, response: "[]"},
				{method: "GET", uri: testCase.uri, acceptProfile: "access", response: "[" + testCase.row + "]"},
				{method: "DELETE", uri: testCase.uri, contentProfile: "access", response: "[]"},
			})
			ctx := context.Background()

			err := apiClient.CreateGrant(ctx, testCase.grant)
			if err != nil {
				t.Fatalf("CreateGrant: %v", err)
			}

			rows, err := apiClient.ListGrants(ctx, testCase.grant)
			if err != nil || len(rows) != 1 || rows[0] != testCase.grant {
				t.Fatalf("ListGrants = %+v, %v, want the one grant %+v", rows, err, testCase.grant)
			}

			err = apiClient.DeleteGrant(ctx, testCase.grant)
			if err != nil {
				t.Fatalf("DeleteGrant: %v", err)
			}
		})
	}
}

func TestIsNotProvisioned(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "an unknown profile", err: &client.Error{Status: 406, Code: "PGRST106"}, want: true},
		{name: "a table that the cache does not know", err: &client.Error{Status: 404, Code: "PGRST205"}, want: true},
		{name: "a missing relation", err: &client.Error{Status: 400, Code: "42P01"}, want: true},
		{name: "a missing schema", err: &client.Error{Status: 400, Code: "3F000"}, want: true},
		{name: "a 404 without a code", err: &client.Error{Status: 404}, want: true},
		{name: "a refused permission", err: &client.Error{Status: 403, Code: "42501"}, want: false},
		{name: "a failure that is not an API error", err: errConnectionRefused, want: false},
		{name: "no error", err: nil, want: false},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			if got := client.IsNotProvisioned(testCase.err); got != testCase.want {
				t.Errorf("IsNotProvisioned(%v) = %t, want %t", testCase.err, got, testCase.want)
			}
		})
	}
}

// noWait replaces the wait between two checks. It counts the calls and returns a fixed error.
type noWait struct {
	calls int
	err   error
}

// wait counts the call and returns the error of the test without a delay.
func (nw *noWait) wait(context.Context, time.Duration) error {
	nw.calls++

	return nw.err
}

// The answers of the health view and the error of a schema that PostgREST does not know yet.
const (
	healthMissing = `[]`
	healthPartial = `[{"name":"s","schema_exists":false,"view_count":0}]`
	healthReady   = `[{"name":"s","schema_exists":true,"view_count":0}]`
	notProvided   = `{"code":"PGRST106","message":"The schema must be one of the following"}`
)

func TestWaitProvisioned(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		steps     []step
		wait      noWait
		wantWaits int
		wantErr   string
	}{
		{
			name: "ready at once",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/", acceptProfile: "s", response: "{}"},
			},
		},
		{
			name: "waits for the row, the schema, then the profile",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthMissing},
				{uri: "/health?name=eq.s", response: healthPartial},
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/", status: 406, response: notProvided},
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/", response: "{}"},
			},
			wantWaits: 3,
		},
		{
			name:    "stops when the context ends",
			steps:   []step{{uri: "/health?name=eq.s", response: healthMissing}},
			wait:    noWait{err: context.DeadlineExceeded},
			wantErr: "wait for the schema s to be provisioned: context deadline exceeded",
		},
		{
			name:    "stops on an error that is not about provisioning",
			steps:   []step{{uri: "/health?name=eq.s", status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantErr: "HTTP 403",
		},
		{
			name: "stops on a refused read of the table",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/", status: 403, response: `{"code":"42501","message":"denied"}`},
			},
			wantErr: "HTTP 403",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			c := newStepClient(t, testCase.steps)
			wait := testCase.wait

			err := c.WaitProvisionedWith(context.Background(), "s", wait.wait)

			checkWait(t, err, testCase.wantErr)

			if testCase.wantErr == "" && wait.calls != testCase.wantWaits {
				t.Errorf("waits = %d, want %d", wait.calls, testCase.wantWaits)
			}
		})
	}
}

func TestWaitDeprovisioned(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		steps     []step
		wait      noWait
		wantWaits int
		wantErr   string
	}{
		{name: "already dropped", steps: []step{{status: 406, response: notProvided}}},
		{
			name: "waits until the read fails",
			steps: []step{
				{response: "[]"},
				{response: "[]"},
				{status: 404, response: `{"code":"PGRST205","message":"not found"}`},
			},
			wantWaits: 2,
		},
		{
			name:    "stops when the context ends",
			steps:   []step{{response: "[]"}},
			wait:    noWait{err: context.Canceled},
			wantErr: "wait for the schema s to be dropped: context canceled",
		},
		{
			name:    "stops on an error that is not about provisioning",
			steps:   []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantErr: "HTTP 403",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			c := newStepClient(t, testCase.steps)
			wait := testCase.wait

			err := c.WaitDeprovisionedWith(context.Background(), "s", wait.wait)

			checkWait(t, err, testCase.wantErr)

			if testCase.wantErr == "" && wait.calls != testCase.wantWaits {
				t.Errorf("waits = %d, want %d", wait.calls, testCase.wantWaits)
			}
		})
	}
}

// checkWait fails the test unless the error is empty, or contains the wanted text.
func checkWait(t *testing.T, err error, want string) {
	t.Helper()

	switch {
	case want == "" && err != nil:
		t.Fatalf("error = %v, want none", err)
	case want != "" && (err == nil || !strings.Contains(err.Error(), want)):
		t.Fatalf("error = %v, want one with %q", err, want)
	}
}
