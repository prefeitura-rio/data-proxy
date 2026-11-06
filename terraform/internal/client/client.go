// Package client calls the PostgREST API of Data Proxy.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// ConsoleProfile is the schema of the configuration tables.
const ConsoleProfile = "console"

// DefaultTimeout limits one request when New gets no HTTP client.
const DefaultTimeout = 30 * time.Second

// maxErrorBody limits how much of an error response the client reads.
const maxErrorBody = 1 << 20

// reservedParameters are the query parameters of PostgREST. A filter on a column with one of these names would not
// filter the rows, so a Patch or a Delete would touch the whole table.
var reservedParameters = map[string]struct{}{
	"select": {}, "columns": {}, "order": {}, "limit": {}, "offset": {}, "on_conflict": {}, "and": {}, "or": {}, "not": {},
}

// TokenSource gives the JWT that the client sends with each request.
type TokenSource interface {
	Token(ctx context.Context) (string, error)
}

// Filter is one condition of the query string, such as id=eq.42.
type Filter struct {
	column   string
	operator string
	value    string
	// raw marks a query parameter of PostgREST, such as order, which has no operator.
	raw bool
}

// Eq matches the rows whose column equals the value.
func Eq(column, value string) Filter {
	return Filter{column: column, operator: "eq", value: value}
}

// IsNull matches the rows whose column is NULL.
func IsNull(column string) Filter {
	return Filter{column: column, operator: "is", value: "null"}
}

// OrderBy sorts the rows by the column, ascending or descending.
func OrderBy(column string, descending bool) Filter {
	direction := "asc"
	if descending {
		direction = "desc"
	}

	return Filter{column: "order", value: column + "." + direction, raw: true}
}

// Limit returns at most n rows.
func Limit(n int) Filter {
	return Filter{column: "limit", value: strconv.Itoa(n), raw: true}
}

// Client sends requests to one PostgREST endpoint.
type Client struct {
	endpoint *url.URL
	tokens   TokenSource
	http     *http.Client
}

// New returns a client for the base address of the API. A nil httpClient means a client with DefaultTimeout.
func New(endpoint string, tokens TokenSource, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil {
		return nil, fmt.Errorf("parse the endpoint: %w", err)
	}

	if err := checkAddress("endpoint", endpoint, parsed); err != nil {
		return nil, err
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

// checkAddress refuses an address that the client can't call safely: another scheme than http or https, no host, or
// http to a host that is not a loopback one, because the token or the secret would cross the network in plain text.
func checkAddress(what, raw string, parsed *url.URL) error {
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("%s %q: the scheme must be http or https", what, raw)
	}

	if parsed.Host == "" {
		return fmt.Errorf("%s %q: the host is empty", what, raw)
	}

	if parsed.Scheme == "http" && !isLoopback(parsed.Hostname()) {
		return fmt.Errorf("%s %q: use https, or http for a loopback host", what, raw)
	}

	return nil
}

// isLoopback reports whether the host is localhost or a loopback IP address.
func isLoopback(host string) bool {
	if host == "localhost" {
		return true
	}

	ip := net.ParseIP(host)

	return ip != nil && ip.IsLoopback()
}

// Get decodes the rows of the table that match every filter into out.
func (c *Client) Get(ctx context.Context, profile, table string, filters []Filter, out any) error {
	return c.do(ctx, request{method: http.MethodGet, profile: profile, path: table, filters: filters}, out)
}

// GetOne decodes the one row that matches every filter into out.
// It wraps ErrNoRow when no row matches, and it fails when more than one row matches.
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

// Insert writes the body, an object or an array of objects, and decodes the written rows into out.
func (c *Client) Insert(ctx context.Context, profile, table string, body, out any) error {
	return c.do(ctx, request{method: http.MethodPost, profile: profile, path: table, body: body, write: true}, out)
}

// Patch changes the rows that match every filter and decodes the changed rows into out. It needs a filter.
func (c *Client) Patch(ctx context.Context, profile, table string, filters []Filter, body, out any) error {
	if len(filters) == 0 {
		return fmt.Errorf("patch %s: give at least one filter", table)
	}

	return c.do(ctx, request{method: http.MethodPatch, profile: profile, path: table, filters: filters, body: body, write: true}, out)
}

// Delete removes the rows that match every filter and decodes the removed rows into out. It needs a filter.
func (c *Client) Delete(ctx context.Context, profile, table string, filters []Filter, out any) error {
	if len(filters) == 0 {
		return fmt.Errorf("delete from %s: give at least one filter", table)
	}

	return c.do(ctx, request{method: http.MethodDelete, profile: profile, path: table, filters: filters, write: true}, out)
}

// RPC calls the function with the arguments, a JSON object, and decodes the result into out.
func (c *Client) RPC(ctx context.Context, profile, function string, args, out any) error {
	if function == "" {
		return errors.New("the function name is empty")
	}

	if args == nil {
		args = map[string]any{}
	}

	return c.do(ctx, request{method: http.MethodPost, profile: profile, path: "rpc/" + url.PathEscape(function), body: args, rpc: true}, out)
}

// GetRPC calls a STABLE or IMMUTABLE function with GET and decodes the result into out.
func (c *Client) GetRPC(ctx context.Context, profile, function string, out any) error {
	if function == "" {
		return errors.New("the function name is empty")
	}

	return c.do(ctx, request{method: http.MethodGet, profile: profile, path: "rpc/" + url.PathEscape(function), rpc: true}, out)
}

// Permissions returns the names of rbac.toml, such as read_config, that the role of the token has.
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

// request is one call to PostgREST. Its path is escaped, except a table name, which do escapes.
type request struct {
	method  string
	profile string
	path    string
	filters []Filter
	body    any
	write   bool
	rpc     bool
}

// do sends one request and decodes the JSON answer into out. It returns an *Error for a status outside 2xx, and it
// ignores an empty answer.
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

	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		raw, err := io.ReadAll(io.LimitReader(resp.Body, maxErrorBody))
		if err != nil {
			return fmt.Errorf("%s %s: read the error response: %w", r.method, r.path, err)
		}

		return parseError(resp.StatusCode, raw)
	}

	if resp.StatusCode == http.StatusPartialContent {
		return fmt.Errorf("%s %s: %w: PostgREST sent the rows %s, so raise postgrest.maxRows or narrow the filter",
			r.method, r.path, ErrTruncated, resp.Header.Get("Content-Range"))
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

// target builds the address of a request from the endpoint, the path and the filters, in the order given.
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

		if _, ok := reservedParameters[filter.column]; ok && !filter.raw {
			return "", fmt.Errorf("filter %d of %s: PostgREST reads %q as a parameter, not as a column", i, r.path, filter.column)
		}

		if i > 0 {
			query.WriteByte('&')
		}

		query.WriteString(escapeQuery(filter.column))
		query.WriteByte('=')

		if !filter.raw {
			query.WriteString(filter.operator)
			query.WriteByte('.')
		}

		query.WriteString(escapeQuery(filter.value))
	}

	target := c.endpoint.String() + "/" + path

	if query.Len() > 0 {
		target += "?" + query.String()
	}

	return target, nil
}

// escapeQuery writes a space as %20, because some servers read a plus sign literally.
func escapeQuery(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}
