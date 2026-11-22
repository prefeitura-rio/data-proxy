package client

import (
	"context"
	"net/http"
	"time"
)

// This file is part of the package, not of the test package, so it can see the names that the tests of client_test
// need and that the package does not export. It exports nothing to a user of the package.

// MaxBody is the longest successful response that the client reads.
const MaxBody = maxBody

// KindOf is kindOf, for the tests.
func KindOf(status int, code string) error {
	return kindOf(status, code)
}

// ParseError is parseError, for the tests.
func ParseError(status int, raw []byte) *Error {
	return parseError(status, raw)
}

// RetryNoRowWithClock is retryNoRow, which takes the clock and the wait function, for the tests.
func RetryNoRowWithClock(
	ctx context.Context,
	backoff Backoff,
	now func() time.Time,
	wait func(context.Context, time.Duration) error,
	read func(context.Context) error,
) error {
	return retryNoRow(ctx, backoff, now, wait, read)
}

// ErrorOfKind returns an Error that wraps the kind, for a test that needs an error with a kind and no response.
func ErrorOfKind(status int, code string, kind error) *Error {
	return &Error{Status: status, Code: code, kind: kind}
}

// WaitProvisionedWith is waitProvisioned with the wait function that the test gives.
func (c *Client) WaitProvisionedWith(
	ctx context.Context,
	schema string,
	wait func(context.Context, time.Duration) error,
) error {
	return c.waitProvisioned(ctx, schema, wait)
}

// WaitDeprovisionedWith is waitDeprovisioned with the wait function that the test gives.
func (c *Client) WaitDeprovisionedWith(
	ctx context.Context,
	schema string,
	wait func(context.Context, time.Duration) error,
) error {
	return c.waitDeprovisioned(ctx, schema, wait)
}

// HTTPClient returns the HTTP client of the Client.
func (c *Client) HTTPClient() *http.Client { return c.http }

// SetClock sets the clock of the source of tokens.
func (cs *CredentialsSource) SetClock(now func() time.Time) { cs.now = now }

// Expiry returns the time when the token of the source expires.
func (cs *CredentialsSource) Expiry() time.Time { return cs.expiry }

// TokenURL returns the token URL of the source.
func (cs *CredentialsSource) TokenURL() string { return cs.config.TokenURL }
