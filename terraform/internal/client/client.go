// Package client calls the PostgREST API of Data Proxy. It doesn't depend on the Terraform Plugin Framework,
// so the resources and the data sources of the provider share it, and its tests need no Terraform.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ConsoleProfile is the schema of the configuration tables. An access policy uses the schema of its data instead.
const ConsoleProfile = "console"

// DefaultTimeout limits one request when New gets no HTTP client.
const DefaultTimeout = 30 * time.Second

// maxErrorBody limits how much of an error response the client reads.
const maxErrorBody = 1 << 20

// reservedParameters are the query parameters of PostgREST. A filter on a column with one of these names would
// not filter the rows, so a Patch or a Delete would touch the whole table.
var reservedParameters = map[string]struct{}{
	"select": {}, "columns": {}, "order": {}, "limit": {}, "offset": {}, "on_conflict": {}, "and": {}, "or": {}, "not": {},
}

// TokenSource gives the JWT that the client sends in the Authorization header of each request.
type TokenSource interface {
	Token(ctx context.Context) (string, error)
}

// Filter is one condition of the query string of PostgREST, such as id=eq.42.
type Filter struct {
	column   string
	operator string
	value    string
}

// Eq matches the rows whose column equals the value.
func Eq(column, value string) Filter {
	return Filter{column: column, operator: "eq", value: value}
}

// IsNull matches the rows whose column is NULL.
func IsNull(column string) Filter {
	return Filter{column: column, operator: "is", value: "null"}
}

// Client sends requests to one PostgREST endpoint.
type Client struct {
	endpoint *url.URL
	tokens   TokenSource
	http     *http.Client
}

// New returns a client for the endpoint, the base address of the PostgREST API.
// With a nil httpClient, it uses a client with DefaultTimeout.
func New(endpoint string, tokens TokenSource, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil {
		return nil, fmt.Errorf("parse the endpoint: %w", err)
	}

	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, fmt.Errorf("endpoint %q: the scheme must be http or https", endpoint)
	}

	if parsed.Host == "" {
		return nil, fmt.Errorf("endpoint %q: the host is empty", endpoint)
	}

	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, fmt.Errorf("endpoint %q: remove the query and the fragment", endpoint)
	}

	if tokens == nil {
		return nil, errors.New("the token source is nil")
	}

	if httpClient == nil {
		httpClient = &http.Client{Timeout: DefaultTimeout}
	}

	parsed.Path = strings.TrimRight(parsed.Path, "/")
	parsed.RawPath = ""

	return &Client{endpoint: parsed, tokens: tokens, http: httpClient}, nil
}

// Get reads the rows of the table that match every filter, and decodes the JSON array into out.
func (c *Client) Get(ctx context.Context, profile, table string, filters []Filter, out any) error {
	return c.do(ctx, request{method: http.MethodGet, profile: profile, path: table, filters: filters}, out)
}

// GetOne reads the one row of the table that matches every filter, and decodes it into out.
// It returns an error that wraps ErrNoRow when no row matches, and an error when more than one row matches.
func (c *Client) GetOne(ctx context.Context, profile, table string, filters []Filter, out any) error {
	var rows []json.RawMessage

	if err := c.Get(ctx, profile, table, filters, &rows); err != nil {
		return err
	}

	switch len(rows) {
	case 0:
		return fmt.Errorf("no row of %s matches the filters: %w", table, ErrNoRow)
	case 1:
		if out == nil {
			return nil
		}

		if err := json.Unmarshal(rows[0], out); err != nil {
			return fmt.Errorf("decode the row of %s: %w", table, err)
		}

		return nil
	default:
		return fmt.Errorf("%d rows of %s match the filters, want one", len(rows), table)
	}
}

// Insert writes the body, one object or an array of objects, to the table, and decodes the written rows into out.
func (c *Client) Insert(ctx context.Context, profile, table string, body, out any) error {
	return c.do(ctx, request{method: http.MethodPost, profile: profile, path: table, body: body, write: true}, out)
}

// Patch changes the rows of the table that match every filter, and decodes the changed rows into out.
// It needs at least one filter, so that it never changes the whole table.
func (c *Client) Patch(ctx context.Context, profile, table string, filters []Filter, body, out any) error {
	if len(filters) == 0 {
		return fmt.Errorf("patch %s: give at least one filter", table)
	}

	return c.do(ctx, request{method: http.MethodPatch, profile: profile, path: table, filters: filters, body: body, write: true}, out)
}

// Delete removes the rows of the table that match every filter, and decodes the removed rows into out.
// It needs at least one filter, so that it never empties the table.
func (c *Client) Delete(ctx context.Context, profile, table string, filters []Filter, out any) error {
	if len(filters) == 0 {
		return fmt.Errorf("delete from %s: give at least one filter", table)
	}

	return c.do(ctx, request{method: http.MethodDelete, profile: profile, path: table, filters: filters, write: true}, out)
}

