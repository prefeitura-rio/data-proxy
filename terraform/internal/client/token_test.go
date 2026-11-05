package client

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"golang.org/x/oauth2"
)

// The credentials that the fake token endpoint expects.
const (
	testClientID     = "terraform"
	testClientSecret = "secret-123"
)

// tokenServer is a fake token endpoint. It answers each client credentials request with token-<n>.
type tokenServer struct {
	t         *testing.T
	expiresIn int
	status    int
	response  string

	mu       sync.Mutex
	requests []tokenRequest
}

// tokenRequest is what the fake token endpoint saw of one request.
type tokenRequest struct {
	grantType string
	clientID  string
	secret    string
	scope     string
}

// ServeHTTP records the token request and answers it with token-<n>, or with the status that the test set.
func (s *tokenServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		s.t.Errorf("parse the token request: %v", err)
	}

	clientID, secret, ok := r.BasicAuth()
	if !ok {
		clientID, secret = r.PostForm.Get("client_id"), r.PostForm.Get("client_secret")
	}

	s.mu.Lock()
	s.requests = append(s.requests, tokenRequest{
		grantType: r.PostForm.Get("grant_type"),
		clientID:  clientID,
		secret:    secret,
		scope:     r.PostForm.Get("scope"),
	})
	count := len(s.requests)
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")

	if s.status != 0 {
		w.WriteHeader(s.status)

		if _, err := w.Write([]byte(s.response)); err != nil {
			s.t.Errorf("write the token response: %v", err)
		}

		return
	}

	body := map[string]any{"access_token": fmt.Sprintf("token-%d", count), "token_type": "Bearer"}
	if s.expiresIn > 0 {
		body["expires_in"] = s.expiresIn
	}

	if err := json.NewEncoder(w).Encode(body); err != nil {
		s.t.Errorf("write the token response: %v", err)
	}
}

// count returns the number of token requests that the server saw.
func (s *tokenServer) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()

	return len(s.requests)
}

// newTokenSource starts the fake token endpoint and returns a source for it, with a clock that the test moves.
// The clock starts at the real time, because oauth2 sets the expiry of a token from the real time.
func newTokenSource(t *testing.T, server *tokenServer, scopes []string) (*CredentialsSource, *time.Time) {
	t.Helper()

	server.t = t
	httpServer := httptest.NewServer(server)
	t.Cleanup(httpServer.Close)

	source, err := NewCredentialsSource(Credentials{
		TokenURL:     httpServer.URL + "/realms/test/protocol/openid-connect/token",
		ClientID:     testClientID,
		ClientSecret: testClientSecret,
		Scopes:       scopes,
	}, httpServer.Client())
	if err != nil {
		t.Fatalf("NewCredentialsSource: %v", err)
	}

	clock := time.Now()
	source.now = func() time.Time { return clock }

	return source, &clock
}

func TestNewCredentialsSourceRejects(t *testing.T) {
	valid := Credentials{TokenURL: "https://id.example.com/token", ClientID: "terraform", ClientSecret: "s"}

	tests := []struct {
		name   string
		change func(*Credentials)
		want   string
	}{
		{
			name:   "token URL without a scheme",
			change: func(c *Credentials) { c.TokenURL = "id.example.com/token" },
			want:   `token URL "id.example.com/token": the scheme must be http or https`,
		},
		{
			name:   "token URL with another scheme",
			change: func(c *Credentials) { c.TokenURL = "ftp://id.example.com/token" },
			want:   `token URL "ftp://id.example.com/token": the scheme must be http or https`,
		},
		{
			name:   "token URL without a host",
			change: func(c *Credentials) { c.TokenURL = "https:///token" },
			want:   `token URL "https:///token": the host is empty`,
		},
		{
			name:   "token URL that doesn't parse",
			change: func(c *Credentials) { c.TokenURL = "https://id.example.com/%zz" },
			want:   `parse the token URL: parse "https://id.example.com/%zz": invalid URL escape "%zz"`,
		},
		{
			name:   "empty client ID",
			change: func(c *Credentials) { c.ClientID = "" },
			want:   "the client ID is empty",
		},
		{
			name:   "empty client secret",
			change: func(c *Credentials) { c.ClientSecret = "" },
			want:   "the client secret is empty",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			credentials := valid
			tt.change(&credentials)

			source, err := NewCredentialsSource(credentials, nil)
			if err == nil {
				t.Fatalf("NewCredentialsSource = %v, want an error", source)
			}

			if err.Error() != tt.want {
				t.Errorf("error = %q, want %q", err.Error(), tt.want)
			}
		})
	}
}

