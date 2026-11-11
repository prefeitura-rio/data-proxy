package client

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
)

// The kinds of an Error, for errors.Is.
var (
	// ErrConflict is a unique or foreign key violation, or an HTTP 409.
	ErrConflict = errors.New("conflict")
	// ErrInvalid is a check violation, or an HTTP 400.
	ErrInvalid = errors.New("invalid")
	// ErrForbidden is a denied permission or row-level security policy, or an HTTP 403.
	ErrForbidden = errors.New("forbidden")
	// ErrUnauthorized is an HTTP 401 without a SQLSTATE, such as a missing or an expired JWT.
	ErrUnauthorized = errors.New("unauthorized")
	// ErrNotFound is an HTTP 404, such as an unknown table.
	ErrNotFound = errors.New("not found")
)

// ErrTruncated is the error of a read that PostgREST cut at its row limit, so the rows are not all the matches.
var ErrTruncated = errors.New("truncated answer")

// ErrTooLarge is the error of a response that is longer than the client reads.
var ErrTooLarge = errors.New("response too large")

// ErrNoRow is the error of GetOne when no row matches. PostgREST answered, so it is not an Error.
var ErrNoRow = errors.New("no row")

// Error is an error response of PostgREST. Code is a SQLSTATE or a PGRST code.
type Error struct {
	Status  int
	Code    string
	Message string
	Details string
	Hint    string
	kind    error
}

// Error describes the HTTP status, the code, the message, the details and the hint of the response.
func (e *Error) Error() string {
	var text strings.Builder

	fmt.Fprintf(&text, "postgrest: HTTP %d", e.Status)

	if e.Code != "" {
		fmt.Fprintf(&text, " %s", e.Code)
	}

	if e.Message != "" {
		fmt.Fprintf(&text, ": %s", e.Message)
	}

	if e.Details != "" {
		fmt.Fprintf(&text, " (details: %s)", e.Details)
	}

	if e.Hint != "" {
		fmt.Fprintf(&text, " (hint: %s)", e.Hint)
	}

	return text.String()
}

// Unwrap returns the kind of the error, so that errors.Is(err, ErrConflict) works.
func (e *Error) Unwrap() error {
	return e.kind
}

// body is the JSON of an error response of PostgREST.
type body struct {
	Code    string  `json:"code"`
	Message string  `json:"message"`
	Details *string `json:"details"`
	Hint    *string `json:"hint"`
}

// parseError builds the Error of a response that is not a success. It reads the JSON body of PostgREST, and it
// keeps the text of any other body as the message.
func parseError(status int, raw []byte) *Error {
	result := &Error{Status: status}

	var parsed body

	if err := json.Unmarshal(raw, &parsed); err == nil && (parsed.Code != "" || parsed.Message != "") {
		result.Code = parsed.Code
		result.Message = parsed.Message

		if parsed.Details != nil {
			result.Details = *parsed.Details
		}

		if parsed.Hint != nil {
			result.Hint = *parsed.Hint
		}
	} else {
		result.Message = strings.TrimSpace(string(raw))
	}

	result.kind = kindOf(status, result.Code)

	return result
}

// kindOf maps the SQLSTATE first, because PostgREST answers 401 to a denied permission when the JWT has no role.
func kindOf(status int, code string) error {
	switch code {
	case "23505", "23503":
		return ErrConflict
	case "23514":
		return ErrInvalid
	case "42501":
		return ErrForbidden
	}

	switch status {
	case http.StatusConflict:
		return ErrConflict
	case http.StatusBadRequest:
		return ErrInvalid
	case http.StatusForbidden:
		return ErrForbidden
	case http.StatusUnauthorized:
		return ErrUnauthorized
	case http.StatusNotFound:
		return ErrNotFound
	}

	return nil
}