// RPC calls the function with the arguments, a JSON object, and decodes its result into out.
func (c *Client) RPC(ctx context.Context, profile, function string, args, out any) error {
	if function == "" {
		return errors.New("the function name is empty")
	}

	if args == nil {
		args = map[string]any{}
	}

	return c.do(ctx, request{method: http.MethodPost, profile: profile, path: "rpc/" + url.PathEscape(function), body: args, rpc: true}, out)
}

// GetRPC calls a function that only reads, a STABLE or an IMMUTABLE one, with GET and no arguments, and decodes
// its result into out. PostgREST finds the function in the schema of Accept-Profile.
func (c *Client) GetRPC(ctx context.Context, profile, function string, out any) error {
	if function == "" {
		return errors.New("the function name is empty")
	}

	return c.do(ctx, request{method: http.MethodGet, profile: profile, path: "rpc/" + url.PathEscape(function), rpc: true}, out)
}

// Permissions returns the permissions of the token, the names of rbac.toml such as read_config that the role of
// its JWT has. It calls console.permissions() with GET /rpc/permissions.
func (c *Client) Permissions(ctx context.Context) ([]string, error) {
	var permissions []string

	if err := c.GetRPC(ctx, ConsoleProfile, "permissions", &permissions); err != nil {
		return nil, fmt.Errorf("read the permissions of the token: %w", err)
	}

	if permissions == nil {
		permissions = []string{}
	}

	return permissions, nil
}

// request is one call to PostgREST. path is already escaped, except a table name, which do escapes.
type request struct {
	method  string
	profile string
	path    string
	filters []Filter
	body    any
	write   bool
	rpc     bool
}

func (c *Client) do(ctx context.Context, r request, out any) error {
	if r.profile == "" {
		return errors.New("the profile is empty")
	}

	target, err := c.target(r)
	if err != nil {
		return err
	}

	var payload io.Reader

	if r.body != nil {
		encoded, err := json.Marshal(r.body)
		if err != nil {
			return fmt.Errorf("encode the body of %s %s: %w", r.method, r.path, err)
		}

		payload = bytes.NewReader(encoded)
	}

	req, err := http.NewRequestWithContext(ctx, r.method, target, payload)
	if err != nil {
		return fmt.Errorf("build the request %s %s: %w", r.method, r.path, err)
	}

	token, err := c.tokens.Token(ctx)
	if err != nil {
		return fmt.Errorf("get the token: %w", err)
	}

	if token == "" {
		return errors.New("the token source returned an empty token")
	}

	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")

	switch {
	case r.write:
		req.Header.Set("Content-Profile", r.profile)
		req.Header.Set("Prefer", "return=representation")
	case r.rpc && r.method == http.MethodPost:
		req.Header.Set("Content-Profile", r.profile)
	default:
		req.Header.Set("Accept-Profile", r.profile)
	}

	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("%s %s: %w", r.method, r.path, err)
	}

	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		raw, err := io.ReadAll(io.LimitReader(resp.Body, maxErrorBody))
		if err != nil {
			return fmt.Errorf("%s %s: read the error response: %w", r.method, r.path, err)
		}

		return parseError(resp.StatusCode, raw)
	}

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("%s %s: read the response: %w", r.method, r.path, err)
	}

	if out == nil || len(bytes.TrimSpace(raw)) == 0 {
		return nil
	}

	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("%s %s: decode the response: %w", r.method, r.path, err)
	}

	return nil
}

// target builds the address of the request: the endpoint, the path and the filters in their given order.
func (c *Client) target(r request) (string, error) {
	path := r.path

	if !r.rpc {
		if path == "" {
			return "", errors.New("the table name is empty")
		}

		path = url.PathEscape(path)
	}

	var query strings.Builder

	for i, filter := range r.filters {
		if filter.column == "" {
			return "", fmt.Errorf("filter %d of %s has an empty column", i, r.path)
		}

		if _, ok := reservedParameters[filter.column]; ok {
			return "", fmt.Errorf("filter %d of %s: PostgREST reads %q as a parameter, not as a column", i, r.path, filter.column)
		}

		if i > 0 {
			query.WriteByte('&')
		}

		query.WriteString(escapeQuery(filter.column))
		query.WriteByte('=')
		query.WriteString(filter.operator)
		query.WriteByte('.')
		query.WriteString(escapeQuery(filter.value))
	}

	target := c.endpoint.String() + "/" + path

	if query.Len() > 0 {
		target += "?" + query.String()
	}

	return target, nil
}

// escapeQuery escapes a part of the query string. It writes a space as %20, because a plus sign is literal for
// some servers.
func escapeQuery(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}
