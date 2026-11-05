package client

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"
	"time"
)

type staticToken struct {
	token string
	err   error
}

func (s staticToken) Token(context.Context) (string, error) {
	return s.token, s.err
}

// captured is what the test server saw of one request.
type captured struct {
	method  string
	uri     string
	headers http.Header
	body    string
}

// newServer starts a server that records each request and answers with the status and the body.
func newServer(t *testing.T, status int, response string) (*Client, *[]captured) {
	t.Helper()

	var requests []captured

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read the request body: %v", err)
		}

		requests = append(requests, captured{method: r.Method, uri: r.URL.RequestURI(), headers: r.Header.Clone(), body: string(raw)})

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(response)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	c, err := New(server.URL+"/api/", staticToken{token: "jwt-123"}, server.Client())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	return c, &requests
}

type row struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

func TestRequests(t *testing.T) {
	tests := []struct {
		id          string
		call        func(context.Context, *Client) error
		method      string
		uri         string
		body        string
		headers     map[string]string
		absent      []string
		contentType string
	}{
		{
			id: "get reads with Accept-Profile",
			call: func(ctx context.Context, c *Client) error {
				var rows []row

				return c.Get(ctx, ConsoleProfile, "projects", []Filter{Eq("name", "a b&c"), IsNull("deleted_at")}, &rows)
			},
			method:  http.MethodGet,
			uri:     "/api/projects?name=eq.a%20b%26c&deleted_at=is.null",
			headers: map[string]string{"Accept-Profile": "console"},
			absent:  []string{"Content-Profile", "Prefer", "Content-Type"},
		},
		{
			id: "get sends the profile of a data schema",
			call: func(ctx context.Context, c *Client) error {
				return c.Get(ctx, "alpha", "access_policies", nil, nil)
			},
			method:  http.MethodGet,
			uri:     "/api/access_policies",
			headers: map[string]string{"Accept-Profile": "alpha"},
			absent:  []string{"Content-Profile", "Prefer"},
		},
		{
			id: "insert writes with Content-Profile and return=representation",
			call: func(ctx context.Context, c *Client) error {
				return c.Insert(ctx, ConsoleProfile, "projects", row{ID: 1, Name: "a"}, nil)
			},
			method:      http.MethodPost,
			uri:         "/api/projects",
			body:        `{"id":1,"name":"a"}`,
			headers:     map[string]string{"Content-Profile": "console", "Prefer": "return=representation"},
			absent:      []string{"Accept-Profile"},
			contentType: "application/json",
		},
		{
			id: "patch writes to the filtered rows",
			call: func(ctx context.Context, c *Client) error {
				return c.Patch(ctx, "alpha", "access_policies", []Filter{Eq("id", "7")}, map[string]string{"name": "b"}, nil)
			},
			method:      http.MethodPatch,
			uri:         "/api/access_policies?id=eq.7",
			body:        `{"name":"b"}`,
			headers:     map[string]string{"Content-Profile": "alpha", "Prefer": "return=representation"},
			absent:      []string{"Accept-Profile"},
			contentType: "application/json",
		},
		{
			id: "delete removes the filtered rows without a body",
			call: func(ctx context.Context, c *Client) error {
				return c.Delete(ctx, ConsoleProfile, "projects", []Filter{Eq("id", "7"), IsNull("parent_id")}, nil)
			},
			method:  http.MethodDelete,
			uri:     "/api/projects?id=eq.7&parent_id=is.null",
			headers: map[string]string{"Content-Profile": "console", "Prefer": "return=representation"},
			absent:  []string{"Accept-Profile", "Content-Type"},
		},
		{
			id: "rpc posts the arguments with Content-Profile",
			call: func(ctx context.Context, c *Client) error {
				return c.RPC(ctx, ConsoleProfile, "grant_role", map[string]string{"role": "viewer"}, nil)
			},
			method:      http.MethodPost,
			uri:         "/api/rpc/grant_role",
			body:        `{"role":"viewer"}`,
			headers:     map[string]string{"Content-Profile": "console"},
			absent:      []string{"Accept-Profile", "Prefer"},
			contentType: "application/json",
		},
		{
			id: "rpc without arguments posts an empty object",
			call: func(ctx context.Context, c *Client) error {
				return c.RPC(ctx, ConsoleProfile, "config", nil, nil)
			},
			method:      http.MethodPost,
			uri:         "/api/rpc/config",
			body:        `{}`,
			headers:     map[string]string{"Content-Profile": "console"},
			absent:      []string{"Accept-Profile", "Prefer"},
			contentType: "application/json",
		},
		{
			id: "get rpc reads with Accept-Profile and no body",
			call: func(ctx context.Context, c *Client) error {
				return c.GetRPC(ctx, ConsoleProfile, "permissions", nil)
			},
			method:  http.MethodGet,
			uri:     "/api/rpc/permissions",
			headers: map[string]string{"Accept-Profile": "console"},
			absent:  []string{"Content-Profile", "Prefer", "Content-Type"},
		},
		{
			id: "permissions reads console.permissions with get",
			call: func(ctx context.Context, c *Client) error {
				_, err := c.Permissions(ctx)

				return err
			},
			method:  http.MethodGet,
			uri:     "/api/rpc/permissions",
			headers: map[string]string{"Accept-Profile": "console"},
			absent:  []string{"Content-Profile", "Prefer", "Content-Type"},
		},
		{
			id: "table name is escaped in the path",
			call: func(ctx context.Context, c *Client) error {
				return c.Get(ctx, ConsoleProfile, "a/b", nil, nil)
			},
			method:  http.MethodGet,
			uri:     "/api/a%2Fb",
			headers: map[string]string{"Accept-Profile": "console"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			c, requests := newServer(t, http.StatusOK, `[]`)

			if err := tt.call(context.Background(), c); err != nil {
				t.Fatalf("call: %v", err)
			}

			if len(*requests) != 1 {
				t.Fatalf("got %d requests, want 1", len(*requests))
			}

			got := (*requests)[0]

			if got.method != tt.method {
				t.Errorf("method = %q, want %q", got.method, tt.method)
			}

			if got.uri != tt.uri {
				t.Errorf("uri = %q, want %q", got.uri, tt.uri)
			}

			if got.body != tt.body {
				t.Errorf("body = %q, want %q", got.body, tt.body)
			}

			want := map[string]string{"Authorization": "Bearer jwt-123", "Accept": "application/json", "Content-Type": tt.contentType}
			for name, value := range tt.headers {
				want[name] = value
			}

			for name, value := range want {
				if got.headers.Get(name) != value {
					t.Errorf("header %s = %q, want %q", name, got.headers.Get(name), value)
				}
			}

			for _, name := range tt.absent {
				if values, ok := got.headers[name]; ok {
					t.Errorf("header %s = %q, want none", name, values)
				}
			}
		})
	}
}