func TestCredentialsSourceRenewsTheToken(t *testing.T) {
	server := &tokenServer{expiresIn: 300}
	source, clock := newTokenSource(t, server, []string{"openid", "data-proxy"})

	before := time.Now()

	first, err := source.Token(context.Background())
	if err != nil {
		t.Fatalf("Token: %v", err)
	}

	after := time.Now()

	if first != "token-1" {
		t.Errorf("token = %q, want %q", first, "token-1")
	}

	lifetime := 300 * time.Second
	if source.expiry.Before(before.Add(lifetime)) || source.expiry.After(after.Add(lifetime)) {
		t.Errorf("expiry = %s, want 5 minutes after the request, between %s and %s",
			source.expiry, before.Add(lifetime), after.Add(lifetime))
	}

	steps := []struct {
		name     string
		at       func(expiry time.Time) time.Time
		want     string
		requests int
	}{
		{
			name:     "a call 31 s before the expiry reuses the token",
			at:       func(expiry time.Time) time.Time { return expiry.Add(-31 * time.Second) },
			want:     "token-1",
			requests: 1,
		},
		{
			name:     "a call 29 s before the expiry renews it",
			at:       func(expiry time.Time) time.Time { return expiry.Add(-29 * time.Second) },
			want:     "token-2",
			requests: 2,
		},
		{
			name:     "a call 31 s before the expiry of the new token reuses it",
			at:       func(expiry time.Time) time.Time { return expiry.Add(-31 * time.Second) },
			want:     "token-2",
			requests: 2,
		},
		{
			name:     "a call after the expiry renews it",
			at:       func(expiry time.Time) time.Time { return expiry.Add(time.Minute) },
			want:     "token-3",
			requests: 3,
		},
	}

	for _, step := range steps {
		*clock = step.at(source.expiry)

		got, err := source.Token(context.Background())
		if err != nil {
			t.Fatalf("%s: Token: %v", step.name, err)
		}

		if got != step.want {
			t.Errorf("%s: token = %q, want %q", step.name, got, step.want)
		}

		if server.count() != step.requests {
			t.Errorf("%s: token requests = %d, want %d", step.name, server.count(), step.requests)
		}
	}

	want := tokenRequest{grantType: "client_credentials", clientID: testClientID, secret: testClientSecret, scope: "openid data-proxy"}

	for i, request := range server.requests {
		if request != want {
			t.Errorf("token request %d = %+v, want %+v", i, request, want)
		}
	}
}

func TestCredentialsSourceKeepsATokenWithoutExpiry(t *testing.T) {
	server := &tokenServer{}
	source, clock := newTokenSource(t, server, nil)

	for _, at := range []time.Duration{0, 24 * time.Hour} {
		*clock = clock.Add(at)

		got, err := source.Token(context.Background())
		if err != nil {
			t.Fatalf("Token: %v", err)
		}

		if got != "token-1" {
			t.Errorf("token = %q, want %q", got, "token-1")
		}
	}

	if server.count() != 1 {
		t.Errorf("token requests = %d, want 1", server.count())
	}

	if server.requests[0].scope != "" {
		t.Errorf("scope = %q, want none", server.requests[0].scope)
	}
}

func TestCredentialsSourceFetchesOnceForConcurrentCalls(t *testing.T) {
	server := &tokenServer{expiresIn: 300}
	source, _ := newTokenSource(t, server, nil)

	const callers = 8

	var wg sync.WaitGroup

	tokens := make([]string, callers)
	errs := make([]error, callers)

	for i := range callers {
		wg.Go(func() {
			tokens[i], errs[i] = source.Token(context.Background())
		})
	}

	wg.Wait()

	for i := range callers {
		if errs[i] != nil {
			t.Errorf("caller %d: Token: %v", i, errs[i])
		}

		if tokens[i] != "token-1" {
			t.Errorf("caller %d: token = %q, want %q", i, tokens[i], "token-1")
		}
	}

	if server.count() != 1 {
		t.Errorf("token requests = %d, want 1", server.count())
	}
}

func TestCredentialsSourceErrors(t *testing.T) {
	tests := []struct {
		name   string
		server *tokenServer
		ctx    func() context.Context
		check  func(*testing.T, error)
	}{
		{
			name: "the identity provider refuses the client",
			server: &tokenServer{
				status:   http.StatusUnauthorized,
				response: `{"error":"unauthorized_client","error_description":"Invalid client or Invalid client credentials"}`,
			},
			ctx: context.Background,
			check: func(t *testing.T, err error) {
				var retrieve *oauth2.RetrieveError
				if !errors.As(err, &retrieve) {
					t.Fatalf("error = %v, want an *oauth2.RetrieveError", err)
				}

				if retrieve.ErrorCode != "unauthorized_client" {
					t.Errorf("error code = %q, want %q", retrieve.ErrorCode, "unauthorized_client")
				}
			},
		},
		{
			name:   "the context ends before the request",
			server: &tokenServer{expiresIn: 300},
			ctx: func() context.Context {
				ctx, cancel := context.WithCancel(context.Background())
				cancel()

				return ctx
			},
			check: func(t *testing.T, err error) {
				if !errors.Is(err, context.Canceled) {
					t.Errorf("error = %v, want context.Canceled", err)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			source, _ := newTokenSource(t, tt.server, nil)

			token, err := source.Token(tt.ctx())
			if err == nil {
				t.Fatalf("Token = %q, want an error", token)
			}

			if !strings.HasPrefix(err.Error(), "fetch a token from "+source.config.TokenURL+": ") {
				t.Errorf("error = %q, want the prefix with the token URL", err.Error())
			}

			if strings.Contains(err.Error(), testClientSecret) {
				t.Errorf("error = %q, want no client secret in it", err.Error())
			}

			tt.check(t, err)
		})
	}
}

func TestCredentialsSourceRetriesAfterAFailure(t *testing.T) {
	server := &tokenServer{expiresIn: 300}
	source, _ := newTokenSource(t, server, nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := source.Token(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("Token with an ended context: error = %v, want context.Canceled", err)
	}

	got, err := source.Token(context.Background())
	if err != nil {
		t.Fatalf("Token: %v", err)
	}

	if got != "token-1" {
		t.Errorf("token = %q, want %q", got, "token-1")
	}
}
