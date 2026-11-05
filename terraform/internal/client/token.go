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

// RenewBefore is how long before its expiry the token source fetches a new token, so that a request never
// reaches the API with a token that expires on the way.
const RenewBefore = 30 * time.Second

// ClientCredentials configures the OAuth 2.0 client credentials grant against the identity provider.
type ClientCredentials struct {
	// TokenURL is the address of the token endpoint, such as the one of a Keycloak realm.
	TokenURL string
	// ClientID is the ID of the confidential client.
	ClientID string
	// ClientSecret is the secret of the client.
	ClientSecret string
	// Scopes are the scopes to request with the token. Empty means the default scopes of the client.
	Scopes []string
}

// ClientCredentialsSource fetches a token with the client credentials grant, keeps it, and fetches a new one
// RenewBefore its expiry. The identity provider gives tokens that last 5 minutes, and a Terraform run can take
// longer. It's safe for concurrent use: one caller fetches, and the others wait for its token.
type ClientCredentialsSource struct {
	config *clientcredentials.Config
	http   *http.Client
	now    func() time.Time

	mu     sync.Mutex
	token  string
	expiry time.Time
}

var _ TokenSource = (*ClientCredentialsSource)(nil)

// NewClientCredentialsSource returns a token source for the credentials.
// With a nil httpClient, it uses a client with DefaultTimeout.
func NewClientCredentialsSource(credentials ClientCredentials, httpClient *http.Client) (*ClientCredentialsSource, error) {
	parsed, err := url.Parse(credentials.TokenURL)
	if err != nil {
		return nil, fmt.Errorf("parse the token URL: %w", err)
	}

	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, fmt.Errorf("token URL %q: the scheme must be http or https", credentials.TokenURL)
	}

	if parsed.Host == "" {
		return nil, fmt.Errorf("token URL %q: the host is empty", credentials.TokenURL)
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

	return &ClientCredentialsSource{config: config, http: httpClient, now: time.Now}, nil
}

// Token returns the access token that it keeps, or fetches a new one when the kept token expires within
// RenewBefore. The context limits the fetch.
func (s *ClientCredentialsSource) Token(ctx context.Context) (string, error) {
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

// valid reports whether the kept token can still be sent. A token without an expiry never expires.
func (s *ClientCredentialsSource) valid() bool {
	if s.token == "" {
		return false
	}

	if s.expiry.IsZero() {
		return true
	}

	return s.now().Add(RenewBefore).Before(s.expiry)
}