func TestDecodesTheResponse(t *testing.T) {
	c, _ := newServer(t, http.StatusCreated, `[{"id":1,"name":"a"}]`)

	var rows []row

	if err := c.Insert(context.Background(), ConsoleProfile, "projects", row{Name: "a"}, &rows); err != nil {
		t.Fatalf("Insert: %v", err)
	}

	want := []row{{ID: 1, Name: "a"}}
	if len(rows) != 1 || rows[0] != want[0] {
		t.Errorf("rows = %v, want %v", rows, want)
	}
}

func TestAcceptsAnEmptyResponse(t *testing.T) {
	c, _ := newServer(t, http.StatusNoContent, ``)

	var rows []row

	if err := c.Delete(context.Background(), ConsoleProfile, "projects", []Filter{Eq("id", "1")}, &rows); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	if rows != nil {
		t.Errorf("rows = %v, want nil", rows)
	}
}

func TestPermissions(t *testing.T) {
	tests := []struct {
		id       string
		status   int
		response string
		want     []string
		wantErr  string
		kind     error
	}{
		{id: "the permissions of the token", status: http.StatusOK, response: `["read_sync","read_config"]`, want: []string{"read_sync", "read_config"}},
		{id: "no permission", status: http.StatusOK, response: `[]`, want: []string{}},
		{id: "a null result", status: http.StatusOK, response: `null`, want: []string{}},
		{
			id:       "a refused token",
			status:   http.StatusUnauthorized,
			response: `{"code":"PGRST301","message":"JWSError JWSInvalidSignature"}`,
			wantErr:  "read the permissions of the token: postgrest: HTTP 401 PGRST301: JWSError JWSInvalidSignature",
			kind:     ErrUnauthorized,
		},
		{
			id:       "a result that isn't a list of names",
			status:   http.StatusOK,
			response: `{"read_config":true}`,
			wantErr:  "read the permissions of the token: GET rpc/permissions: decode the response: json: cannot unmarshal object into Go value of type []string",
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			c, _ := newServer(t, tt.status, tt.response)

			got, err := c.Permissions(context.Background())

			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("Permissions: %v", err)
				}

				if !reflect.DeepEqual(got, tt.want) {
					t.Errorf("permissions = %q, want %q", got, tt.want)
				}

				return
			}

			if err == nil || err.Error() != tt.wantErr {
				t.Fatalf("error = %v, want %q", err, tt.wantErr)
			}

			if tt.kind != nil && !errors.Is(err, tt.kind) {
				t.Errorf("errors.Is(err, %v) = false, want true", tt.kind)
			}

			if got != nil {
				t.Errorf("permissions = %q, want nil with an error", got)
			}
		})
	}
}

