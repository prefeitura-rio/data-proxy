package client

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sync"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/clientcredentials"
)

// RenewBefore is how long before its expiry the token source fetches a new token.
const RenewBefore = 30 * time.Second

// Credentials configures the OAuth 2.0 client credentials grant. Empty Scopes means the default scopes of
// the client.
type Credentials struct {
	TokenURL     string
	ClientID     string
	ClientSecret string
	Scopes       []string
}

// CredentialsSource keeps a token from the client credentials grant and renews it RenewBefore its expiry.
// It is safe for concurrent use: one caller fetches, and the others wait for its token.
type CredentialsSource struct {
	config *clientcredentials.Config
	http   *http.Client
	now    func() time.Time

	mu     sync.Mutex
	token  string
	expiry time.Time
}

var _ TokenSource = (*CredentialsSource)(nil)

// NewCredentialsSource returns a token source. A nil httpClient means a client with DefaultTimeout.
func NewCredentialsSource(credentials Credentials, httpClient *http.Client) (*CredentialsSource, error) {
	parsed, err := url.Parse(credentials.TokenURL)
	if err != nil {
		return nil, fmt.Errorf("parse the token URL: %w", err)
	}

	if err := checkAddress("token URL", credentials.TokenURL, parsed); err != nil {
		return nil, err
	}

	if credentials.ClientID == "" {
		return nil, errors.New("the client ID is empty")
	}

	if credentials.ClientSecret == "" {
		return nil, errors.New("the client secret is empty")
	}

	if httpClient == nil {
		httpClient = &http.Client{Timeout: DefaultTimeout}
	}

	config := &clientcredentials.Config{
		ClientID:     credentials.ClientID,
		ClientSecret: credentials.ClientSecret,
		TokenURL:     credentials.TokenURL,
		Scopes:       append([]string(nil), credentials.Scopes...),
	}

	return &CredentialsSource{config: config, http: httpClient, now: time.Now}, nil
}

// Token returns the kept access token, or fetches a new one when it expires within RenewBefore.
func (s *CredentialsSource) Token(ctx context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.valid() {
		return s.token, nil
	}

	token, err := s.config.Token(context.WithValue(ctx, oauth2.HTTPClient, s.http))
	if err != nil {
		return "", fmt.Errorf("fetch a token from %s: %w", s.config.TokenURL, err)
	}

	s.token = token.AccessToken
	s.expiry = token.Expiry

	return s.token, nil
}

// valid treats a token without an expiry as one that never expires.
func (s *CredentialsSource) valid() bool {
	if s.token == "" {
		return false
	}

	if s.expiry.IsZero() {
		return true
	}

	return s.now().Add(RenewBefore).Before(s.expiry)
}
