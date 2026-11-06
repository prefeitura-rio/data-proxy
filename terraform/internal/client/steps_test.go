package client

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

// step is one request that a test server expects, and the answer that it gives. An empty field is not checked.
type step struct {
	method         string
	uri            string
	acceptProfile  string
	contentProfile string
	body           string
	status         int
	response       string
}

// newStepClient starts a server that expects the steps in order and returns a client for it. A request that
// differs from its step fails the test, and so does a step that no request reached.
func newStepClient(t *testing.T, steps []step) *Client {
	t.Helper()

	next := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if next >= len(steps) {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.RequestURI())
			w.WriteHeader(http.StatusInternalServerError)

			return
		}

		want := steps[next]
		next++

		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read the request body: %v", err)
		}

		checkStep(t, want, r, raw)

		status := want.status
		if status == 0 {
			status = http.StatusOK
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(want.response)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(func() {
		server.Close()

		if next != len(steps) {
			t.Errorf("the server got %d requests, want %d", next, len(steps))
		}
	})

	c, err := New(server.URL, staticToken{token: "jwt-123"}, server.Client())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	return c
}

// checkStep compares a request with the step that it should match.
func checkStep(t *testing.T, want step, r *http.Request, body []byte) {
	t.Helper()

	if want.method != "" && r.Method != want.method {
		t.Errorf("method = %s, want %s", r.Method, want.method)
	}

	if want.uri != "" && r.URL.RequestURI() != want.uri {
		t.Errorf("uri = %s, want %s", r.URL.RequestURI(), want.uri)
	}

	if want.acceptProfile != "" && r.Header.Get("Accept-Profile") != want.acceptProfile {
		t.Errorf("Accept-Profile = %q, want %q", r.Header.Get("Accept-Profile"), want.acceptProfile)
	}

	if want.contentProfile != "" && r.Header.Get("Content-Profile") != want.contentProfile {
		t.Errorf("Content-Profile = %q, want %q", r.Header.Get("Content-Profile"), want.contentProfile)
	}

	if want.body != "" && !sameJSON(t, want.body, string(body)) {
		t.Errorf("body = %s, want %s", body, want.body)
	}
}

// sameJSON reports whether two JSON texts hold the same value.
func sameJSON(t *testing.T, want, got string) bool {
	t.Helper()

	var left, right any

	if err := json.Unmarshal([]byte(want), &left); err != nil {
		t.Fatalf("want is not JSON: %v", err)
	}

	if err := json.Unmarshal([]byte(got), &right); err != nil {
		return false
	}

	return reflect.DeepEqual(left, right)
}