func TestGetOne(t *testing.T) {
	tests := []struct {
		id       string
		response string
		want     row
		noRow    bool
		wantErr  string
	}{
		{id: "one row", response: `[{"id":3,"name":"c"}]`, want: row{ID: 3, Name: "c"}},
		{id: "no row", response: `[]`, noRow: true, wantErr: "no row of projects matches the filters: no row"},
		{id: "two rows", response: `[{"id":1},{"id":2}]`, wantErr: "2 rows of projects match the filters, want one"},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			c, _ := newServer(t, http.StatusOK, tt.response)

			var got row

			err := c.GetOne(context.Background(), ConsoleProfile, "projects", []Filter{Eq("id", "3")}, &got)

			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("GetOne: %v", err)
				}

				if got != tt.want {
					t.Errorf("row = %v, want %v", got, tt.want)
				}

				return
			}

			if err == nil || err.Error() != tt.wantErr {
				t.Fatalf("error = %v, want %q", err, tt.wantErr)
			}

			if errors.Is(err, ErrNoRow) != tt.noRow {
				t.Errorf("errors.Is(err, ErrNoRow) = %t, want %t", !tt.noRow, tt.noRow)
			}

			if errors.Is(err, ErrNotFound) {
				t.Errorf("errors.Is(err, ErrNotFound) = true, want false")
			}
		})
	}
}

func TestRefusesBeforeTheRequest(t *testing.T) {
	tests := []struct {
		id      string
		tokens  TokenSource
		call    func(context.Context, *Client) error
		wantErr string
	}{
		{
			id: "patch without a filter",
			call: func(ctx context.Context, c *Client) error {
				return c.Patch(ctx, ConsoleProfile, "projects", nil, map[string]int{}, nil)
			},
			wantErr: "patch projects: give at least one filter",
		},
		{
			id:      "delete without a filter",
			call:    func(ctx context.Context, c *Client) error { return c.Delete(ctx, ConsoleProfile, "projects", nil, nil) },
			wantErr: "delete from projects: give at least one filter",
		},
		{
			id:      "empty profile",
			call:    func(ctx context.Context, c *Client) error { return c.Get(ctx, "", "projects", nil, nil) },
			wantErr: "the profile is empty",
		},
		{
			id:      "empty table",
			call:    func(ctx context.Context, c *Client) error { return c.Get(ctx, ConsoleProfile, "", nil, nil) },
			wantErr: "the table name is empty",
		},
		{
			id:      "empty function",
			call:    func(ctx context.Context, c *Client) error { return c.RPC(ctx, ConsoleProfile, "", nil, nil) },
			wantErr: "the function name is empty",
		},
		{
			id:      "empty function of a get rpc",
			call:    func(ctx context.Context, c *Client) error { return c.GetRPC(ctx, ConsoleProfile, "", nil) },
			wantErr: "the function name is empty",
		},
		{
			id: "filter without a column",
			call: func(ctx context.Context, c *Client) error {
				return c.Get(ctx, ConsoleProfile, "projects", []Filter{Eq("", "1")}, nil)
			},
			wantErr: "filter 0 of projects has an empty column",
		},
		{
			id: "delete filtered on a reserved parameter",
			call: func(ctx context.Context, c *Client) error {
				return c.Delete(ctx, ConsoleProfile, "projects", []Filter{Eq("columns", "x")}, nil)
			},
			wantErr: `filter 0 of projects: PostgREST reads "columns" as a parameter, not as a column`,
		},
		{
			id: "patch filtered on a reserved parameter after a column",
			call: func(ctx context.Context, c *Client) error {
				return c.Patch(ctx, ConsoleProfile, "projects", []Filter{Eq("id", "1"), IsNull("or")}, map[string]int{}, nil)
			},
			wantErr: `filter 1 of projects: PostgREST reads "or" as a parameter, not as a column`,
		},
		{
			id: "get filtered on a reserved parameter",
			call: func(ctx context.Context, c *Client) error {
				return c.Get(ctx, ConsoleProfile, "projects", []Filter{Eq("select", "*")}, nil)
			},
			wantErr: `filter 0 of projects: PostgREST reads "select" as a parameter, not as a column`,
		},
		{
			id:      "token error",
			tokens:  staticToken{err: errors.New("token endpoint down")},
			call:    func(ctx context.Context, c *Client) error { return c.Get(ctx, ConsoleProfile, "projects", nil, nil) },
			wantErr: "get the token: token endpoint down",
		},
		{
			id:      "empty token",
			tokens:  staticToken{},
			call:    func(ctx context.Context, c *Client) error { return c.Get(ctx, ConsoleProfile, "projects", nil, nil) },
			wantErr: "the token source returned an empty token",
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			var calls atomic.Int32

			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				calls.Add(1)
				w.WriteHeader(http.StatusOK)
			}))
			t.Cleanup(server.Close)

			tokens := tt.tokens
			if tokens == nil {
				tokens = staticToken{token: "jwt-123"}
			}

			c, err := New(server.URL, tokens, server.Client())
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			err = tt.call(context.Background(), c)
			if err == nil || err.Error() != tt.wantErr {
				t.Fatalf("error = %v, want %q", err, tt.wantErr)
			}

			if calls.Load() != 0 {
				t.Errorf("server got %d requests, want 0", calls.Load())
			}
		})
	}
}

func TestNew(t *testing.T) {
	tests := []struct {
		id       string
		endpoint string
		tokens   TokenSource
		wantErr  string
	}{
		{id: "https with a path", endpoint: "https://data-proxy.example.com/api/", tokens: staticToken{}},
		{id: "other scheme", endpoint: "ftp://data-proxy.example.com", tokens: staticToken{}, wantErr: `endpoint "ftp://data-proxy.example.com": the scheme must be http or https`},
		{id: "no host", endpoint: "https://", tokens: staticToken{}, wantErr: `endpoint "https://": the host is empty`},
		{id: "query", endpoint: "https://data-proxy.example.com?a=1", tokens: staticToken{}, wantErr: `endpoint "https://data-proxy.example.com?a=1": remove the query and the fragment`},
		{id: "no token source", endpoint: "https://data-proxy.example.com", wantErr: "the token source is nil"},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			c, err := New(tt.endpoint, tt.tokens, nil)

			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("New: %v", err)
				}

				if c.http.Timeout != DefaultTimeout {
					t.Errorf("timeout = %s, want %s", c.http.Timeout, DefaultTimeout)
				}

				return
			}

			if err == nil || err.Error() != tt.wantErr {
				t.Fatalf("error = %v, want %q", err, tt.wantErr)
			}
		})
	}
}

func TestErrorResponses(t *testing.T) {
	tests := []struct {
		id       string
		status   int
		response string
		kind     error
		want     Error
		text     string
	}{
		{
			id:       "unique violation is a conflict",
			status:   http.StatusConflict,
			response: `{"code":"23505","message":"duplicate key value violates unique constraint \"projects_name_key\"","details":"Key (name)=(a) already exists.","hint":null}`,
			kind:     ErrConflict,
			want:     Error{Status: 409, Code: "23505", Message: `duplicate key value violates unique constraint "projects_name_key"`, Details: "Key (name)=(a) already exists."},
			text:     `postgrest: HTTP 409 23505: duplicate key value violates unique constraint "projects_name_key" (details: Key (name)=(a) already exists.)`,
		},
		{
			id:       "foreign key violation is a conflict",
			status:   http.StatusConflict,
			response: `{"code":"23503","message":"insert or update on table \"tables\" violates foreign key constraint","details":null,"hint":null}`,
			kind:     ErrConflict,
			want:     Error{Status: 409, Code: "23503", Message: `insert or update on table "tables" violates foreign key constraint`},
			text:     `postgrest: HTTP 409 23503: insert or update on table "tables" violates foreign key constraint`,
		},
		{
			id:       "check violation is invalid",
			status:   http.StatusBadRequest,
			response: `{"code":"23514","message":"new row violates check constraint \"cache_ttl_positive\"","details":null,"hint":"Use a positive cache_ttl."}`,
			kind:     ErrInvalid,
			want:     Error{Status: 400, Code: "23514", Message: `new row violates check constraint "cache_ttl_positive"`, Hint: "Use a positive cache_ttl."},
			text:     `postgrest: HTTP 400 23514: new row violates check constraint "cache_ttl_positive" (hint: Use a positive cache_ttl.)`,
		},
		{
			id:       "row-level security is forbidden",
			status:   http.StatusForbidden,
			response: `{"code":"42501","message":"new row violates row-level security policy for table \"projects\"","details":null,"hint":null}`,
			kind:     ErrForbidden,
			want:     Error{Status: 403, Code: "42501", Message: `new row violates row-level security policy for table "projects"`},
			text:     `postgrest: HTTP 403 42501: new row violates row-level security policy for table "projects"`,
		},
		{
			id:       "denied permission with HTTP 401 is forbidden",
			status:   http.StatusUnauthorized,
			response: `{"code":"42501","message":"permission denied for table projects","details":null,"hint":null}`,
			kind:     ErrForbidden,
			want:     Error{Status: 401, Code: "42501", Message: "permission denied for table projects"},
			text:     `postgrest: HTTP 401 42501: permission denied for table projects`,
		},
		{
			id:       "HTTP 403 without a SQLSTATE is forbidden",
			status:   http.StatusForbidden,
			response: `forbidden`,
			kind:     ErrForbidden,
			want:     Error{Status: 403, Message: "forbidden"},
			text:     `postgrest: HTTP 403: forbidden`,
		},
		{
			id:       "expired JWT is unauthorized",
			status:   http.StatusUnauthorized,
			response: `{"code":"PGRST303","message":"JWT expired","details":null,"hint":null}`,
			kind:     ErrUnauthorized,
			want:     Error{Status: 401, Code: "PGRST303", Message: "JWT expired"},
			text:     `postgrest: HTTP 401 PGRST303: JWT expired`,
		},
		{
			id:       "unknown table is not found",
			status:   http.StatusNotFound,
			response: `{"code":"PGRST205","message":"Could not find the table 'console.nope' in the schema cache","details":null,"hint":"Perhaps you meant the table 'console.projects'"}`,
			kind:     ErrNotFound,
			want:     Error{Status: 404, Code: "PGRST205", Message: "Could not find the table 'console.nope' in the schema cache", Hint: "Perhaps you meant the table 'console.projects'"},
			text:     `postgrest: HTTP 404 PGRST205: Could not find the table 'console.nope' in the schema cache (hint: Perhaps you meant the table 'console.projects')`,
		},
		{
			id:       "other HTTP 409 is a conflict",
			status:   http.StatusConflict,
			response: `{"code":"PGRST999","message":"conflict","details":null,"hint":null}`,
			kind:     ErrConflict,
			want:     Error{Status: 409, Code: "PGRST999", Message: "conflict"},
			text:     `postgrest: HTTP 409 PGRST999: conflict`,
		},
		{
			id:       "other HTTP 400 is invalid",
			status:   http.StatusBadRequest,
			response: `{"code":"22P02","message":"invalid input syntax for type integer: \"x\"","details":null,"hint":null}`,
			kind:     ErrInvalid,
			want:     Error{Status: 400, Code: "22P02", Message: `invalid input syntax for type integer: "x"`},
			text:     `postgrest: HTTP 400 22P02: invalid input syntax for type integer: "x"`,
		},
		{
			id:       "server error has no kind",
			status:   http.StatusBadGateway,
			response: "<html>bad gateway</html>\n",
			want:     Error{Status: 502, Message: "<html>bad gateway</html>"},
			text:     `postgrest: HTTP 502: <html>bad gateway</html>`,
		},
	}

	kinds := []error{ErrConflict, ErrInvalid, ErrForbidden, ErrUnauthorized, ErrNotFound, ErrNoRow}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			c, _ := newServer(t, tt.status, tt.response)

			err := c.Get(context.Background(), ConsoleProfile, "projects", nil, nil)

			var got *Error
			if !errors.As(err, &got) {
				t.Fatalf("error = %v, want *Error", err)
			}

			if got.Status != tt.want.Status || got.Code != tt.want.Code || got.Message != tt.want.Message ||
				got.Details != tt.want.Details || got.Hint != tt.want.Hint {
				t.Errorf("error = %+v, want %+v", *got, tt.want)
			}

			if got.Error() != tt.text {
				t.Errorf("text = %q, want %q", got.Error(), tt.text)
			}

			for _, kind := range kinds {
				if errors.Is(err, kind) != (kind == tt.kind) {
					t.Errorf("errors.Is(err, %v) = %t, want %t", kind, !(kind == tt.kind), kind == tt.kind)
				}
			}
		})
	}
}

func TestRetryReadsTheReplicaAgain(t *testing.T) {
	var calls atomic.Int32

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		if calls.Add(1) < 3 {
			_, _ = w.Write([]byte(`[]`))

			return
		}

		_, _ = w.Write([]byte(`[{"id":9,"name":"new"}]`))
	}))
	t.Cleanup(server.Close)

	c, err := New(server.URL, staticToken{token: "jwt-123"}, server.Client())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	var got row

	backoff := Backoff{Initial: time.Millisecond, Max: 2 * time.Millisecond, Timeout: 5 * time.Second}

	err = RetryNoRow(context.Background(), backoff, func(ctx context.Context) error {
		return c.GetOne(ctx, ConsoleProfile, "projects", []Filter{Eq("id", "9")}, &got)
	})
	if err != nil {
		t.Fatalf("RetryNoRow: %v", err)
	}

	if got != (row{ID: 9, Name: "new"}) {
		t.Errorf("row = %v, want {9 new}", got)
	}

	if calls.Load() != 3 {
		t.Errorf("server got %d requests, want 3", calls.Load())
	}
}
